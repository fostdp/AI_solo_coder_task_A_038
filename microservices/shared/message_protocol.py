"""
消息协议定义
微服务间通信的消息格式规范
"""

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from uuid import UUID, uuid4


@dataclass
class MessageHeader:
    """消息头"""
    message_id: str
    message_type: str
    source_service: str
    target_service: Optional[str]
    timestamp: str
    version: str = "1.0"


@dataclass
class TelemetryData:
    """遥测数据消息"""
    device_id: int
    shelf_id: int
    timestamp: str
    temperatures: List[float]
    vacuum_levels: List[float]
    cold_trap_temp: float
    heating_powers: List[float]
    batch_id: Optional[str] = None
    cycle_id: Optional[int] = None
    data_quality: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ControlCommand:
    """控制命令消息"""
    device_id: int
    shelf_id: int
    timestamp: str
    auto_mode: bool
    power_adjustments: List[float]
    target_temp: Optional[float] = None
    batch_id: Optional[str] = None
    command_id: str = ""

    def __post_init__(self):
        if not self.command_id:
            self.command_id = str(uuid4())

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ControlStatus:
    """控制状态消息"""
    device_id: int
    shelf_id: int
    timestamp: str
    auto_mode: bool
    current_powers: List[float]
    temperature_diff: float
    avg_temperature: float
    adjustments: List[float]
    batch_id: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PredictionResult:
    """预测结果消息"""
    device_id: int
    timestamp: str
    moisture_content: float
    moisture_confidence: float
    reconstitution_time: float
    reconstitution_confidence: float
    drying_rate: float
    is_qualified: bool
    moisture_threshold: float
    reconstitution_threshold: float
    formula_id: Optional[str] = None
    batch_id: Optional[str] = None
    drift_detected: bool = False
    adaptation_level: float = 0.0
    model_version: str = "2.0"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AlarmEvent:
    """告警事件消息"""
    alarm_id: str
    device_id: int
    shelf_id: Optional[int]
    timestamp: str
    alarm_type: str
    severity: str
    message: str
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AlarmAck:
    """告警确认消息"""
    alarm_id: str
    acknowledged_by: str
    timestamp: str

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ConfigUpdate:
    """配置更新消息"""
    config_type: str  # control, prediction, alarm
    config_data: Dict
    source_service: str
    timestamp: str

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ServiceStatus:
    """服务状态消息"""
    service_id: str
    service_type: str
    status: str  # running, error, degraded
    timestamp: str
    metrics: Dict[str, Any]
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


class MessageFactory:
    """消息工厂"""

    @staticmethod
    def create_telemetry(data: TelemetryData, source_service: str) -> Dict:
        """创建遥测消息"""
        header = MessageHeader(
            message_id=str(uuid4()),
            message_type="telemetry",
            source_service=source_service,
            target_service=None,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        return {
            "header": asdict(header),
            "payload": data.to_dict()
        }

    @staticmethod
    def create_control_command(cmd: ControlCommand, source_service: str) -> Dict:
        """创建控制命令消息"""
        header = MessageHeader(
            message_id=str(uuid4()),
            message_type="control_command",
            source_service=source_service,
            target_service="profinet-driver",
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        return {
            "header": asdict(header),
            "payload": cmd.to_dict()
        }

    @staticmethod
    def create_prediction(result: PredictionResult, source_service: str) -> Dict:
        """创建预测结果消息"""
        header = MessageHeader(
            message_id=str(uuid4()),
            message_type="prediction",
            source_service=source_service,
            target_service=None,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        return {
            "header": asdict(header),
            "payload": result.to_dict()
        }

    @staticmethod
    def create_alarm(alarm: AlarmEvent, source_service: str) -> Dict:
        """创建告警消息"""
        header = MessageHeader(
            message_id=str(uuid4()),
            message_type="alarm",
            source_service=source_service,
            target_service=None,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        return {
            "header": asdict(header),
            "payload": alarm.to_dict()
        }

    @staticmethod
    def create_config_update(config_type: str, config_data: Dict, source_service: str) -> Dict:
        """创建配置更新消息"""
        config = ConfigUpdate(
            config_type=config_type,
            config_data=config_data,
            source_service=source_service,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        header = MessageHeader(
            message_id=str(uuid4()),
            message_type="config",
            source_service=source_service,
            target_service=None,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        return {
            "header": asdict(header),
            "payload": config.to_dict()
        }

    @staticmethod
    def create_service_status(service_id: str, service_type: str, status: str,
                               metrics: Dict = None, error_message: str = None) -> Dict:
        """创建服务状态消息"""
        service_status = ServiceStatus(
            service_id=service_id,
            service_type=service_type,
            status=status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics or {},
            error_message=error_message
        )
        header = MessageHeader(
            message_id=str(uuid4()),
            message_type="status",
            source_service=service_id,
            target_service=None,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        return {
            "header": asdict(header),
            "payload": service_status.to_dict()
        }


def serialize_message(message: Dict) -> str:
    """序列化消息为JSON字符串"""
    return json.dumps(message, ensure_ascii=False)


def deserialize_message(message_str: str) -> Dict:
    """反序列化JSON字符串为消息"""
    return json.loads(message_str)


def validate_message(message: Dict, expected_type: str) -> bool:
    """验证消息类型"""
    try:
        return message["header"]["message_type"] == expected_type
    except (KeyError, TypeError):
        return False


def extract_payload(message: Dict) -> Dict:
    """提取消息载荷"""
    return message.get("payload", {})
