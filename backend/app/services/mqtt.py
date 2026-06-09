"""
MQTT告警推送服务
将告警信息推送至MES系统
"""

import json
import asyncio
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

try:
    import paho.mqtt.client as mqtt
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False


@dataclass
class MQTTConfig:
    broker: str = "localhost"
    port: int = 1883
    topic: str = "pharmacy/mes/alarm"
    username: Optional[str] = None
    password: Optional[str] = None
    keepalive: int = 60
    qos: int = 1


class MQTTAlarmPublisher:
    def __init__(self, config: MQTTConfig = None):
        self.config = config or MQTTConfig()
        self.client = None
        self.connected = False
        self._message_queue = asyncio.Queue()
        self._publish_task = None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print(f"MQTT连接成功: {self.config.broker}:{self.config.port}")
        else:
            self.connected = False
            print(f"MQTT连接失败，错误码: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        print(f"MQTT断开连接，错误码: {rc}")

    def _on_publish(self, client, userdata, mid):
        pass

    async def connect(self):
        if not HAS_PAHO:
            print("警告: paho-mqtt未安装，MQTT功能不可用")
            return False
            
        try:
            self.client = mqtt.Client(
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
            print(f"MQTT连接异常: {e}")
            return False

    async def disconnect(self):
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

    async def _process_queue(self):
        while True:
            try:
                message = await self._message_queue.get()
                await self._publish_message(message)
                self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"MQTT消息处理异常: {e}")
                await asyncio.sleep(1)

    async def _publish_message(self, message: dict):
        if not HAS_PAHO or not self.connected or not self.client:
            print(f"MQTT未连接，消息缓存: {json.dumps(message, ensure_ascii=False)[:50]}...")
            return False
        
        try:
            payload = json.dumps(message, ensure_ascii=False)
            result = self.client.publish(
                self.config.topic,
                payload=payload,
                qos=self.config.qos,
                retain=False
            )
            
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"MQTT消息发布失败: {result.rc}")
                return False
            
            await asyncio.sleep(0.01)
            return True
        except Exception as e:
            print(f"MQTT消息发布异常: {e}")
            return False

    async def publish_alarm(self, alarm_event) -> bool:
        message = {
            "alarm_id": str(alarm_event.id),
            "timestamp": alarm_event.timestamp.isoformat(),
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

    async def publish_batch_alarms(self, alarm_events: list) -> list:
        results = []
        for alarm in alarm_events:
            result = await self.publish_alarm(alarm)
            results.append(result)
        return results

    def is_connected(self) -> bool:
        return self.connected

    def update_config(self, config: MQTTConfig):
        self.config = config
        if self.connected:
            asyncio.create_task(self.reconnect())

    async def reconnect(self):
        await self.disconnect()
        await asyncio.sleep(1)
        await self.connect()


class MQTTService:
    def __init__(self, config: MQTTConfig = None):
        self.publisher = MQTTAlarmPublisher(config)
        self._started = False

    async def start(self):
        if not self._started:
            await self.publisher.connect()
            self._started = True

    async def stop(self):
        if self._started:
            await self.publisher.disconnect()
            self._started = False

    async def send_alarm(self, alarm_event) -> bool:
        if not self._started:
            await self.start()
        return await self.publisher.publish_alarm(alarm_event)

    async def send_alarms(self, alarm_events: list) -> list:
        if not self._started:
            await self.start()
        return await self.publisher.publish_batch_alarms(alarm_events)

    def is_available(self) -> bool:
        return HAS_PAHO and self.publisher.is_connected()
