"""
告警发布微服务
订阅遥测数据和预测结果，检测4类告警：
- 温度温差超限
- 真空度异常
- 冷阱温度过高
- 质量预测不合格

发布告警事件到Redis，并通过MQTT推送到MES系统
"""

import asyncio
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
from collections import deque
from dataclasses import dataclass
from uuid import UUID, uuid4

try:
    import numpy as np
    HAS_NUMPY: bool = True
except ImportError:
    np = None
    HAS_NUMPY: bool = False

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import (
    MicroserviceBase, RedisConfig,
    CHANNELS, SERVICE_IDS,
    AlarmEvent, AlarmAck,
    MessageFactory,
    validate_message, extract_payload,
    config_loader, AlarmConfig
)


@dataclass
class AlarmThresholds:
    """告警阈值配置"""
    temp_diff: float = 1.0
    temp_diff_critical_multiplier: float = 1.5
    vacuum_min: float = 0.1
    vacuum_max: float = 100.0
    cold_trap_max: float = -50.0
    cold_trap_min: float = -85.0
    moisture_max: float = 3.0
    reconstitution_max: float = 120.0
    min_confidence: float = 0.7


@dataclass
class MQTTConfig:
    """MQTT配置"""
    broker: str = "localhost"
    port: int = 1883
    topic: str = "pharmacy/mes/alarm"
    username: Optional[str] = None
    password: Optional[str] = None
    keepalive: int = 60
    qos: int = 1


class AlarmDetector:
    """告警检测器"""

    def __init__(self, thresholds: AlarmThresholds = None):
        self.thresholds: AlarmThresholds = thresholds or AlarmThresholds()
        self.active_alarms: Dict[str, AlarmEvent] = {}
        self.alarm_history: deque = deque(maxlen=1000)
        self.cooldown_period: Dict[str, datetime] = {}
        self.cooldown_seconds: int = 30
        self.history_cache_size: int = 1000
        self.active_cache_size: int = 100

    def _in_cooldown(self, alarm_key: str) -> bool:
        """检查告警是否在冷却期内"""
        if alarm_key in self.cooldown_period:
            if datetime.now(timezone.utc) < self.cooldown_period[alarm_key]:
                return True
        return False

    def _set_cooldown(self, alarm_key: str) -> None:
        """设置告警冷却期"""
        self.cooldown_period[alarm_key] = datetime.now(timezone.utc) + \
            timedelta(seconds=self.cooldown_seconds)

    def check_temperature_diff(self, device_id: int, shelf_id: int,
                               temperatures: List[float]) -> Optional[AlarmEvent]:
        """检测温度温差超限"""
        if not temperatures:
            return None

        temp_diff: float = max(temperatures) - min(temperatures)
        alarm_key: str = f"temp_diff_{device_id}_{shelf_id}"

        critical_threshold: float = self.thresholds.temp_diff * self.thresholds.temp_diff_critical_multiplier

        if temp_diff > self.thresholds.temp_diff and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            severity: str = "warning" if temp_diff < critical_threshold else "critical"
            return AlarmEvent(
                alarm_id=str(uuid4()),
                device_id=device_id,
                shelf_id=shelf_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                alarm_type="temperature_diff",
                severity=severity,
                message=f"设备{device_id}搁板{shelf_id}温度温差超限: {temp_diff:.2f}℃ > {self.thresholds.temp_diff}℃"
            )
        return None

    def check_vacuum(self, device_id: int, shelf_id: int,
                     vacuum_levels: List[float]) -> Optional[AlarmEvent]:
        """检测真空度异常"""
        if not vacuum_levels:
            return None

        if HAS_NUMPY:
            avg_vacuum: float = float(np.mean(vacuum_levels))
        else:
            avg_vacuum: float = sum(vacuum_levels) / len(vacuum_levels)
        alarm_key: str = f"vacuum_{device_id}_{shelf_id}"

        if avg_vacuum < self.thresholds.vacuum_min and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                alarm_id=str(uuid4()),
                device_id=device_id,
                shelf_id=shelf_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                alarm_type="vacuum_abnormal",
                severity="critical",
                message=f"设备{device_id}搁板{shelf_id}真空度过低: {avg_vacuum:.4f}Pa < {self.thresholds.vacuum_min}Pa"
            )
        elif avg_vacuum > self.thresholds.vacuum_max and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                alarm_id=str(uuid4()),
                device_id=device_id,
                shelf_id=shelf_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                alarm_type="vacuum_abnormal",
                severity="warning",
                message=f"设备{device_id}搁板{shelf_id}真空度过高: {avg_vacuum:.4f}Pa > {self.thresholds.vacuum_max}Pa"
            )
        return None

    def check_cold_trap(self, device_id: int, shelf_id: int,
                        cold_trap_temp: float) -> Optional[AlarmEvent]:
        """检测冷阱温度过高"""
        alarm_key: str = f"cold_trap_{device_id}"

        if cold_trap_temp > self.thresholds.cold_trap_max and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                alarm_id=str(uuid4()),
                device_id=device_id,
                shelf_id=shelf_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                alarm_type="cold_trap_high",
                severity="critical",
                message=f"设备{device_id}冷阱温度过高: {cold_trap_temp:.2f}℃ > {self.thresholds.cold_trap_max}℃"
            )
        elif cold_trap_temp < self.thresholds.cold_trap_min and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                alarm_id=str(uuid4()),
                device_id=device_id,
                shelf_id=shelf_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                alarm_type="cold_trap_low",
                severity="warning",
                message=f"设备{device_id}冷阱温度过低: {cold_trap_temp:.2f}℃ < {self.thresholds.cold_trap_min}℃"
            )
        return None

    def check_quality_prediction(self, device_id: int,
                                 is_qualified: bool,
                                 moisture: float,
                                 reconstitution: float,
                                 moisture_confidence: float = 1.0,
                                 reconstitution_confidence: float = 1.0) -> Optional[AlarmEvent]:
        """检测质量预测不合格"""
        alarm_key: str = f"quality_{device_id}"

        issues: List[str] = []
        min_confidence: float = min(moisture_confidence, reconstitution_confidence)

        if not is_qualified and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)

            if moisture > self.thresholds.moisture_max:
                issues.append(f"水分含量{moisture:.2f}% > {self.thresholds.moisture_max}%")
            if reconstitution > self.thresholds.reconstitution_max:
                issues.append(f"复溶时间{reconstitution:.1f}s > {self.thresholds.reconstitution_max}s")
            if min_confidence < self.thresholds.min_confidence:
                issues.append(f"预测置信度过低: {min_confidence:.2f} < {self.thresholds.min_confidence}")

            if not issues:
                issues.append("产品质量预测不合格")

            return AlarmEvent(
                alarm_id=str(uuid4()),
                device_id=device_id,
                shelf_id=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
                alarm_type="quality_prediction",
                severity="critical",
                message=f"设备{device_id}产品质量预测不合格: {'; '.join(issues)}"
            )
        return None

    def process_telemetry(self, device_id: int, shelf_id: int,
                          temperatures: List[float],
                          vacuum_levels: List[float],
                          cold_trap_temp: float) -> List[AlarmEvent]:
        """处理遥测数据，检测所有相关告警"""
        alarms: List[AlarmEvent] = []

        alarm: Optional[AlarmEvent] = self.check_temperature_diff(device_id, shelf_id, temperatures)
        if alarm:
            alarms.append(alarm)

        alarm = self.check_vacuum(device_id, shelf_id, vacuum_levels)
        if alarm:
            alarms.append(alarm)

        alarm = self.check_cold_trap(device_id, shelf_id, cold_trap_temp)
        if alarm:
            alarms.append(alarm)

        for alarm in alarms:
            key: str = f"{alarm.alarm_type}_{alarm.device_id}_{alarm.shelf_id or 'all'}"
            self.active_alarms[key] = alarm
            self.alarm_history.append(alarm)

        return alarms

    def acknowledge_alarm(self, alarm_id: str, acknowledged_by: str) -> bool:
        """确认告警"""
        for key, alarm in list(self.active_alarms.items()):
            if alarm.alarm_id == alarm_id:
                alarm.acknowledged = True
                alarm.acknowledged_by = acknowledged_by
                alarm.acknowledged_at = datetime.now(timezone.utc).isoformat()
                del self.active_alarms[key]
                return True
        return False

    def get_active_alarms(self) -> List[AlarmEvent]:
        """获取当前活跃告警"""
        return [a for a in self.active_alarms.values() if not a.acknowledged]

    def get_alarm_history(self, limit: int = 100) -> List[AlarmEvent]:
        """获取告警历史"""
        return list(self.alarm_history)[-limit:]

    def update_thresholds(self, thresholds: AlarmThresholds) -> None:
        """更新告警阈值"""
        self.thresholds = thresholds

    def update_config(self, alarm_config: AlarmConfig) -> None:
        """从AlarmConfig更新配置"""
        self.cooldown_seconds = alarm_config.global_config.get('cooldown_seconds', 30)
        self.history_cache_size = alarm_config.global_config.get('history_cache_size', 1000)
        self.active_cache_size = alarm_config.global_config.get('active_cache_size', 100)
        self.alarm_history = deque(maxlen=self.history_cache_size)


class MQTTAlarmPublisher:
    """MQTT告警推送器"""

    def __init__(self, config: MQTTConfig = None):
        self.config: MQTTConfig = config or MQTTConfig()
        self.client: Optional[any] = None
        self.connected: bool = False
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._publish_task: Optional[asyncio.Task] = None

        try:
            import paho.mqtt.client as mqtt
            self.mqtt = mqtt
            self.HAS_PAHO: bool = True
        except ImportError:
            self.mqtt = None
            self.HAS_PAHO: bool = False

    def _on_connect(self, client, userdata, flags, rc) -> None:
        """MQTT连接回调"""
        if rc == 0:
            self.connected = True
            print(f"[MQTT] 连接成功: {self.config.broker}:{self.config.port}")
        else:
            self.connected = False
            print(f"[MQTT] 连接失败，错误码: {rc}")

    def _on_disconnect(self, client, userdata, rc) -> None:
        """MQTT断开连接回调"""
        self.connected = False
        print(f"[MQTT] 断开连接，错误码: {rc}")

    def _on_publish(self, client, userdata, mid) -> None:
        """MQTT发布回调"""
        pass

    async def connect(self) -> bool:
        """连接MQTT broker"""
        if not self.HAS_PAHO:
            print("[MQTT] 警告: paho-mqtt未安装，MQTT功能不可用")
            return False

        try:
            self.client = self.mqtt.Client(
                client_id=f"freeze_dryer_alarm_{datetime.now().timestamp()}",
                clean_session=True
            )

            if self.config.username and self.config.password:
                self.client.username_pw_set(
                    self.config.username,
                    self.config.password
                )

            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_publish = self._on_publish

            self.client.connect_async(
                self.config.broker,
                self.config.port,
                self.config.keepalive
            )

            self.client.loop_start()

            for _ in range(10):
                if self.connected:
                    break
                await asyncio.sleep(0.5)

            if self.connected:
                self._publish_task = asyncio.create_task(self._process_queue())

            return self.connected
        except Exception as e:
            print(f"[MQTT] 连接异常: {e}")
            return False

    async def disconnect(self) -> None:
        """断开MQTT连接"""
        if self._publish_task:
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                pass

        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False

    async def _process_queue(self) -> None:
        """处理消息队列"""
        while True:
            try:
                message: dict = await self._message_queue.get()
                await self._publish_message(message)
                self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MQTT] 消息处理异常: {e}")
                await asyncio.sleep(1)

    async def _publish_message(self, message: dict) -> bool:
        """发布单条消息"""
        if not self.HAS_PAHO or not self.connected or not self.client:
            print(f"[MQTT] 未连接，消息缓存: {json.dumps(message, ensure_ascii=False)[:50]}...")
            return False

        try:
            payload: str = json.dumps(message, ensure_ascii=False)
            result = self.client.publish(
                self.config.topic,
                payload=payload,
                qos=self.config.qos,
                retain=False
            )

            if result.rc != self.mqtt.MQTT_ERR_SUCCESS:
                print(f"[MQTT] 消息发布失败: {result.rc}")
                return False

            await asyncio.sleep(0.01)
            return True
        except Exception as e:
            print(f"[MQTT] 消息发布异常: {e}")
            return False

    async def publish_alarm(self, alarm_event: AlarmEvent) -> bool:
        """发布告警到MQTT"""
        message: dict = {
            "alarm_id": alarm_event.alarm_id,
            "timestamp": alarm_event.timestamp,
            "device_id": alarm_event.device_id,
            "shelf_id": alarm_event.shelf_id,
            "alarm_type": alarm_event.alarm_type,
            "severity": alarm_event.severity,
            "message": alarm_event.message,
            "source": "freeze_dryer_control_system",
            "version": "1.0"
        }

        await self._message_queue.put(message)
        return True

    async def publish_batch_alarms(self, alarm_events: List[AlarmEvent]) -> List[bool]:
        """批量发布告警"""
        results: List[bool] = []
        for alarm in alarm_events:
            result: bool = await self.publish_alarm(alarm)
            results.append(result)
        return results

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self.connected

    def update_config(self, config: MQTTConfig) -> None:
        """更新MQTT配置"""
        self.config = config
        if self.connected:
            asyncio.create_task(self.reconnect())

    async def reconnect(self) -> None:
        """重连MQTT"""
        await self.disconnect()
        await asyncio.sleep(1)
        await self.connect()


class AlarmPublisherService(MicroserviceBase):
    """告警发布微服务"""

    def __init__(self, redis_config: RedisConfig = None):
        super().__init__(
            service_id=SERVICE_IDS['ALARM_PUBLISHER'],
            service_type="alarm_management",
            redis_config=redis_config
        )

        self._detector: Optional[AlarmDetector] = None
        self._mqtt_publisher: Optional[MQTTAlarmPublisher] = None
        self._check_interval: int = 10
        self._num_devices: int = 10
        self._num_shelves: int = 5

        self._latest_telemetry: Dict[str, dict] = {}
        self._latest_predictions: Dict[int, dict] = {}
        self._alarm_check_task: Optional[asyncio.Task] = None

        self._load_config()

    def _load_config(self) -> None:
        """加载告警配置"""
        alarm_config: AlarmConfig = config_loader.load_alarm_config()

        thresholds: AlarmThresholds = AlarmThresholds(
            temp_diff=alarm_config.temperature.get('diff_threshold', 1.0),
            temp_diff_critical_multiplier=alarm_config.temperature.get('critical_multiplier', 1.5),
            vacuum_min=alarm_config.vacuum.get('min_threshold', 0.1),
            vacuum_max=alarm_config.vacuum.get('max_threshold', 100.0),
            cold_trap_max=alarm_config.cold_trap.get('max_threshold', -50.0),
            cold_trap_min=alarm_config.cold_trap.get('min_threshold', -85.0),
            moisture_max=alarm_config.quality.get('moisture_max', 3.0),
            reconstitution_max=alarm_config.quality.get('reconstitution_max', 120.0),
            min_confidence=alarm_config.quality.get('min_confidence', 0.7)
        )

        if self._detector is None:
            self._detector = AlarmDetector(thresholds)
        else:
            self._detector.update_thresholds(thresholds)

        self._detector.update_config(alarm_config)

        mqtt_config_data: dict = alarm_config.mqtt_publisher
        mqtt_config: MQTTConfig = MQTTConfig(
            topic=mqtt_config_data.get('topic', 'pharmacy/mes/alarm'),
            qos=mqtt_config_data.get('qos', 1)
        )

        if self._mqtt_publisher is None:
            self._mqtt_publisher = MQTTAlarmPublisher(mqtt_config)
        else:
            self._mqtt_publisher.update_config(mqtt_config)

        self._check_interval = alarm_config.global_config.get('check_interval', 10)

        print(f"[{self.service_id}] 告警配置已加载")

    async def _subscribe_channels(self) -> None:
        """订阅Redis频道"""
        await self.subscribe(CHANNELS['TELEMETRY_RAW'], self._handle_telemetry)
        await self.subscribe(CHANNELS['PREDICTION_RESULT'], self._handle_prediction)
        await self.subscribe(CHANNELS['ALARM_ACK'], self._handle_alarm_ack)
        await self.subscribe(CHANNELS['CONFIG_UPDATE'], self._handle_config_update)

    async def _on_start(self) -> None:
        """服务启动时执行"""
        print(f"[{self.service_id}] 启动告警检测服务...")

        if self._mqtt_publisher:
            await self._mqtt_publisher.connect()

        self._alarm_check_task = asyncio.create_task(self._alarm_check_loop())
        self._sub_tasks.append(self._alarm_check_task)

        print(f"[{self.service_id}] 告警检测服务已启动")
        print(f"[{self.service_id}] 检测间隔: {self._check_interval}s")
        print(f"[{self.service_id}] 监控设备数: {self._num_devices}")

    async def _on_stop(self) -> None:
        """服务停止时执行"""
        print(f"[{self.service_id}] 停止告警检测服务...")

        if self._mqtt_publisher:
            await self._mqtt_publisher.disconnect()

        print(f"[{self.service_id}] 告警检测服务已停止")

    async def _handle_telemetry(self, message: dict) -> None:
        """处理遥测数据"""
        if not validate_message(message, 'telemetry'):
            return

        payload: dict = extract_payload(message)
        device_id: int = payload.get('device_id')
        shelf_id: int = payload.get('shelf_id')

        if device_id is None or shelf_id is None:
            return

        key: str = f"{device_id}_{shelf_id}"
        self._latest_telemetry[key] = payload

        self._increment_metric("messages_received")

        alarms: List[AlarmEvent] = self._detector.process_telemetry(
            device_id=device_id,
            shelf_id=shelf_id,
            temperatures=payload.get('temperatures', []),
            vacuum_levels=payload.get('vacuum_levels', []),
            cold_trap_temp=payload.get('cold_trap_temp', -70.0)
        )

        for alarm in alarms:
            await self._publish_alarm(alarm)

    async def _handle_prediction(self, message: dict) -> None:
        """处理预测结果"""
        if not validate_message(message, 'prediction'):
            return

        payload: dict = extract_payload(message)
        device_id: int = payload.get('device_id')

        if device_id is None:
            return

        self._latest_predictions[device_id] = payload
        self._increment_metric("messages_received")

        is_qualified: bool = payload.get('is_qualified', True)
        moisture: float = payload.get('moisture_content', 0.0)
        reconstitution: float = payload.get('reconstitution_time', 0.0)
        moisture_confidence: float = payload.get('moisture_confidence', 1.0)
        reconstitution_confidence: float = payload.get('reconstitution_confidence', 1.0)

        alarm: Optional[AlarmEvent] = self._detector.check_quality_prediction(
            device_id=device_id,
            is_qualified=is_qualified,
            moisture=moisture,
            reconstitution=reconstitution,
            moisture_confidence=moisture_confidence,
            reconstitution_confidence=reconstitution_confidence
        )

        if alarm:
            key: str = f"{alarm.alarm_type}_{alarm.device_id}_{alarm.shelf_id or 'all'}"
            self._detector.active_alarms[key] = alarm
            self._detector.alarm_history.append(alarm)
            await self._publish_alarm(alarm)

    async def _handle_alarm_ack(self, message: dict) -> None:
        """处理告警确认"""
        if not validate_message(message, 'alarm_ack'):
            return

        payload: dict = extract_payload(message)
        alarm_id: str = payload.get('alarm_id')
        acknowledged_by: str = payload.get('acknowledged_by', 'system')

        if not alarm_id:
            return

        success: bool = self._detector.acknowledge_alarm(alarm_id, acknowledged_by)
        self._increment_metric("messages_received")

        if success:
            print(f"[{self.service_id}] 告警已确认: {alarm_id} by {acknowledged_by}")
            self._increment_metric("alarms_acknowledged")
        else:
            print(f"[{self.service_id}] 告警确认失败: {alarm_id} 未找到")

    async def _handle_config_update(self, message: dict) -> None:
        """处理配置更新"""
        if not validate_message(message, 'config'):
            return

        payload: dict = extract_payload(message)
        config_type: str = payload.get('config_type')

        if config_type == 'alarm' or config_type == 'all':
            print(f"[{self.service_id}] 收到告警配置更新通知")
            config_loader.reload_all()
            self._load_config()
            self._increment_metric("config_updates")

    async def _alarm_check_loop(self) -> None:
        """告警检测循环 - 每10秒执行一次"""
        while self._running:
            try:
                cycle_start: float = asyncio.get_event_loop().time()

                alarms_count: int = 0

                for device_id in range(1, self._num_devices + 1):
                    for shelf_id in range(1, self._num_shelves + 1):
                        key: str = f"{device_id}_{shelf_id}"
                        telemetry: Optional[dict] = self._latest_telemetry.get(key)

                        if telemetry:
                            alarms: List[AlarmEvent] = self._detector.process_telemetry(
                                device_id=device_id,
                                shelf_id=shelf_id,
                                temperatures=telemetry.get('temperatures', []),
                                vacuum_levels=telemetry.get('vacuum_levels', []),
                                cold_trap_temp=telemetry.get('cold_trap_temp', -70.0)
                            )

                            for alarm in alarms:
                                await self._publish_alarm(alarm)
                                alarms_count += 1

                    prediction: Optional[dict] = self._latest_predictions.get(device_id)
                    if prediction:
                        is_qualified: bool = prediction.get('is_qualified', True)
                        moisture: float = prediction.get('moisture_content', 0.0)
                        reconstitution: float = prediction.get('reconstitution_time', 0.0)
                        moisture_confidence: float = prediction.get('moisture_confidence', 1.0)
                        reconstitution_confidence: float = prediction.get('reconstitution_confidence', 1.0)

                        alarm: Optional[AlarmEvent] = self._detector.check_quality_prediction(
                            device_id=device_id,
                            is_qualified=is_qualified,
                            moisture=moisture,
                            reconstitution=reconstitution,
                            moisture_confidence=moisture_confidence,
                            reconstitution_confidence=reconstitution_confidence
                        )

                        if alarm:
                            key: str = f"{alarm.alarm_type}_{alarm.device_id}_{alarm.shelf_id or 'all'}"
                            self._detector.active_alarms[key] = alarm
                            self._detector.alarm_history.append(alarm)
                            await self._publish_alarm(alarm)
                            alarms_count += 1

                if alarms_count > 0:
                    print(f"[{self.service_id}] 本轮检测产生 {alarms_count} 条告警")

                self._metrics["active_alarms"] = len(self._detector.get_active_alarms())
                self._metrics["total_alarms"] = len(self._detector.alarm_history)

                cycle_duration: float = asyncio.get_event_loop().time() - cycle_start
                sleep_time: float = max(0, self._check_interval - cycle_duration)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                print(f"[{self.service_id}] 告警检测循环异常: {e}")
                self._increment_metric("errors")
                await asyncio.sleep(self._check_interval)

    async def _publish_alarm(self, alarm: AlarmEvent) -> None:
        """发布告警事件"""
        message: dict = MessageFactory.create_alarm(alarm, self.service_id)

        success: bool = await self.publish(CHANNELS['ALARM_EVENT'], message)
        if success:
            self._increment_metric("messages_published")
            self._increment_metric("alarms_published")

        if self._mqtt_publisher:
            await self._mqtt_publisher.publish_alarm(alarm)
            self._increment_metric("alarms_mqtt_published")

        specific_channel: str = f"{CHANNELS['ALARM_EVENT']}:{alarm.device_id}"
        await self.publish(specific_channel, message)

        print(f"[{self.service_id}] 告警发布: {alarm.alarm_type} "
              f"设备{alarm.device_id} "
              f"[{alarm.severity}] {alarm.message[:60]}...")


async def main() -> None:
    """主函数"""
    redis_config: RedisConfig = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0'))
    )

    service: AlarmPublisherService = AlarmPublisherService(redis_config)

    print("=" * 60)
    print("告警发布微服务启动")
    print(f"服务ID: {service.service_id}")
    print(f"服务类型: {service.service_type}")
    print(f"Redis: {redis_config.host}:{redis_config.port}")
    print(f"检测间隔: {service._check_interval}s")
    print(f"监控设备数: {service._num_devices}")
    print(f"监控搁板数: {service._num_shelves}")
    print("=" * 60)

    try:
        await service.start()

        while True:
            await asyncio.sleep(1)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n\n正在停止服务...")
        await service.stop()
        print("服务已安全退出")
    except Exception as e:
        print(f"服务异常: {e}")
        await service.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
