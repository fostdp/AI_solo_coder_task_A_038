from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, and_
from typing import Optional
from datetime import datetime, timedelta
from uuid import UUID
from app.core.database import get_db
from app.schemas.telemetry import AlarmData, AlarmAcknowledge
from app.services.alarm import AlarmDetector, AlarmThresholds
from app.services.mqtt import MQTTService, MQTTConfig
from app.core.config import settings
import asyncio

router = APIRouter(prefix="/api/alarm", tags=["alarm"])

alarm_detector = AlarmDetector(
    AlarmThresholds(
        temp_diff=settings.TEMP_DIFF_THRESHOLD,
        vacuum_min=settings.VACUUM_MIN_THRESHOLD,
        vacuum_max=settings.VACUUM_MAX_THRESHOLD,
        cold_trap_max=settings.COLD_TRAP_MAX_THRESHOLD,
        moisture_max=settings.MOISTURE_MAX_THRESHOLD,
        reconstitution_max=settings.RECONSTITUTION_MAX_THRESHOLD
    )
)

mqtt_service = MQTTService(
    MQTTConfig(
        broker=settings.MQTT_BROKER,
        port=settings.MQTT_PORT,
        topic=settings.MQTT_TOPIC,
        username=settings.MQTT_USERNAME,
        password=settings.MQTT_PASSWORD
    )
)


@router.get("/current")
async def get_current_alarms():
    alarms = alarm_detector.get_active_alarms()
    return {
        "count": len(alarms),
        "alarms": [
            {
                "id": str(a.id),
                "timestamp": a.timestamp.isoformat(),
                "device_id": a.device_id,
                "shelf_id": a.shelf_id,
                "alarm_type": a.alarm_type,
                "severity": a.severity,
                "message": a.message,
                "acknowledged": a.acknowledged
            }
            for a in alarms
        ]
    }


@router.get("/history")
async def get_alarm_history(
    device_id: Optional[int] = None,
    alarm_type: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    try:
        if not start_time:
            start_time = datetime.now() - timedelta(days=7)
        if not end_time:
            end_time = datetime.now()
        
        query = text(f"""
            SELECT * FROM alarms
            WHERE timestamp >= :start_time AND timestamp <= :end_time
            {f"AND device_id = {device_id}" if device_id else ""}
            {f"AND alarm_type = '{alarm_type}'" if alarm_type else ""}
            ORDER BY timestamp DESC
            LIMIT :limit
        """)
        
        result = await db.execute(query, {
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit
        })
        rows = result.all()
        
        alarms = []
        for row in rows:
            alarms.append({
                "id": str(row.id),
                "timestamp": row.timestamp.isoformat(),
                "device_id": row.device_id,
                "shelf_id": row.shelf_id,
                "alarm_type": row.alarm_type,
                "severity": row.severity,
                "message": row.message,
                "acknowledged": row.acknowledged,
                "acknowledged_by": row.acknowledged_by,
                "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None
            })
        
        return {"count": len(alarms), "alarms": alarms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/acknowledge")
async def acknowledge_alarm(ack: AlarmAcknowledge, db: AsyncSession = Depends(get_db)):
    try:
        success = alarm_detector.acknowledge_alarm(ack.alarm_id, ack.acknowledged_by)
        
        if success:
            update_sql = text("""
                UPDATE alarms
                SET acknowledged = true,
                    acknowledged_by = :acknowledged_by,
                    acknowledged_at = :acknowledged_at
                WHERE id = :alarm_id
            """)
            
            await db.execute(update_sql, {
                "alarm_id": ack.alarm_id,
                "acknowledged_by": ack.acknowledged_by,
                "acknowledged_at": datetime.now()
            })
            await db.commit()
            
            return {"status": "success", "message": "Alarm acknowledged"}
        else:
            raise HTTPException(status_code=404, detail="Alarm not found")
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


async def process_new_alarms(alarms, db: AsyncSession):
    for alarm in alarms:
        try:
            insert_sql = text("""
                INSERT INTO alarms (
                    id, timestamp, device_id, shelf_id, alarm_type, severity, message
                ) VALUES (
                    :id, :timestamp, :device_id, :shelf_id, :alarm_type, :severity, :message
                )
            """)
            
            await db.execute(insert_sql, {
                "id": alarm.id,
                "timestamp": alarm.timestamp,
                "device_id": alarm.device_id,
                "shelf_id": alarm.shelf_id,
                "alarm_type": alarm.alarm_type,
                "severity": alarm.severity,
                "message": alarm.message
            })
            
            await mqtt_service.send_alarm(alarm)
            
        except Exception as e:
            print(f"Error processing alarm: {e}")
    
    await db.commit()


@router.post("/check")
async def check_alarms(
    device_id: int,
    shelf_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    try:
        query = text(f"""
            SELECT DISTINCT ON (shelf_id)
                temp_1, temp_2, temp_3, temp_4, temp_5, temp_6, temp_7, temp_8,
                vacuum_1, vacuum_2, cold_trap_temp
            FROM telemetry
            WHERE device_id = :device_id AND shelf_id = :shelf_id
            ORDER BY shelf_id, timestamp DESC
        """)
        
        result = await db.execute(query, {"device_id": device_id, "shelf_id": shelf_id})
        row = result.first()
        
        if not row:
            return {"status": "no_data", "alarms": []}
        
        temperatures = [row.temp_1, row.temp_2, row.temp_3, row.temp_4,
                       row.temp_5, row.temp_6, row.temp_7, row.temp_8]
        vacuum_levels = [row.vacuum_1, row.vacuum_2]
        cold_trap_temp = row.cold_trap_temp
        
        temperatures = [t for t in temperatures if t is not None]
        vacuum_levels = [v for v in vacuum_levels if v is not None]
        
        if len(temperatures) < 8 or len(vacuum_levels) < 2:
            return {"status": "insufficient_data", "alarms": []}
        
        alarms = alarm_detector.process_telemetry(
            device_id, shelf_id,
            temperatures, vacuum_levels, cold_trap_temp
        )
        
        if alarms:
            background_tasks.add_task(process_new_alarms, alarms, db)
        
        return {
            "status": "success",
            "alarm_count": len(alarms),
            "alarms": [
                {
                    "id": str(a.id),
                    "alarm_type": a.alarm_type,
                    "severity": a.severity,
                    "message": a.message
                }
                for a in alarms
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/thresholds")
async def get_alarm_thresholds():
    t = alarm_detector.thresholds
    return {
        "temp_diff": t.temp_diff,
        "vacuum_min": t.vacuum_min,
        "vacuum_max": t.vacuum_max,
        "cold_trap_max": t.cold_trap_max,
        "moisture_max": t.moisture_max,
        "reconstitution_max": t.reconstitution_max
    }


@router.put("/thresholds")
async def set_alarm_thresholds(
    temp_diff: Optional[float] = None,
    vacuum_min: Optional[float] = None,
    vacuum_max: Optional[float] = None,
    cold_trap_max: Optional[float] = None,
    moisture_max: Optional[float] = None,
    reconstitution_max: Optional[float] = None
):
    t = alarm_detector.thresholds
    
    if temp_diff is not None:
        t.temp_diff = temp_diff
    if vacuum_min is not None:
        t.vacuum_min = vacuum_min
    if vacuum_max is not None:
        t.vacuum_max = vacuum_max
    if cold_trap_max is not None:
        t.cold_trap_max = cold_trap_max
    if moisture_max is not None:
        t.moisture_max = moisture_max
    if reconstitution_max is not None:
        t.reconstitution_max = reconstitution_max
    
    alarm_detector.update_thresholds(t)
    
    return {"status": "success", "thresholds": {
        "temp_diff": t.temp_diff,
        "vacuum_min": t.vacuum_min,
        "vacuum_max": t.vacuum_max,
        "cold_trap_max": t.cold_trap_max,
        "moisture_max": t.moisture_max,
        "reconstitution_max": t.reconstitution_max
    }}


@router.get("/mqtt/status")
async def get_mqtt_status():
    return {
        "connected": mqtt_service.is_available(),
        "broker": settings.MQTT_BROKER,
        "port": settings.MQTT_PORT,
        "topic": settings.MQTT_TOPIC
    }
