from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_NAME: str = "冻干机温度均匀性控制系统"
    APP_VERSION: str = "1.0.0"
    
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/freeze_dryer"
    
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_TOPIC: str = "pharmacy/mes/alarm"
    MQTT_USERNAME: Optional[str] = None
    MQTT_PASSWORD: Optional[str] = None
    
    TEMP_DIFF_THRESHOLD: float = 1.0
    VACUUM_MIN_THRESHOLD: float = 0.1
    VACUUM_MAX_THRESHOLD: float = 100.0
    COLD_TRAP_MAX_THRESHOLD: float = -50.0
    MOISTURE_MAX_THRESHOLD: float = 3.0
    RECONSTITUTION_MAX_THRESHOLD: float = 120.0
    
    AUTO_CONTROL_ENABLED: bool = True
    CONTROL_INTERVAL: int = 10
    
    PLS_N_COMPONENTS: int = 6
    PREDICTION_INTERVAL: int = 60
    
    CORS_ORIGINS: list = ["*"]
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
