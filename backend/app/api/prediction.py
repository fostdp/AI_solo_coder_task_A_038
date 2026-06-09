from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Optional
from datetime import datetime, timedelta
from app.core.database import get_db
from app.schemas.telemetry import PredictionResultData
from app.services.prediction import QualityPredictionService
import asyncio

router = APIRouter(prefix="/api/prediction", tags=["prediction"])

prediction_service = QualityPredictionService(n_devices=10)


@router.post("/quality")
async def predict_quality(
    device_id: int,
    batch_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    try:
        query = text(f"""
            SELECT 
                shelf_id, temp_1, temp_2, temp_3, temp_4, temp_5, temp_6, temp_7, temp_8,
                vacuum_1, vacuum_2, cold_trap_temp,
                power_1, power_2, power_3, power_4, power_5, power_6, power_7, power_8,
                timestamp
            FROM telemetry
            WHERE device_id = :device_id AND timestamp >= :start_time
            ORDER BY timestamp DESC
            LIMIT 600
        """)
        
        start_time = datetime.now() - timedelta(hours=2)
        result = await db.execute(query, {"device_id": device_id, "start_time": start_time})
        rows = result.all()
        
        for row in rows:
            temperatures = [row.temp_1, row.temp_2, row.temp_3, row.temp_4,
                           row.temp_5, row.temp_6, row.temp_7, row.temp_8]
            vacuum_levels = [row.vacuum_1, row.vacuum_2]
            heating_powers = [row.power_1, row.power_2, row.power_3, row.power_4,
                             row.power_5, row.power_6, row.power_7, row.power_8]
            
            temperatures = [t for t in temperatures if t is not None]
            vacuum_levels = [v for v in vacuum_levels if v is not None]
            heating_powers = [p for p in heating_powers if p is not None]
            
            if len(temperatures) == 8 and len(vacuum_levels) == 2 and len(heating_powers) == 8:
                prediction_service.add_telemetry(
                    device_id, row.shelf_id,
                    temperatures, vacuum_levels,
                    heating_powers, row.cold_trap_temp
                )
        
        prediction = prediction_service.predict(device_id, batch_id)
        
        if not batch_id:
            batch_id = f"BATCH-{device_id}-{datetime.now().strftime('%Y%m%d%H%M')}"
        
        insert_sql = text("""
            INSERT INTO prediction_results (
                device_id, batch_id, timestamp,
                moisture_pred, moisture_conf, moisture_threshold,
                reconstitution_pred, reconstitution_conf, reconstitution_threshold,
                drying_rate, is_qualified
            ) VALUES (
                :device_id, :batch_id, :timestamp,
                :moisture_pred, :moisture_conf, :moisture_threshold,
                :reconstitution_pred, :reconstitution_conf, :reconstitution_threshold,
                :drying_rate, :is_qualified
            )
        """)
        
        await db.execute(insert_sql, {
            "device_id": device_id,
            "batch_id": batch_id,
            "timestamp": datetime.now(),
            "moisture_pred": prediction.moisture_content,
            "moisture_conf": prediction.moisture_confidence,
            "moisture_threshold": prediction.moisture_threshold,
            "reconstitution_pred": prediction.reconstitution_time,
            "reconstitution_conf": prediction.reconstitution_confidence,
            "reconstitution_threshold": prediction.reconstitution_threshold,
            "drying_rate": prediction.drying_rate,
            "is_qualified": prediction.is_qualified
        })
        await db.commit()
        
        return PredictionResultData(
            device_id=device_id,
            batch_id=batch_id,
            moisture_content={
                "predicted": prediction.moisture_content,
                "confidence": prediction.moisture_confidence,
                "threshold": prediction.moisture_threshold,
                "is_qualified": prediction.moisture_content <= prediction.moisture_threshold
            },
            reconstitution_time={
                "predicted": prediction.reconstitution_time,
                "confidence": prediction.reconstitution_confidence,
                "threshold": prediction.reconstitution_threshold,
                "is_qualified": prediction.reconstitution_time <= prediction.reconstitution_threshold
            },
            drying_rate=prediction.drying_rate,
            is_qualified=prediction.is_qualified,
            timestamp=datetime.now()
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/result/{device_id}")
async def get_prediction_result(
    device_id: int,
    limit: int = 10,
    db: AsyncSession = Depends(get_db)
):
    try:
        query = text(f"""
            SELECT * FROM prediction_results
            WHERE device_id = :device_id
            ORDER BY timestamp DESC
            LIMIT :limit
        """)
        
        result = await db.execute(query, {"device_id": device_id, "limit": limit})
        rows = result.all()
        
        results = []
        for row in rows:
            results.append({
                "id": row.id,
                "device_id": row.device_id,
                "batch_id": row.batch_id,
                "timestamp": row.timestamp,
                "moisture_content": {
                    "predicted": row.moisture_pred,
                    "confidence": row.moisture_conf,
                    "threshold": row.moisture_threshold,
                    "is_qualified": row.moisture_pred <= row.moisture_threshold
                },
                "reconstitution_time": {
                    "predicted": row.reconstitution_pred,
                    "confidence": row.reconstitution_conf,
                    "threshold": row.reconstitution_threshold,
                    "is_qualified": row.reconstitution_pred <= row.reconstitution_threshold
                },
                "drying_rate": row.drying_rate,
                "is_qualified": row.is_qualified
            })
        
        return {"count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/model/{device_id}")
async def get_model_info(device_id: int):
    predictor = prediction_service.get_predictor(device_id)
    return predictor.get_model_info()


@router.put("/thresholds")
async def set_prediction_thresholds(
    moisture_max: float,
    reconstitution_max: float
):
    if moisture_max <= 0 or reconstitution_max <= 0:
        raise HTTPException(status_code=400, detail="Thresholds must be positive")
    prediction_service.set_thresholds(moisture_max, reconstitution_max)
    return {
        "status": "success",
        "moisture_max_threshold": moisture_max,
        "reconstitution_max_threshold": reconstitution_max
    }
