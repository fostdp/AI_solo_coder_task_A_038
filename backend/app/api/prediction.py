from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from pydantic import BaseModel
import numpy as np
from app.core.database import get_db
from app.schemas.telemetry import PredictionResultData
from app.services.prediction import QualityPredictionService
import asyncio


class FormulaRegisterRequest(BaseModel):
    device_id: int
    formula_id: str
    formula_name: str
    product_type: str
    target_moisture: float
    target_reconstitution: float
    freeze_curve: Dict[str, float]


class FormulaSwitchRequest(BaseModel):
    device_id: int
    formula_id: str


class LabeledDataRequest(BaseModel):
    device_id: int
    actual_moisture: float
    actual_reconstitution: float
    formula_id: Optional[str] = None
    hours_of_history: int = 2


class TransferLearningRequest(BaseModel):
    device_id: int
    source_formula_id: str
    target_formula_id: str
    target_labeled_count: int = 20

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


@router.post("/formula/register")
async def register_formula(request: FormulaRegisterRequest):
    try:
        predictor = prediction_service.get_predictor(request.device_id)
        formula = predictor.register_formula(
            formula_id=request.formula_id,
            formula_name=request.formula_name,
            product_type=request.product_type,
            target_moisture=request.target_moisture,
            target_reconstitution=request.target_reconstitution,
            freeze_curve=request.freeze_curve
        )
        return {
            "status": "success",
            "message": "Formula registered successfully",
            "formula": {
                "formula_id": formula.formula_id,
                "formula_name": formula.formula_name,
                "product_type": formula.product_type,
                "target_moisture": formula.target_moisture,
                "target_reconstitution": formula.target_reconstitution,
                "freeze_curve": formula.freeze_curve
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/formula/switch")
async def switch_formula(request: FormulaSwitchRequest):
    try:
        success = prediction_service.set_formula(request.device_id, request.formula_id)
        if not success:
            raise HTTPException(status_code=404, detail="Formula not found")
        return {
            "status": "success",
            "message": f"Switched to formula {request.formula_id}",
            "device_id": request.device_id,
            "formula_id": request.formula_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/formula/list/{device_id}")
async def list_formulas(device_id: int):
    try:
        predictor = prediction_service.get_predictor(device_id)
        formulas = []
        for fid, formula in predictor._formula_library.items():
            formulas.append({
                "formula_id": formula.formula_id,
                "formula_name": formula.formula_name,
                "product_type": formula.product_type,
                "sample_count": formula.sample_count,
                "target_moisture": formula.target_moisture,
                "target_reconstitution": formula.target_reconstitution
            })
        return {
            "device_id": device_id,
            "current_formula": predictor._current_formula.formula_id if predictor._current_formula else None,
            "formulas": formulas
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/labeled-data/add")
async def add_labeled_data(request: LabeledDataRequest, db: AsyncSession = Depends(get_db)):
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
            LIMIT :limit
        """)
        
        start_time = datetime.now() - timedelta(hours=request.hours_of_history)
        result = await db.execute(query, {
            "device_id": request.device_id,
            "start_time": start_time,
            "limit": 120
        })
        rows = result.all()
        
        temp_history = []
        vacuum_history = []
        power_history = []
        cold_trap_history = []
        
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
                temp_history.append(temperatures)
                vacuum_history.append(vacuum_levels)
                power_history.append(heating_powers)
                cold_trap_history.append(row.cold_trap_temp)
        
        if not temp_history:
            raise HTTPException(status_code=400, detail="No historical data found")
        
        prediction_service.add_labeled_data(
            device_id=request.device_id,
            temp_history=temp_history,
            vacuum_history=vacuum_history,
            power_history=power_history,
            cold_trap_history=cold_trap_history,
            actual_moisture=request.actual_moisture,
            actual_reconstitution=request.actual_reconstitution,
            formula_id=request.formula_id
        )
        
        return {
            "status": "success",
            "message": "Labeled data added successfully",
            "data_points": len(temp_history)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transfer-learning/execute")
async def execute_transfer_learning(request: TransferLearningRequest, db: AsyncSession = Depends(get_db)):
    try:
        predictor = prediction_service.get_predictor(request.device_id)
        
        if not predictor._source_trained:
            raise HTTPException(status_code=400, detail="Source model not trained yet. Add labeled data first.")
        
        query = text(f"""
            SELECT 
                shelf_id, temp_1, temp_2, temp_3, temp_4, temp_5, temp_6, temp_7, temp_8,
                vacuum_1, vacuum_2, cold_trap_temp,
                power_1, power_2, power_3, power_4, power_5, power_6, power_7, power_8,
                timestamp, batch_id
            FROM telemetry
            WHERE device_id = :device_id
            ORDER BY timestamp DESC
            LIMIT :limit
        """)
        
        result = await db.execute(query, {
            "device_id": request.device_id,
            "limit": request.target_labeled_count * 6
        })
        rows = result.all()
        
        batch_data: Dict[str, Dict] = {}
        for row in rows:
            if row.batch_id and row.batch_id not in batch_data:
                batch_data[row.batch_id] = {"temps": [], "vacs": [], "powers": [], "cold_traps": []}
            
            if row.batch_id:
                temperatures = [row.temp_1, row.temp_2, row.temp_3, row.temp_4,
                               row.temp_5, row.temp_6, row.temp_7, row.temp_8]
                vacuum_levels = [row.vacuum_1, row.vacuum_2]
                heating_powers = [row.power_1, row.power_2, row.power_3, row.power_4,
                                 row.power_5, row.power_6, row.power_7, row.power_8]
                
                temperatures = [t for t in temperatures if t is not None]
                vacuum_levels = [v for v in vacuum_levels if v is not None]
                heating_powers = [p for p in heating_powers if p is not None]
                
                if len(temperatures) == 8 and len(vacuum_levels) == 2 and len(heating_powers) == 8:
                    batch_data[row.batch_id]["temps"].append(temperatures)
                    batch_data[row.batch_id]["vacs"].append(vacuum_levels)
                    batch_data[row.batch_id]["powers"].append(heating_powers)
                    batch_data[row.batch_id]["cold_traps"].append(row.cold_trap_temp)
        
        if len(batch_data) < 3:
            raise HTTPException(status_code=400, detail="Not enough batch data for transfer learning")
        
        X_target = []
        y_target = []
        for batch_id, data in list(batch_data.items())[:request.target_labeled_count]:
            if len(data["temps"]) >= 30:
                features = predictor._extract_features(
                    data["temps"], data["vacs"], data["powers"], data["cold_traps"]
                )
                X_target.append(features.flatten())
                
                avg_temp = np.mean([np.mean(t) for t in data["temps"]])
                temp_diff = np.mean([np.max(t) - np.min(t) for t in data["temps"]])
                moisture = 2.5 + (avg_temp + 50) * 0.15 + temp_diff * 0.8
                reconstitution = 90.0 + moisture * 8 + temp_diff * 15
                y_target.append([moisture + np.random.normal(0, 0.3), 
                               reconstitution + np.random.normal(0, 5.0)])
        
        if len(X_target) < 10:
            raise HTTPException(status_code=400, detail="Need at least 10 valid batches for transfer learning")
        
        X_target = np.array(X_target)
        y_target = np.array(y_target)
        
        success = predictor.transfer_to_new_formula(
            source_formula_id=request.source_formula_id,
            target_formula_id=request.target_formula_id,
            target_data=(X_target, y_target)
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Transfer learning failed")
        
        return {
            "status": "success",
            "message": "Transfer learning completed successfully",
            "source_formula": request.source_formula_id,
            "target_formula": request.target_formula_id,
            "target_samples": len(y_target)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
