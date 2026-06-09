from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, and_
from typing import List, Optional
from datetime import datetime, timedelta
from app.core.database import get_db
from app.models.models import Telemetry
from app.schemas.telemetry import TelemetryData, RealtimeData
import numpy as np

router = APIRouter(prefix="/api/data", tags=["data"])


@router.post("/telemetry", status_code=201)
async def receive_telemetry(
    data: TelemetryData,
    db: AsyncSession = Depends(get_db)
):
    try:
        temp_fields = [f"temp_{i+1}" for i in range(8)]
        vacuum_fields = [f"vacuum_{i+1}" for i in range(2)]
        power_fields = [f"power_{i+1}" for i in range(8)]
        
        values = {
            "timestamp": data.timestamp,
            "device_id": data.device_id,
            "shelf_id": data.shelf_id,
            "cold_trap_temp": data.cold_trap_temp,
            **{k: v for k, v in zip(temp_fields, data.temperatures)},
            **{k: v for k, v in zip(vacuum_fields, data.vacuum_levels)},
            **{k: v for k, v in zip(power_fields, data.heating_powers)},
        }
        
        insert_sql = text(f"""
            INSERT INTO telemetry (
                timestamp, device_id, shelf_id, cold_trap_temp,
                {', '.join(temp_fields)}, {', '.join(vacuum_fields)}, {', '.join(power_fields)}
            ) VALUES (
                :timestamp, :device_id, :shelf_id, :cold_trap_temp,
                {', '.join([f':{k}' for k in temp_fields])},
                {', '.join([f':{k}' for k in vacuum_fields])},
                {', '.join([f':{k}' for k in power_fields])}
            )
            ON CONFLICT (timestamp, device_id, shelf_id) DO UPDATE SET
                cold_trap_temp = EXCLUDED.cold_trap_temp,
                {', '.join([f'{k} = EXCLUDED.{k}' for k in temp_fields])},
                {', '.join([f'{k} = EXCLUDED.{k}' for k in vacuum_fields])},
                {', '.join([f'{k} = EXCLUDED.{k}' for k in power_fields])}
        """)
        
        await db.execute(insert_sql, values)
        await db.commit()
        
        return {"status": "success", "message": "Telemetry data stored"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/realtime/{device_id}", response_model=List[RealtimeData])
async def get_realtime_data(
    device_id: int,
    db: AsyncSession = Depends(get_db)
):
    try:
        query = text(f"""
            SELECT DISTINCT ON (shelf_id)
                timestamp, device_id, shelf_id,
                temp_1, temp_2, temp_3, temp_4, temp_5, temp_6, temp_7, temp_8,
                vacuum_1, vacuum_2,
                cold_trap_temp,
                power_1, power_2, power_3, power_4, power_5, power_6, power_7, power_8
            FROM telemetry
            WHERE device_id = :device_id
            ORDER BY shelf_id, timestamp DESC
        """)
        
        result = await db.execute(query, {"device_id": device_id})
        rows = result.all()
        
        realtime_data = []
        for row in rows:
            temps = [row.temp_1, row.temp_2, row.temp_3, row.temp_4,
                    row.temp_5, row.temp_6, row.temp_7, row.temp_8]
            vacuums = [row.vacuum_1, row.vacuum_2]
            powers = [row.power_1, row.power_2, row.power_3, row.power_4,
                     row.power_5, row.power_6, row.power_7, row.power_8]
            
            temps_array = np.array([t for t in temps if t is not None])
            if len(temps_array) > 0:
                temp_diff = float(np.max(temps_array) - np.min(temps_array))
                avg_temp = float(np.mean(temps_array))
            else:
                temp_diff = 0.0
                avg_temp = 0.0
            
            vac_array = np.array([v for v in vacuums if v is not None])
            avg_vacuum = float(np.mean(vac_array)) if len(vac_array) > 0 else 0.0
            
            realtime_data.append(RealtimeData(
                device_id=row.device_id,
                shelf_id=row.shelf_id,
                timestamp=row.timestamp,
                temperatures=temps,
                temperature_diff=temp_diff,
                avg_temperature=avg_temp,
                vacuum_levels=vacuums,
                avg_vacuum=avg_vacuum,
                cold_trap_temp=row.cold_trap_temp,
                heating_powers=powers
            ))
        
        return realtime_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_history_data(
    device_id: int,
    shelf_id: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db)
):
    try:
        if not start_time:
            start_time = datetime.now() - timedelta(hours=1)
        if not end_time:
            end_time = datetime.now()
        
        conditions = [
            Telemetry.timestamp >= start_time,
            Telemetry.timestamp <= end_time,
            Telemetry.device_id == device_id
        ]
        if shelf_id:
            conditions.append(Telemetry.shelf_id == shelf_id)
        
        query = (
            select(Telemetry)
            .where(and_(*conditions))
            .order_by(Telemetry.timestamp.desc())
            .limit(limit)
        )
        
        result = await db.execute(query)
        rows = result.scalars().all()
        
        data = []
        for row in rows:
            data.append({
                "timestamp": row.timestamp,
                "device_id": row.device_id,
                "shelf_id": row.shelf_id,
                "temperatures": [row.temp_1, row.temp_2, row.temp_3, row.temp_4,
                                row.temp_5, row.temp_6, row.temp_7, row.temp_8],
                "vacuum_levels": [row.vacuum_1, row.vacuum_2],
                "cold_trap_temp": row.cold_trap_temp,
                "heating_powers": [row.power_1, row.power_2, row.power_3, row.power_4,
                                  row.power_5, row.power_6, row.power_7, row.power_8]
            })
        
        return {"count": len(data), "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/{device_id}")
async def get_device_stats(
    device_id: int,
    hours: int = Query(1, ge=1, le=24),
    db: AsyncSession = Depends(get_db)
):
    try:
        start_time = datetime.now() - timedelta(hours=hours)
        
        query = text(f"""
            SELECT 
                shelf_id,
                COUNT(*) as sample_count,
                AVG((temp_1+temp_2+temp_3+temp_4+temp_5+temp_6+temp_7+temp_8)/8) as avg_temp,
                MAX(GREATEST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) as max_temp,
                MIN(LEAST(temp_1,temp_2,temp_3,temp_4,temp_5,temp_6,temp_7,temp_8)) as min_temp,
                AVG((vacuum_1+vacuum_2)/2) as avg_vacuum,
                AVG(cold_trap_temp) as avg_cold_trap
            FROM telemetry
            WHERE device_id = :device_id AND timestamp >= :start_time
            GROUP BY shelf_id
            ORDER BY shelf_id
        """)
        
        result = await db.execute(query, {"device_id": device_id, "start_time": start_time})
        rows = result.all()
        
        stats = []
        for row in rows:
            stats.append({
                "shelf_id": row.shelf_id,
                "sample_count": row.sample_count,
                "avg_temp": round(float(row.avg_temp), 2) if row.avg_temp else None,
                "max_temp": round(float(row.max_temp), 2) if row.max_temp else None,
                "min_temp": round(float(row.min_temp), 2) if row.min_temp else None,
                "temp_diff": round(float(row.max_temp - row.min_temp), 2) if row.max_temp and row.min_temp else None,
                "avg_vacuum": round(float(row.avg_vacuum), 4) if row.avg_vacuum else None,
                "avg_cold_trap": round(float(row.avg_cold_trap), 2) if row.avg_cold_trap else None
            })
        
        return {"device_id": device_id, "time_window_hours": hours, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
