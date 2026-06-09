"""
Redis客户端基类
提供异步Redis连接、发布、订阅功能
"""

import asyncio
import json
import time
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass

try:
    import redis.asyncio as redis_async
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

from .redis_channels import CHANNELS
from .message_protocol import serialize_message, deserialize_message


@dataclass
class RedisConfig:
    """Redis配置"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 10.0
    retry_on_timeout: bool = True
    max_connections: int = 50


class RedisClientBase:
    """Redis客户端基类"""

    def __init__(self, service_id: str, config: RedisConfig = None):
        self.service_id = service_id
        self.config = config or RedisConfig()
        self._client: Optional[redis_async.Redis] = None
        self._pubsub: Optional[redis_async.client.PubSub] = None
        self._sub_tasks: List[asyncio.Task] = []
        self._message_handlers: Dict[str, Callable] = {}
        self._is_connected = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10

    async def connect(self) -> bool:
        """连接Redis"""
        if not HAS_REDIS:
            print(f"[{self.service_id}] 警告: redis-py未安装")
            return False

        try:
            self._client = redis_async.Redis(
                host=self.config.host,
                port=self.config.port,
                db=self.config.db,
                password=self.config.password,
                socket_timeout=self.config.socket_timeout,
                socket_connect_timeout=self.config.socket_connect_timeout,
                retry_on_timeout=self.config.retry_on_timeout,
                max_connections=self.config.max_connections,
                decode_responses=True
            )

            await self._client.ping()
            self._is_connected = True
            self._reconnect_attempts = 0
            print(f"[{self.service_id}] Redis连接成功")
            return True

        except Exception as e:
            print(f"[{self.service_id}] Redis连接失败: {e}")
            return False

    async def disconnect(self):
        """断开连接"""
        for task in self._sub_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            await self._pubsub.aclose()
            self._pubsub = None

        if self._client:
            await self._client.aclose()
            self._client = None

        self._is_connected = False
        print(f"[{self.service_id}] Redis已断开")

    async def _reconnect(self) -> bool:
        """重连Redis"""
        if self._reconnect_attempts >= self._max_reconnect_attempts:
            print(f"[{self.service_id}] 达到最大重连次数，放弃重连")
            return False

        self._reconnect_attempts += 1
        wait_time = min(2 ** self._reconnect_attempts, 30)
        print(f"[{self.service_id}] 第{self._reconnect_attempts}次重连，等待{wait_time}s...")

        await asyncio.sleep(wait_time)
        return await self.connect()

    async def publish(self, channel: str, message: Dict) -> bool:
        """发布消息"""
        if not self._is_connected or not self._client:
            if not await self._reconnect():
                return False

        try:
            message_str = serialize_message(message)
            await self._client.publish(channel, message_str)
            return True
        except Exception as e:
            print(f"[{self.service_id}] 发布消息失败: {e}")
            self._is_connected = False
            return False

    async def subscribe(self, channel: str, handler: Callable[[Dict], None]):
        """订阅频道"""
        if not self._client:
            await self.connect()

        if not self._pubsub:
            self._pubsub = self._client.pubsub()

        self._message_handlers[channel] = handler
        await self._pubsub.subscribe(channel)

        task = asyncio.create_task(self._listen_loop(channel))
        self._sub_tasks.append(task)
        print(f"[{self.service_id}] 订阅频道: {channel}")

    async def _listen_loop(self, channel: str):
        """监听循环"""
        while True:
            try:
                if not self._pubsub:
                    await asyncio.sleep(1)
                    continue

                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0
                )

                if message and message['type'] == 'message':
                    try:
                        payload = deserialize_message(message['data'])
                        handler = self._message_handlers.get(channel)
                        if handler:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(payload)
                            else:
                                handler(payload)
                    except Exception as e:
                        print(f"[{self.service_id}] 处理消息失败: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{self.service_id}] 监听异常: {e}")
                self._is_connected = False
                await asyncio.sleep(1)
                if not self._is_connected:
                    await self._reconnect()

    async def set_key(self, key: str, value: Any, expire: int = None) -> bool:
        """设置键值"""
        if not self._is_connected or not self._client:
            if not await self._reconnect():
                return False

        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, (int, float)):
                value = str(value)

            if expire:
                await self._client.setex(key, expire, value)
            else:
                await self._client.set(key, value)
            return True
        except Exception as e:
            print(f"[{self.service_id}] 设置键值失败: {e}")
            return False

    async def get_key(self, key: str) -> Optional[Any]:
        """获取键值"""
        if not self._is_connected or not self._client:
            if not await self._reconnect():
                return None

        try:
            value = await self._client.get(key)
            if value:
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return value
            return None
        except Exception as e:
            print(f"[{self.service_id}] 获取键值失败: {e}")
            return None

    async def lpush(self, key: str, value: Any) -> int:
        """列表左推"""
        if not self._is_connected or not self._client:
            if not await self._reconnect():
                return 0

        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            return await self._client.lpush(key, value)
        except Exception as e:
            print(f"[{self.service_id}] 列表左推失败: {e}")
            return 0

    async def rpop(self, key: str, timeout: float = 0) -> Optional[Any]:
        """列表右弹（阻塞）"""
        if not self._is_connected or not self._client:
            if not await self._reconnect():
                return None

        try:
            if timeout > 0:
                result = await self._client.brpop(key, timeout=timeout)
                if result:
                    _, value = result
                else:
                    return None
            else:
                value = await self._client.rpop(key)
                if not value:
                    return None

            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            print(f"[{self.service_id}] 列表右弹失败: {e}")
            return None

    @property
    def is_connected(self) -> bool:
        return self._is_connected


class MicroserviceBase(RedisClientBase):
    """微服务基类"""

    def __init__(self, service_id: str, service_type: str, redis_config: RedisConfig = None):
        super().__init__(service_id, redis_config)
        self.service_type = service_type
        self._running = False
        self._start_time: Optional[float] = None
        self._metrics: Dict[str, Any] = {
            "messages_received": 0,
            "messages_published": 0,
            "errors": 0,
            "uptime_seconds": 0
        }

    async def start(self):
        """启动服务"""
        if self._running:
            return

        self._running = True
        self._start_time = time.time()

        await self.connect()
        await self._subscribe_channels()
        await self._on_start()

        status_task = asyncio.create_task(self._status_loop())
        self._sub_tasks.append(status_task)

        print(f"[{self.service_id}] 服务启动完成")

    async def stop(self):
        """停止服务"""
        if not self._running:
            return

        self._running = False
        await self._on_stop()
        await self.disconnect()

        print(f"[{self.service_id}] 服务已停止")

    async def _subscribe_channels(self):
        """订阅需要的频道 - 子类重写"""
        pass

    async def _on_start(self):
        """启动时执行 - 子类重写"""
        pass

    async def _on_stop(self):
        """停止时执行 - 子类重写"""
        pass

    async def _status_loop(self):
        """状态上报循环"""
        while self._running:
            try:
                self._metrics["uptime_seconds"] = time.time() - self._start_time if self._start_time else 0

                status_message = {
                    "header": {
                        "message_id": "",
                        "message_type": "status",
                        "source_service": self.service_id,
                        "target_service": None,
                        "timestamp": "",
                        "version": "1.0"
                    },
                    "payload": {
                        "service_id": self.service_id,
                        "service_type": self.service_type,
                        "status": "running" if self._is_connected else "degraded",
                        "timestamp": "",
                        "metrics": self._metrics,
                        "error_message": None
                    }
                }

                await self.publish(CHANNELS['SYSTEM_STATUS'], status_message)
                self._metrics["messages_published"] += 1

            except Exception as e:
                print(f"[{self.service_id}] 状态上报失败: {e}")

            await asyncio.sleep(30)

    def _increment_metric(self, name: str, amount: int = 1):
        """增加指标"""
        if name in self._metrics:
            self._metrics[name] += amount
        else:
            self._metrics[name] = amount
