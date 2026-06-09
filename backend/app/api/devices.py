from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.core.database import get_db
from app.models.models import Device, Shelf
from app.schemas.telemetry import DeviceInfo, ShelfInfo

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=List[DeviceInfo])
async def get_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.id))
    devices = result.scalars().all()
    return devices


@router.get("/{device_id}", response_model=DeviceInfo)
async def get_device(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.get("/{device_id}/shelves", response_model=List[ShelfInfo])
async def get_device_shelves(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Shelf).where(Shelf.device_id == device_id).order_by(Shelf.shelf_number)
    )
    shelves = result.scalars().all()
    return shelves
