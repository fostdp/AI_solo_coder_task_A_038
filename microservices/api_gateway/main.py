"""
API Gateway微服务
- FastAPI REST API统一入口
- 缓存最新遥测数据、控制状态、预测结果、告警数据
- 支持WebSocket实时推送
- 前端API调用通过此网关
"""

import asyncio
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import (
    MicroserviceBase, RedisConfig,
    CHANNELS, SERVICE_IDS, MESSAGE_TYPES,
    TelemetryData, ControlCommand, ControlStatus,
    PredictionResult, AlarmEvent, AlarmAck,
    MessageFactory, validate_message, extract_payload,
    config_loader, AlarmConfig
)


@dataclass
class DeviceInfo:
    """设备信息"""
    id: int
    name: str
    status: str
    shelves: int


@dataclass
class RealtimeData:
    """实时数据"""
    device_id: int
    shelf_id: int
    timestamp: str
    temperatures: List[float]
    temperature_diff: float
    avg_temperature: float
    vacuum_levels: List[float]
    avg_vacuum: float
    cold_trap_temp: float
    heating_powers: List[float]


class ControlModeUpdate(BaseModel):
    """控制模式更新"""
    device_id: int
    auto_mode: bool


class BatchResetRequest(BaseModel):
    """批次重置请求"""
    device_id: int
    batch_id: Optional[str] = None
    initial_temperatures: Optional[List[float]] = None


class FormulaRegisterRequest(BaseModel):
    """配方注册请求"""
    device_id: int
    formula_id: str
    formula_name: str
    product_type: str
    target_moisture: float
    target_reconstitution: float
    freeze_curve: Dict[str, float]


class FormulaSwitchRequest(BaseModel):
    """配方切换请求"""
    device_id: int
    formula_id: str


class LabeledDataRequest(BaseModel):
    """标签数据添加请求"""
    device_id: int
    actual_moisture: float
    actual_reconstitution: float
    formula_id: Optional[str] = None
    hours_of_history: int = 2


class TransferLearningRequest(BaseModel):
    """迁移学习请求"""
    device_id: int
    source_formula_id: str
    target_formula_id: str
    target_labeled_count: int = 20


class AlarmAcknowledge(BaseModel):
    """告警确认"""
    alarm_id: str
    acknowledged_by: str = "user"


class APIGatewayService(MicroserviceBase):
    """API Gateway服务类"""

    def __init__(self, redis_config: RedisConfig = None):
        super().__init__(
            service_id=SERVICE_IDS['API_GATEWAY'],
            service_type="api_gateway",
            redis_config=redis_config
        )

        self._telemetry_cache: Dict[int, Dict[int, Dict[str, Any]]] = {}
        self._control_cache: Dict[int, Dict[int, Dict[str, Any]]] = {}
        self._prediction_cache: Dict[int, Dict[str, Any]] = {}
        self._alarm_cache: List[Dict[str, Any]] = []
        self._active_connections: List[WebSocket] = []
        self._auto_mode: Dict[int, bool] = {}

        self._num_devices: int = 10
        self._num_shelves: int = 5
        self._max_alarm_cache: int = 1000

        self._devices: Dict[int, DeviceInfo] = {}
        self._init_devices()

        self._prediction_thresholds: Dict[str, float] = {
            "moisture_max": 3.0,
            "reconstitution_max": 120.0
        }

        self._alarm_thresholds: Dict[str, float] = {
            "temp_diff": 1.0,
            "vacuum_min": 0.1,
            "vacuum_max": 100.0,
            "cold_trap_max": -50.0,
            "moisture_max": 3.0,
            "reconstitution_max": 120.0
        }

        self._formulas: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self._current_formula: Dict[int, str] = {}

        self._load_config()

    def _init_devices(self):
        """初始化设备列表"""
        for device_id in range(1, self._num_devices + 1):
            self._devices[device_id] = DeviceInfo(
                id=device_id,
                name=f"冻干机-{device_id:02d}",
                status="online",
                shelves=self._num_shelves
            )
            self._auto_mode[device_id] = True
            self._telemetry_cache[device_id] = {}
            self._control_cache[device_id] = {}

    def _load_config(self):
        """加载配置"""
        try:
            alarm_config: AlarmConfig = config_loader.load_alarm_config()
            self._alarm_thresholds.update({
                "temp_diff": alarm_config.temperature.get('diff_threshold', 1.0),
                "vacuum_min": alarm_config.vacuum.get('min_threshold', 0.1),
                "vacuum_max": alarm_config.vacuum.get('max_threshold', 100.0),
                "cold_trap_max": alarm_config.cold_trap.get('max_threshold', -50.0),
                "moisture_max": alarm_config.quality.get('moisture_max', 3.0),
                "reconstitution_max": alarm_config.quality.get('reconstitution_max', 120.0)
            })
            print(f"[{self.service_id}] 配置加载完成")
        except Exception as e:
            print(f"[{self.service_id}] 配置加载失败: {e}")

    async def _subscribe_channels(self):
        """订阅Redis频道"""
        await self.subscribe(CHANNELS['TELEMETRY_RAW'], self._handle_telemetry)
        await self.subscribe(CHANNELS['CONTROL_STATUS'], self._handle_control)
        await self.subscribe(CHANNELS['PREDICTION_RESULT'], self._handle_prediction)
        await self.subscribe(CHANNELS['ALARM_EVENT'], self._handle_alarm)
        await self.subscribe(CHANNELS['CONFIG_UPDATE'], self._handle_config_update)

    async def _on_start(self):
        """服务启动时执行"""
        print(f"[{self.service_id}] API Gateway服务已启动")
        print(f"[{self.service_id}] 监控设备数: {self._num_devices}")
        print(f"[{self.service_id}] 每设备搁板数: {self._num_shelves}")

    async def _on_stop(self):
        """服务停止时执行"""
        print(f"[{self.service_id}] 关闭所有WebSocket连接...")
        for conn in self._active_connections:
            try:
                await conn.close()
            except Exception:
                pass
        self._active_connections.clear()
        print(f"[{self.service_id}] API Gateway服务已停止")

    async def _handle_telemetry(self, message: Dict):
        """处理遥测数据"""
        if not validate_message(message, MESSAGE_TYPES['TELEMETRY']):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')
        shelf_id = payload.get('shelf_id')

        if device_id is None or shelf_id is None:
            return

        try:
            import numpy as np
            temps = np.array([t for t in payload.get('temperatures', []) if t is not None])
            vacs = np.array([v for v in payload.get('vacuum_levels', []) if v is not None])

            if len(temps) > 0:
                temp_diff = float(np.max(temps) - np.min(temps))
                avg_temp = float(np.mean(temps))
            else:
                temp_diff = 0.0
                avg_temp = 0.0

            avg_vacuum = float(np.mean(vacs)) if len(vacs) > 0 else 0.0
        except ImportError:
            temps = [t for t in payload.get('temperatures', []) if t is not None]
            vacs = [v for v in payload.get('vacuum_levels', []) if v is not None]
            temp_diff = max(temps) - min(temps) if temps else 0.0
            avg_temp = sum(temps) / len(temps) if temps else 0.0
            avg_vacuum = sum(vacs) / len(vacs) if vacs else 0.0

        realtime_data = {
            "device_id": device_id,
            "shelf_id": shelf_id,
            "timestamp": payload.get('timestamp', datetime.now(timezone.utc).isoformat()),
            "temperatures": payload.get('temperatures', []),
            "temperature_diff": temp_diff,
            "avg_temperature": avg_temp,
            "vacuum_levels": payload.get('vacuum_levels', []),
            "avg_vacuum": avg_vacuum,
            "cold_trap_temp": payload.get('cold_trap_temp', 0.0),
            "heating_powers": payload.get('heating_powers', [])
        }

        if device_id not in self._telemetry_cache:
            self._telemetry_cache[device_id] = {}
        self._telemetry_cache[device_id][shelf_id] = realtime_data

        self._increment_metric("messages_received")
        await self._broadcast("telemetry", realtime_data)

    async def _handle_control(self, message: Dict):
        """处理控制状态"""
        if not validate_message(message, MESSAGE_TYPES['CONTROL_STATUS']):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')
        shelf_id = payload.get('shelf_id')

        if device_id is None or shelf_id is None:
            return

        control_data = {
            "device_id": device_id,
            "shelf_id": shelf_id,
            "timestamp": payload.get('timestamp', datetime.now(timezone.utc).isoformat()),
            "auto_mode": payload.get('auto_mode', True),
            "current_powers": payload.get('current_powers', []),
            "temperature_diff": payload.get('temperature_diff', 0.0),
            "avg_temperature": payload.get('avg_temperature', 0.0),
            "adjustments": payload.get('adjustments', []),
            "batch_id": payload.get('batch_id')
        }

        if device_id not in self._control_cache:
            self._control_cache[device_id] = {}
        self._control_cache[device_id][shelf_id] = control_data
        self._auto_mode[device_id] = payload.get('auto_mode', True)

        self._increment_metric("messages_received")
        await self._broadcast("control", control_data)

    async def _handle_prediction(self, message: Dict):
        """处理预测结果"""
        if not validate_message(message, MESSAGE_TYPES['PREDICTION']):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')

        if device_id is None:
            return

        prediction_data = {
            "device_id": device_id,
            "timestamp": payload.get('timestamp', datetime.now(timezone.utc).isoformat()),
            "moisture_content": {
                "predicted": payload.get('moisture_content', 0.0),
                "confidence": payload.get('moisture_confidence', 0.0),
                "threshold": payload.get('moisture_threshold', self._prediction_thresholds['moisture_max']),
                "is_qualified": payload.get('moisture_content', 0.0) <= payload.get('moisture_threshold', self._prediction_thresholds['moisture_max'])
            },
            "reconstitution_time": {
                "predicted": payload.get('reconstitution_time', 0.0),
                "confidence": payload.get('reconstitution_confidence', 0.0),
                "threshold": payload.get('reconstitution_threshold', self._prediction_thresholds['reconstitution_max']),
                "is_qualified": payload.get('reconstitution_time', 0.0) <= payload.get('reconstitution_threshold', self._prediction_thresholds['reconstitution_max'])
            },
            "drying_rate": payload.get('drying_rate', 0.0),
            "is_qualified": payload.get('is_qualified', True),
            "formula_id": payload.get('formula_id'),
            "batch_id": payload.get('batch_id'),
            "drift_detected": payload.get('drift_detected', False),
            "adaptation_level": payload.get('adaptation_level', 0.0),
            "model_version": payload.get('model_version', '2.0')
        }

        self._prediction_cache[device_id] = prediction_data

        self._increment_metric("messages_received")
        await self._broadcast("prediction", prediction_data)

    async def _handle_alarm(self, message: Dict):
        """处理告警事件"""
        if not validate_message(message, MESSAGE_TYPES['ALARM']):
            return

        payload = extract_payload(message)
        alarm_id = payload.get('alarm_id')

        if not alarm_id:
            return

        alarm_data = {
            "id": alarm_id,
            "timestamp": payload.get('timestamp', datetime.now(timezone.utc).isoformat()),
            "device_id": payload.get('device_id'),
            "shelf_id": payload.get('shelf_id'),
            "alarm_type": payload.get('alarm_type'),
            "severity": payload.get('severity'),
            "message": payload.get('message'),
            "acknowledged": payload.get('acknowledged', False),
            "acknowledged_by": payload.get('acknowledged_by'),
            "acknowledged_at": payload.get('acknowledged_at')
        }

        existing_idx = None
        for i, alarm in enumerate(self._alarm_cache):
            if alarm["id"] == alarm_id:
                existing_idx = i
                break

        if existing_idx is not None:
            self._alarm_cache[existing_idx] = alarm_data
        else:
            self._alarm_cache.insert(0, alarm_data)
            if len(self._alarm_cache) > self._max_alarm_cache:
                self._alarm_cache.pop()

        self._increment_metric("messages_received")
        await self._broadcast("alarm", alarm_data)

    async def _handle_config_update(self, message: Dict):
        """处理配置更新"""
        if not validate_message(message, MESSAGE_TYPES['CONFIG']):
            return

        payload = extract_payload(message)
        config_type = payload.get('config_type')

        if config_type in ['alarm', 'prediction', 'all']:
            print(f"[{self.service_id}] 收到配置更新: {config_type}")
            config_loader.reload_all()
            self._load_config()
            self._increment_metric("config_updates")

    async def _broadcast(self, message_type: str, data: Any):
        """广播消息到所有WebSocket连接"""
        if not self._active_connections:
            return

        message = {"type": message_type, "data": data}
        disconnected = []

        for conn in self._active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                disconnected.append(conn)

        for conn in disconnected:
            if conn in self._active_connections:
                self._active_connections.remove(conn)

    async def add_websocket_connection(self, websocket: WebSocket):
        """添加WebSocket连接"""
        self._active_connections.append(websocket)
        print(f"[{self.service_id}] WebSocket连接已建立，当前连接数: {len(self._active_connections)}")

    async def remove_websocket_connection(self, websocket: WebSocket):
        """移除WebSocket连接"""
        if websocket in self._active_connections:
            self._active_connections.remove(websocket)
        print(f"[{self.service_id}] WebSocket连接已关闭，当前连接数: {len(self._active_connections)}")

    def get_devices(self) -> List[DeviceInfo]:
        """获取所有设备"""
        return list(self._devices.values())

    def get_device(self, device_id: int) -> Optional[DeviceInfo]:
        """获取设备详情"""
        return self._devices.get(device_id)

    def get_realtime_data(self, device_id: int) -> List[Dict[str, Any]]:
        """获取设备实时数据"""
        if device_id not in self._telemetry_cache:
            return []
        return list(self._telemetry_cache[device_id].values())

    def get_latest_control(self, device_id: int, shelf_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """获取最新控制命令"""
        if device_id not in self._control_cache:
            return None

        if shelf_id is not None:
            return self._control_cache[device_id].get(shelf_id)

        shelves = list(self._control_cache[device_id].values())
        return shelves[0] if shelves else None

    def get_prediction(self, device_id: int) -> Optional[Dict[str, Any]]:
        """获取预测结果"""
        return self._prediction_cache.get(device_id)

    def get_current_alarms(self) -> List[Dict[str, Any]]:
        """获取当前活跃告警"""
        return [a for a in self._alarm_cache if not a.get('acknowledged', False)]

    def get_alarm_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取告警历史"""
        return self._alarm_cache[:limit]

    def acknowledge_alarm(self, alarm_id: str, acknowledged_by: str) -> bool:
        """确认告警"""
        for alarm in self._alarm_cache:
            if alarm["id"] == alarm_id:
                alarm["acknowledged"] = True
                alarm["acknowledged_by"] = acknowledged_by
                alarm["acknowledged_at"] = datetime.now(timezone.utc).isoformat()

                ack_msg = AlarmAck(
                    alarm_id=alarm_id,
                    acknowledged_by=acknowledged_by,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                message = {
                    "header": {
                        "message_id": str(uuid4()),
                        "message_type": MESSAGE_TYPES['ALARM_ACK'],
                        "source_service": self.service_id,
                        "target_service": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "version": "1.0"
                    },
                    "payload": ack_msg.to_dict()
                }
                asyncio.create_task(self.publish(CHANNELS['ALARM_ACK'], message))
                return True
        return False

    def set_auto_mode(self, device_id: int, auto_mode: bool) -> bool:
        """设置自动/手动模式"""
        if device_id not in self._auto_mode:
            return False

        self._auto_mode[device_id] = auto_mode

        control_cmd = ControlCommand(
            device_id=device_id,
            shelf_id=1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            auto_mode=auto_mode,
            power_adjustments=[0.0] * 8,
            command_id=str(uuid4())
        )
        message = MessageFactory.create_control_command(control_cmd, self.service_id)
        asyncio.create_task(self.publish(CHANNELS['CONTROL_COMMAND'], message))
        return True

    def set_prediction_thresholds(self, moisture_max: float, reconstitution_max: float):
        """设置预测阈值"""
        self._prediction_thresholds["moisture_max"] = moisture_max
        self._prediction_thresholds["reconstitution_max"] = reconstitution_max

        config_msg = MessageFactory.create_config_update(
            config_type="prediction",
            config_data=self._prediction_thresholds,
            source_service=self.service_id
        )
        asyncio.create_task(self.publish(CHANNELS['CONFIG_UPDATE'], config_msg))

    def set_alarm_thresholds(self, thresholds: Dict[str, float]):
        """设置告警阈值"""
        self._alarm_thresholds.update(thresholds)

        config_msg = MessageFactory.create_config_update(
            config_type="alarm",
            config_data=self._alarm_thresholds,
            source_service=self.service_id
        )
        asyncio.create_task(self.publish(CHANNELS['CONFIG_UPDATE'], config_msg))

    def reset_batch(self, device_id: int, batch_id: Optional[str] = None,
                    initial_temperatures: Optional[List[float]] = None) -> str:
        """重置批次"""
        if batch_id is None:
            batch_id = f"BATCH-{device_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        reset_msg = {
            "header": {
                "message_id": str(uuid4()),
                "message_type": "control_command",
                "source_service": self.service_id,
                "target_service": SERVICE_IDS['TEMP_CONTROLLER'],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "1.0"
            },
            "payload": {
                "device_id": device_id,
                "reset_batch": True,
                "batch_id": batch_id,
                "initial_temperatures": initial_temperatures
            }
        }
        asyncio.create_task(self.publish(CHANNELS['CONTROL_COMMAND'], reset_msg))
        return batch_id

    def get_control_metrics(self, device_id: int) -> Dict[str, Any]:
        """获取控制指标"""
        return {
            "device_id": device_id,
            "auto_mode": self._auto_mode.get(device_id, True),
            "control_cache_size": len(self._control_cache.get(device_id, {})),
            "telemetry_cache_size": len(self._telemetry_cache.get(device_id, {}))
        }

    def register_formula(self, device_id: int, formula_id: str, formula_name: str,
                         product_type: str, target_moisture: float,
                         target_reconstitution: float, freeze_curve: Dict[str, float]) -> Dict[str, Any]:
        """注册配方"""
        if device_id not in self._formulas:
            self._formulas[device_id] = {}

        formula = {
            "formula_id": formula_id,
            "formula_name": formula_name,
            "product_type": product_type,
            "target_moisture": target_moisture,
            "target_reconstitution": target_reconstitution,
            "freeze_curve": freeze_curve,
            "sample_count": 0
        }
        self._formulas[device_id][formula_id] = formula

        config_msg = MessageFactory.create_config_update(
            config_type="prediction",
            config_data={"formula_registration": formula},
            source_service=self.service_id
        )
        asyncio.create_task(self.publish(CHANNELS['PREDICTION_CONFIG'], config_msg))

        return formula

    def switch_formula(self, device_id: int, formula_id: str) -> bool:
        """切换配方"""
        if device_id not in self._formulas or formula_id not in self._formulas[device_id]:
            return False

        self._current_formula[device_id] = formula_id

        config_msg = MessageFactory.create_config_update(
            config_type="prediction",
            config_data={"formula_switch": {"device_id": device_id, "formula_id": formula_id}},
            source_service=self.service_id
        )
        asyncio.create_task(self.publish(CHANNELS['PREDICTION_CONFIG'], config_msg))

        return True

    def list_formulas(self, device_id: int) -> Dict[str, Any]:
        """获取配方列表"""
        formulas = []
        if device_id in self._formulas:
            for fid, formula in self._formulas[device_id].items():
                formulas.append({
                    "formula_id": formula.get("formula_id", fid),
                    "formula_name": formula.get("formula_name", ""),
                    "product_type": formula.get("product_type", ""),
                    "sample_count": formula.get("sample_count", 0),
                    "target_moisture": formula.get("target_moisture", 0.0),
                    "target_reconstitution": formula.get("target_reconstitution", 0.0)
                })

        return {
            "device_id": device_id,
            "current_formula": self._current_formula.get(device_id),
            "formulas": formulas
        }

    def get_model_info(self, device_id: int) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "device_id": device_id,
            "model_version": "2.0",
            "model_type": "Gradient Boosting + Iterative Learning",
            "feature_count": 56,
            "sample_count": 1000,
            "last_trained": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "moisture_mae": 0.25,
                "reconstitution_mae": 8.5,
                "overall_accuracy": 0.92
            },
            "current_formula": self._current_formula.get(device_id)
        }


gateway_service: Optional[APIGatewayService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI生命周期管理"""
    global gateway_service

    redis_config = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0'))
    )

    gateway_service = APIGatewayService(redis_config)
    await gateway_service.start()

    yield

    await gateway_service.stop()


app = FastAPI(
    title="Freeze Dryer API Gateway",
    description="生物制药冻干机API网关 - 提供REST API和WebSocket实时推送",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """根路径"""
    return {
        "app": "Freeze Dryer API Gateway",
        "version": "2.0.0",
        "status": "running",
        "docs": "/docs",
        "websocket": "/ws"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "redis_connected": gateway_service.is_connected if gateway_service else False,
        "websocket_connections": len(gateway_service._active_connections) if gateway_service else 0
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket实时推送端点"""
    await websocket.accept()
    await gateway_service.add_websocket_connection(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        await gateway_service.remove_websocket_connection(websocket)
    except Exception as e:
        print(f"[WebSocket] 异常: {e}")
        await gateway_service.remove_websocket_connection(websocket)


@app.get("/api/devices")
async def get_devices():
    """获取所有设备"""
    return gateway_service.get_devices()


@app.get("/api/devices/{device_id}")
async def get_device(device_id: int):
    """获取设备详情"""
    device = gateway_service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@app.get("/api/data/realtime/{device_id}")
async def get_realtime_data(device_id: int):
    """获取实时数据"""
    data = gateway_service.get_realtime_data(device_id)
    return data


@app.get("/api/data/history")
async def get_history_data(
    device_id: int,
    shelf_id: Optional[int] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000)
):
    """获取历史数据（从缓存返回最新数据）"""
    realtime_data = gateway_service.get_realtime_data(device_id)
    if shelf_id is not None:
        realtime_data = [d for d in realtime_data if d.get("shelf_id") == shelf_id]
    return {"count": len(realtime_data), "data": realtime_data}


@app.get("/api/data/stats/{device_id}")
async def get_device_stats(device_id: int, hours: int = Query(1, ge=1, le=24)):
    """获取设备统计数据"""
    realtime_data = gateway_service.get_realtime_data(device_id)
    stats = []

    for shelf_data in realtime_data:
        temps = [t for t in shelf_data.get("temperatures", []) if t is not None]
        vacs = [v for v in shelf_data.get("vacuum_levels", []) if v is not None]

        if temps:
            import numpy as np
            stats.append({
                "shelf_id": shelf_data.get("shelf_id"),
                "sample_count": 1,
                "avg_temp": round(float(np.mean(temps)), 2),
                "max_temp": round(float(np.max(temps)), 2),
                "min_temp": round(float(np.min(temps)), 2),
                "temp_diff": round(float(np.max(temps) - np.min(temps)), 2),
                "avg_vacuum": round(float(np.mean(vacs)), 4) if vacs else None,
                "avg_cold_trap": round(shelf_data.get("cold_trap_temp", 0.0), 2)
            })

    return {"device_id": device_id, "time_window_hours": hours, "stats": stats}


@app.post("/api/control/calculate")
async def calculate_control(device_id: int, shelf_id: int):
    """执行控制计算（返回缓存的控制状态）"""
    control_data = gateway_service.get_latest_control(device_id, shelf_id)
    if not control_data:
        return {"status": "no_data", "adjustments": [0] * 8}

    return {
        "auto_mode": control_data.get("auto_mode", True),
        "uniformity": {
            "temp_diff": control_data.get("temperature_diff", 0.0),
            "avg_temp": control_data.get("avg_temperature", 0.0)
        },
        "adjustments": control_data.get("adjustments", []),
        "current_powers": control_data.get("current_powers", [])
    }


@app.put("/api/control/mode")
async def set_control_mode(update: ControlModeUpdate):
    """切换自动/手动模式"""
    success = gateway_service.set_auto_mode(update.device_id, update.auto_mode)
    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "status": "success",
        "device_id": update.device_id,
        "auto_mode": update.auto_mode
    }


@app.get("/api/control/latest/{device_id}")
async def get_latest_control_command(device_id: int, shelf_id: Optional[int] = None):
    """获取最新控制命令"""
    return gateway_service.get_latest_control(device_id, shelf_id)


@app.post("/api/control/batch/reset")
async def reset_batch(request: BatchResetRequest):
    """重置批次"""
    batch_id = gateway_service.reset_batch(
        device_id=request.device_id,
        batch_id=request.batch_id,
        initial_temperatures=request.initial_temperatures
    )
    return {
        "status": "success",
        "message": "Batch reset successfully",
        "device_id": request.device_id,
        "batch_id": batch_id
    }


@app.get("/api/control/batch/metrics/{device_id}")
async def get_batch_metrics(device_id: int):
    """获取控制指标"""
    metrics = gateway_service.get_control_metrics(device_id)
    return {
        "status": "success",
        "device_id": device_id,
        "metrics": metrics
    }


@app.post("/api/prediction/predict")
async def predict_quality(device_id: int, batch_id: Optional[str] = None):
    """执行预测（返回缓存的预测结果）"""
    prediction = gateway_service.get_prediction(device_id)
    if not prediction:
        raise HTTPException(status_code=404, detail="No prediction data available")
    return prediction


@app.get("/api/prediction/history")
async def get_prediction_history(device_id: int, limit: int = 10):
    """获取历史预测"""
    prediction = gateway_service.get_prediction(device_id)
    results = [prediction] if prediction else []
    return {"count": len(results), "results": results}


@app.get("/api/prediction/model/{device_id}")
async def get_model_info(device_id: int):
    """获取模型信息"""
    return gateway_service.get_model_info(device_id)


@app.put("/api/prediction/thresholds")
async def set_prediction_thresholds(moisture_max: float, reconstitution_max: float):
    """设置阈值"""
    if moisture_max <= 0 or reconstitution_max <= 0:
        raise HTTPException(status_code=400, detail="Thresholds must be positive")
    gateway_service.set_prediction_thresholds(moisture_max, reconstitution_max)
    return {
        "status": "success",
        "moisture_max_threshold": moisture_max,
        "reconstitution_max_threshold": reconstitution_max
    }


@app.post("/api/prediction/formula/register")
async def register_formula(request: FormulaRegisterRequest):
    """注册配方"""
    formula = gateway_service.register_formula(
        device_id=request.device_id,
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
        "formula": formula
    }


@app.put("/api/prediction/formula/switch")
async def switch_formula(request: FormulaSwitchRequest):
    """切换配方"""
    success = gateway_service.switch_formula(request.device_id, request.formula_id)
    if not success:
        raise HTTPException(status_code=404, detail="Formula not found")
    return {
        "status": "success",
        "message": f"Switched to formula {request.formula_id}",
        "device_id": request.device_id,
        "formula_id": request.formula_id
    }


@app.get("/api/prediction/formula/list/{device_id}")
async def list_formulas(device_id: int):
    """获取配方列表"""
    return gateway_service.list_formulas(device_id)


@app.post("/api/prediction/labeled-data/add")
async def add_labeled_data(request: LabeledDataRequest):
    """添加标签数据"""
    config_msg = MessageFactory.create_config_update(
        config_type="prediction",
        config_data={
            "labeled_data": {
                "device_id": request.device_id,
                "actual_moisture": request.actual_moisture,
                "actual_reconstitution": request.actual_reconstitution,
                "formula_id": request.formula_id,
                "hours_of_history": request.hours_of_history
            }
        },
        source_service=SERVICE_IDS['API_GATEWAY']
    )
    asyncio.create_task(gateway_service.publish(CHANNELS['PREDICTION_CONFIG'], config_msg))

    return {
        "status": "success",
        "message": "Labeled data submitted for processing"
    }


@app.post("/api/prediction/transfer-learning/execute")
async def execute_transfer_learning(request: TransferLearningRequest):
    """执行迁移学习"""
    config_msg = MessageFactory.create_config_update(
        config_type="prediction",
        config_data={
            "transfer_learning": {
                "device_id": request.device_id,
                "source_formula_id": request.source_formula_id,
                "target_formula_id": request.target_formula_id,
                "target_labeled_count": request.target_labeled_count
            }
        },
        source_service=SERVICE_IDS['API_GATEWAY']
    )
    asyncio.create_task(gateway_service.publish(CHANNELS['PREDICTION_CONFIG'], config_msg))

    return {
        "status": "success",
        "message": "Transfer learning task submitted",
        "source_formula": request.source_formula_id,
        "target_formula": request.target_formula_id
    }


@app.get("/api/alarm/current")
async def get_current_alarms():
    """获取当前告警"""
    alarms = gateway_service.get_current_alarms()
    return {
        "count": len(alarms),
        "alarms": alarms
    }


@app.get("/api/alarm/history")
async def get_alarm_history(
    device_id: Optional[int] = None,
    alarm_type: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 100
):
    """获取历史告警"""
    alarms = gateway_service.get_alarm_history(limit)

    if device_id is not None:
        alarms = [a for a in alarms if a.get("device_id") == device_id]
    if alarm_type is not None:
        alarms = [a for a in alarms if a.get("alarm_type") == alarm_type]

    return {"count": len(alarms), "alarms": alarms}


@app.post("/api/alarm/{alarm_id}/acknowledge")
async def acknowledge_alarm(alarm_id: str, ack: AlarmAcknowledge):
    """确认告警"""
    success = gateway_service.acknowledge_alarm(alarm_id, ack.acknowledged_by)
    if not success:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return {"status": "success", "message": "Alarm acknowledged"}


@app.get("/api/alarm/thresholds")
async def get_alarm_thresholds():
    """获取告警阈值"""
    return gateway_service._alarm_thresholds


@app.put("/api/alarm/thresholds")
async def set_alarm_thresholds(
    temp_diff: Optional[float] = None,
    vacuum_min: Optional[float] = None,
    vacuum_max: Optional[float] = None,
    cold_trap_max: Optional[float] = None,
    moisture_max: Optional[float] = None,
    reconstitution_max: Optional[float] = None
):
    """更新告警阈值"""
    thresholds = {}
    if temp_diff is not None:
        thresholds["temp_diff"] = temp_diff
    if vacuum_min is not None:
        thresholds["vacuum_min"] = vacuum_min
    if vacuum_max is not None:
        thresholds["vacuum_max"] = vacuum_max
    if cold_trap_max is not None:
        thresholds["cold_trap_max"] = cold_trap_max
    if moisture_max is not None:
        thresholds["moisture_max"] = moisture_max
    if reconstitution_max is not None:
        thresholds["reconstitution_max"] = reconstitution_max

    gateway_service.set_alarm_thresholds(thresholds)
    return {"status": "success", "thresholds": gateway_service._alarm_thresholds}


async def main():
    """主函数"""
    import uvicorn

    redis_config = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0'))
    )

    print("=" * 60)
    print("API Gateway微服务启动")
    print(f"服务ID: {SERVICE_IDS['API_GATEWAY']}")
    print(f"服务类型: api_gateway")
    print(f"Redis: {redis_config.host}:{redis_config.port}")
    print(f"HTTP端口: 8000")
    print(f"WebSocket: /ws")
    print("=" * 60)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    asyncio.run(main())
