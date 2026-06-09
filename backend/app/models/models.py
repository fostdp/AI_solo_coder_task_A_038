from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.core.database import Base


class Device(Base):
    __tablename__ = "devices"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    location = Column(String(100))
    status = Column(String(20), default="running")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Shelf(Base):
    __tablename__ = "shelves"
    
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"))
    shelf_number = Column(Integer, nullable=False)
    temp_sensor_count = Column(Integer, default=8)
    vacuum_sensor_count = Column(Integer, default=2)


class Telemetry(Base):
    __tablename__ = "telemetry"
    
    timestamp = Column(DateTime(timezone=True), primary_key=True)
    device_id = Column(Integer, primary_key=True)
    shelf_id = Column(Integer, primary_key=True)
    temp_1 = Column(Float)
    temp_2 = Column(Float)
    temp_3 = Column(Float)
    temp_4 = Column(Float)
    temp_5 = Column(Float)
    temp_6 = Column(Float)
    temp_7 = Column(Float)
    temp_8 = Column(Float)
    vacuum_1 = Column(Float)
    vacuum_2 = Column(Float)
    cold_trap_temp = Column(Float)
    power_1 = Column(Float)
    power_2 = Column(Float)
    power_3 = Column(Float)
    power_4 = Column(Float)
    power_5 = Column(Float)
    power_6 = Column(Float)
    power_7 = Column(Float)
    power_8 = Column(Float)


class ControlCommand(Base):
    __tablename__ = "control_commands"
    
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"))
    shelf_id = Column(Integer, ForeignKey("shelves.id", ondelete="CASCADE"))
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    power_adj_1 = Column(Float)
    power_adj_2 = Column(Float)
    power_adj_3 = Column(Float)
    power_adj_4 = Column(Float)
    power_adj_5 = Column(Float)
    power_adj_6 = Column(Float)
    power_adj_7 = Column(Float)
    power_adj_8 = Column(Float)
    auto_mode = Column(Boolean, default=True)


class PredictionResult(Base):
    __tablename__ = "prediction_results"
    
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"))
    batch_id = Column(String(50))
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    moisture_pred = Column(Float)
    moisture_conf = Column(Float)
    moisture_threshold = Column(Float, default=3.0)
    reconstitution_pred = Column(Float)
    reconstitution_conf = Column(Float)
    reconstitution_threshold = Column(Float, default=120.0)
    drying_rate = Column(Float)
    is_qualified = Column(Boolean)


class Alarm(Base):
    __tablename__ = "alarms"
    
    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"))
    shelf_id = Column(Integer, ForeignKey("shelves.id", ondelete="CASCADE"))
    alarm_type = Column(String(30), nullable=False)
    severity = Column(String(10), nullable=False)
    message = Column(String, nullable=False)
    acknowledged = Column(Boolean, default=False)
    acknowledged_by = Column(String(50))
    acknowledged_at = Column(DateTime(timezone=True))


class SystemConfig(Base):
    __tablename__ = "system_config"
    
    key = Column(String(50), primary_key=True)
    value = Column(String)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
