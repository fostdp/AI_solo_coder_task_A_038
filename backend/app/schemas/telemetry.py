from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
from uuid import UUID


class TelemetryData(BaseModel):
    device_id: int = Field(..., ge=1, le=10)
    shelf_id: int = Field(..., ge=1, le=5)
    timestamp: datetime
    temperatures: List[float] = Field(..., min_length=8, max_length=8)
    vacuum_levels: List[float] = Field(..., min_length=2, max_length=2)
    cold_trap_temp: float
    heating_powers: List[float] = Field(..., min_length=8, max_length=8)


class DeviceInfo(BaseModel):
    id: int
    name: str
    location: str
    status: str

    class Config:
        from_attributes = True


class ShelfInfo(BaseModel):
    id: int
    device_id: int
    shelf_number: int
    temp_sensor_count: int
    vacuum_sensor_count: int

    class Config:
        from_attributes = True


class TemperatureStats(BaseModel):
    timestamp: datetime
    device_id: int
    shelf_id: int
    avg_temp: float
    max_temp: float
    min_temp: float
    temp_diff: float
    avg_vacuum: float
    avg_cold_trap: float


class ControlCommand(BaseModel):
    device_id: int
    shelf_id: int
    timestamp: Optional[datetime] = None
    power_adjustments: List[float] = Field(..., min_length=8, max_length=8)
    auto_mode: bool = True


class ControlModeUpdate(BaseModel):
    device_id: int
    auto_mode: bool


class PredictionResultData(BaseModel):
    device_id: int
    batch_id: Optional[str] = None
    moisture_content: dict
    reconstitution_time: dict
    drying_rate: float
    is_qualified: bool
    timestamp: Optional[datetime] = None


class AlarmData(BaseModel):
    id: Optional[UUID] = None
    timestamp: Optional[datetime] = None
    device_id: int
    shelf_id: Optional[int] = None
    alarm_type: str
    severity: str
    message: str
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


class AlarmAcknowledge(BaseModel):
    alarm_id: UUID
    acknowledged_by: str


class RealtimeData(BaseModel):
    device_id: int
    shelf_id: int
    timestamp: datetime
    temperatures: List[float]
    temperature_diff: float
    avg_temperature: float
    vacuum_levels: List[float]
    avg_vacuum: float
    cold_trap_temp: float
    heating_powers: List[float]
    has_alarm: bool = False
