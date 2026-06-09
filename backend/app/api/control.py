from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Optional
from datetime import datetime
from app.core.database import get_db
from app.schemas.telemetry import ControlCommand, ControlModeUpdate
from app.services.control import TemperatureUniformityController
import asyncio

router = APIRouter(prefix="/api/control", tags=["control"])

controller = TemperatureUniformityController(n_shelves=5, n_heaters_per_shelf=8)
auto_mode = {device_id: True for device_id in range(1, 11)}


@router.post("/power")
async def send_control_command(
    command: ControlCommand,
    db: AsyncSession = Depends(get_db)
):
    try:
        power_fields = [f"power_adj_{i+1}" for i in range(8)]
        
        values = {
            "device_id": command.device_id,
            "shelf_id": command.shelf_id,
            "timestamp": command.timestamp or datetime.now(),
            "auto_mode": command.auto_mode,
            **{k: v for k, v in zip(power_fields, command.power_adjustments)},
        }
        
        insert_sql = text(f"""
            INSERT INTO control_commands (
                device_id, shelf_id, timestamp, auto_mode,
                {', '.join(power_fields)}
            ) VALUES (
                :device_id, :shelf_id, :timestamp, :auto_mode,
                {', '.join([f':{k}' for k in power_fields])}
            )
        """)
        
        await db.execute(insert_sql, values)
        await db.commit()
        
        if not command.auto_mode:
            auto_mode[command.device_id] = False
            controller.set_auto_mode(False)
        
        return {
            "status": "success",
            "message": "Control command sent",
            "command": command.model_dump()
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/mode")
async def set_control_mode(update: ControlModeUpdate):
    auto_mode[update.device_id] = update.auto_mode
    controller.set_auto_mode(update.auto_mode)
    return {
        "status": "success",
        "device_id": update.device_id,
        "auto_mode": update.auto_mode
    }


@router.get("/latest/{device_id}")
async def get_latest_control_command(
    device_id: int,
    shelf_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    try:
        if not auto_mode.get(device_id, True):
            return None
        
        query = text(f"""
            SELECT * FROM control_commands
            WHERE device_id = :device_id
            {f"AND shelf_id = {shelf_id}" if shelf_id else ""}
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        
        result = await db.execute(query, {"device_id": device_id})
        row = result.first()
        
        if row:
            return {
                "id": row.id,
                "device_id": row.device_id,
                "shelf_id": row.shelf_id,
                "timestamp": row.timestamp,
                "power_adj_1": row.power_adj_1,
                "power_adj_2": row.power_adj_2,
                "power_adj_3": row.power_adj_3,
                "power_adj_4": row.power_adj_4,
                "power_adj_5": row.power_adj_5,
                "power_adj_6": row.power_adj_6,
                "power_adj_7": row.power_adj_7,
                "power_adj_8": row.power_adj_8,
                "auto_mode": row.auto_mode
            }
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calculate/{device_id}/{shelf_id}")
async def calculate_power_adjustment(
    device_id: int,
    shelf_id: int,
    db: AsyncSession = Depends(get_db)
):
    try:
        if not auto_mode.get(device_id, True):
            return {"auto_mode": False, "adjustments": [0] * 8}
        
        query = text(f"""
            SELECT DISTINCT ON (shelf_id)
                temp_1, temp_2, temp_3, temp_4, temp_5, temp_6, temp_7, temp_8,
                power_1, power_2, power_3, power_4, power_5, power_6, power_7, power_8
            FROM telemetry
            WHERE device_id = :device_id AND shelf_id = :shelf_id
            ORDER BY shelf_id, timestamp DESC
        """)
        
        result = await db.execute(query, {"device_id": device_id, "shelf_id": shelf_id})
        row = result.first()
        
        if not row:
            return {"status": "no_data", "adjustments": [0] * 8}
        
        temperatures = [row.temp_1, row.temp_2, row.temp_3, row.temp_4,
                       row.temp_5, row.temp_6, row.temp_7, row.temp_8]
        powers = [row.power_1, row.power_2, row.power_3, row.power_4,
                 row.power_5, row.power_6, row.power_7, row.power_8]
        
        temperatures = [t for t in temperatures if t is not None]
        powers = [p for p in powers if p is not None]
        
        if len(temperatures) < 8 or len(powers) < 8:
            return {"status": "insufficient_data", "adjustments": [0] * 8}
        
        uniformity = controller.get_temperature_uniformity(temperatures)
        adjustments = controller.calculate_power_adjustments(shelf_id, temperatures, powers)
        
        return {
            "auto_mode": True,
            "uniformity": uniformity,
            "adjustments": [round(a, 2) for a in adjustments],
            "current_temperatures": temperatures,
            "current_powers": powers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threshold")
async def get_threshold():
    return {"temp_diff_threshold": controller.temp_diff_threshold}


@router.put("/threshold")
async def set_threshold(threshold: float):
    if threshold <= 0:
        raise HTTPException(status_code=400, detail="Threshold must be positive")
    controller.set_threshold(threshold)
    return {"status": "success", "temp_diff_threshold": threshold}


@router.get("/status/{device_id}")
async def get_control_status(device_id: int):
    return {
        "device_id": device_id,
        "auto_mode": auto_mode.get(device_id, True),
        "temp_diff_threshold": controller.temp_diff_threshold
    }
