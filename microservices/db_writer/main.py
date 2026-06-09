"""
数据库写入微服务
订阅Redis频道，批量写入TimescaleDB
支持批量缓存、优雅降级、自动重连
"""

import asyncio
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

try:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text, exc
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import (
    MicroserviceBase, RedisConfig,
    CHANNELS, SERVICE_IDS,
    TelemetryData, ControlCommand, PredictionResult, AlarmEvent,
    validate_message, extract_payload
)


class DataType(str, Enum):
    """数据类型枚举"""
    TELEMETRY = "telemetry"
    CONTROL = "control"
    PREDICTION = "prediction"
    ALARM = "alarm"


@dataclass
class WriteItem:
    """写入队列项"""
    data_type: DataType
    data: Dict[str, Any]
    received_at: float


@dataclass
class DBConfig:
    """数据库配置"""
    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/freeze_dryer"
    pool_size: int = 20
    max_overflow: int = 30
    pool_recycle: int = 3600
    connect_timeout: int = 10
    max_reconnect_attempts: int = 10


class DBWriterService(MicroserviceBase):
    """数据库写入微服务"""

    def __init__(self, redis_config: RedisConfig = None, db_config: DBConfig = None):
        super().__init__(
            service_id=SERVICE_IDS['DB_WRITER'],
            service_type="database_writer",
            redis_config=redis_config
        )

        self._db_config: DBConfig = db_config or DBConfig(
            url=os.environ.get(
                'DATABASE_URL',
                'postgresql+asyncpg://postgres:postgres@localhost:5432/freeze_dryer'
            )
        )

        self._engine = None
        self._session_factory: Optional[async_sessionmaker] = None
        self._db_connected: bool = False
        self._db_reconnect_attempts: int = 0

        self._write_queue: asyncio.Queue[WriteItem] = asyncio.Queue(maxsize=10000)
        self._batch_size: int = 50
        self._flush_interval: float = 10.0

        self._fallback_dir: Path = Path(__file__).parent / "fallback_data"
        self._fallback_dir.mkdir(exist_ok=True)

        self._writer_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        self._metrics["queue_size"] = 0
        self._metrics["total_written"] = 0
        self._metrics["total_fallback"] = 0
        self._metrics["db_errors"] = 0

    async def _connect_db(self) -> bool:
        """连接数据库"""
        if not HAS_SQLALCHEMY:
            print(f"[{self.service_id}] 警告: SQLAlchemy或asyncpg未安装")
            return False

        try:
            if self._engine:
                await self._engine.dispose()

            self._engine = create_async_engine(
                self._db_config.url,
                echo=False,
                pool_size=self._db_config.pool_size,
                max_overflow=self._db_config.max_overflow,
                pool_recycle=self._db_config.pool_recycle,
                connect_args={"timeout": self._db_config.connect_timeout}
            )

            self._session_factory = async_sessionmaker(
                self._engine,
                class_=AsyncSession,
                expire_on_commit=False
            )

            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT 1"))

            self._db_connected = True
            self._db_reconnect_attempts = 0
            print(f"[{self.service_id}] 数据库连接成功")

            await self._load_fallback_data()
            return True

        except Exception as e:
            print(f"[{self.service_id}] 数据库连接失败: {e}")
            self._db_connected = False
            return False

    async def _disconnect_db(self) -> None:
        """断开数据库连接"""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._db_connected = False
            print(f"[{self.service_id}] 数据库已断开")

    async def _reconnect_db(self) -> bool:
        """重连数据库"""
        if self._db_reconnect_attempts >= self._db_config.max_reconnect_attempts:
            print(f"[{self.service_id}] 达到最大重连次数，放弃重连")
            return False

        self._db_reconnect_attempts += 1
        wait_time = min(2 ** self._db_reconnect_attempts, 30)
        print(f"[{self.service_id}] 第{self._db_reconnect_attempts}次数据库重连，等待{wait_time}s...")

        await asyncio.sleep(wait_time)
        return await self._connect_db()

    async def _subscribe_channels(self) -> None:
        """订阅Redis频道"""
        await self.subscribe(CHANNELS['TELEMETRY_RAW'], self._handle_telemetry)
        await self.subscribe(CHANNELS['CONTROL_COMMAND'], self._handle_control)
        await self.subscribe(CHANNELS['PREDICTION_RESULT'], self._handle_prediction)
        await self.subscribe(CHANNELS['ALARM_EVENT'], self._handle_alarm)

    async def _on_start(self) -> None:
        """服务启动时执行"""
        print(f"[{self.service_id}] 启动数据库写入服务...")

        await self._connect_db()

        self._writer_task = asyncio.create_task(self._writer_loop())
        self._sub_tasks.append(self._writer_task)

        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._sub_tasks.append(self._reconnect_task)

        print(f"[{self.service_id}] 数据库写入服务已启动")
        print(f"[{self.service_id}] 批量大小: {self._batch_size}")
        print(f"[{self.service_id}] 刷新间隔: {self._flush_interval}s")
        print(f"[{self.service_id}] 队列容量: {self._write_queue.maxsize}")

    async def _on_stop(self) -> None:
        """服务停止时执行"""
        print(f"[{self.service_id}] 停止数据库写入服务...")

        await self._flush_queue()
        await self._disconnect_db()

        print(f"[{self.service_id}] 数据库写入服务已停止")

    async def _handle_telemetry(self, message: Dict) -> None:
        """处理遥测数据"""
        if not validate_message(message, 'telemetry'):
            return

        payload = extract_payload(message)
        self._increment_metric("messages_received")

        item = WriteItem(
            data_type=DataType.TELEMETRY,
            data=payload,
            received_at=asyncio.get_event_loop().time()
        )

        await self._enqueue_item(item)

    async def _handle_control(self, message: Dict) -> None:
        """处理控制命令"""
        if not validate_message(message, 'control_command'):
            return

        payload = extract_payload(message)
        self._increment_metric("messages_received")

        item = WriteItem(
            data_type=DataType.CONTROL,
            data=payload,
            received_at=asyncio.get_event_loop().time()
        )

        await self._enqueue_item(item)

    async def _handle_prediction(self, message: Dict) -> None:
        """处理预测结果"""
        if not validate_message(message, 'prediction'):
            return

        payload = extract_payload(message)
        self._increment_metric("messages_received")

        item = WriteItem(
            data_type=DataType.PREDICTION,
            data=payload,
            received_at=asyncio.get_event_loop().time()
        )

        await self._enqueue_item(item)

    async def _handle_alarm(self, message: Dict) -> None:
        """处理告警事件"""
        if not validate_message(message, 'alarm'):
            return

        payload = extract_payload(message)
        self._increment_metric("messages_received")

        item = WriteItem(
            data_type=DataType.ALARM,
            data=payload,
            received_at=asyncio.get_event_loop().time()
        )

        await self._enqueue_item(item)

    async def _enqueue_item(self, item: WriteItem) -> None:
        """将数据项加入队列"""
        try:
            if self._write_queue.full():
                print(f"[{self.service_id}] 队列已满，写入降级文件")
                await self._write_fallback(item)
            else:
                self._write_queue.put_nowait(item)

            self._metrics["queue_size"] = self._write_queue.qsize()

        except Exception as e:
            print(f"[{self.service_id}] 入队失败，写入降级文件: {e}")
            await self._write_fallback(item)

    async def _writer_loop(self) -> None:
        """写入循环"""
        while self._running:
            batch: List[WriteItem] = []
            try:
                item = await asyncio.wait_for(
                    self._write_queue.get(),
                    timeout=self._flush_interval
                )
                batch.append(item)

                while len(batch) < self._batch_size:
                    try:
                        item = self._write_queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                if self._db_connected:
                    success = await self._write_batch(batch)
                    if not success:
                        await self._write_fallback_batch(batch)
                else:
                    await self._write_fallback_batch(batch)

                self._metrics["queue_size"] = self._write_queue.qsize()

            except asyncio.TimeoutError:
                if batch:
                    if self._db_connected:
                        success = await self._write_batch(batch)
                        if not success:
                            await self._write_fallback_batch(batch)
                    else:
                        await self._write_fallback_batch(batch)

            except Exception as e:
                print(f"[{self.service_id}] 写入循环异常: {e}")
                self._increment_metric("errors")
                if batch:
                    await self._write_fallback_batch(batch)
                await asyncio.sleep(1)

    async def _write_batch(self, batch: List[WriteItem]) -> bool:
        """批量写入数据库"""
        if not self._session_factory or not self._db_connected:
            return False

        try:
            async with self._session_factory() as session:
                telemetry_data: List[Tuple] = []
                control_data: List[Tuple] = []
                prediction_data: List[Tuple] = []
                alarm_data: List[Tuple] = []

                for item in batch:
                    try:
                        if item.data_type == DataType.TELEMETRY:
                            telemetry_data.append(self._prepare_telemetry(item.data))
                        elif item.data_type == DataType.CONTROL:
                            control_data.append(self._prepare_control(item.data))
                        elif item.data_type == DataType.PREDICTION:
                            prediction_data.append(self._prepare_prediction(item.data))
                        elif item.data_type == DataType.ALARM:
                            alarm_data.append(self._prepare_alarm(item.data))
                    except Exception as e:
                        print(f"[{self.service_id}] 数据准备失败: {e}")
                        self._increment_metric("errors")

                if telemetry_data:
                    await self._insert_telemetry(session, telemetry_data)
                if control_data:
                    await self._insert_control(session, control_data)
                if prediction_data:
                    await self._insert_prediction(session, prediction_data)
                if alarm_data:
                    await self._insert_alarm(session, alarm_data)

                await session.commit()

            written_count = len(telemetry_data) + len(control_data) + len(prediction_data) + len(alarm_data)
            self._metrics["total_written"] += written_count
            print(f"[{self.service_id}] 批量写入完成: {len(batch)}条")
            return True

        except exc.OperationalError as e:
            print(f"[{self.service_id}] 数据库连接异常: {e}")
            self._db_connected = False
            self._increment_metric("db_errors")
            return False

        except Exception as e:
            print(f"[{self.service_id}] 批量写入失败: {e}")
            self._increment_metric("db_errors")
            return False

    def _prepare_telemetry(self, data: Dict) -> Tuple:
        """准备遥测数据"""
        temps = data.get('temperatures', [0.0] * 8)
        vacuums = data.get('vacuum_levels', [0.0] * 2)
        powers = data.get('heating_powers', [0.0] * 8)

        return (
            data.get('timestamp', datetime.now(timezone.utc).isoformat()),
            data.get('device_id', 0),
            data.get('shelf_id', 0),
            temps[0] if len(temps) > 0 else 0.0,
            temps[1] if len(temps) > 1 else 0.0,
            temps[2] if len(temps) > 2 else 0.0,
            temps[3] if len(temps) > 3 else 0.0,
            temps[4] if len(temps) > 4 else 0.0,
            temps[5] if len(temps) > 5 else 0.0,
            temps[6] if len(temps) > 6 else 0.0,
            temps[7] if len(temps) > 7 else 0.0,
            vacuums[0] if len(vacuums) > 0 else 0.0,
            vacuums[1] if len(vacuums) > 1 else 0.0,
            data.get('cold_trap_temp', 0.0),
            powers[0] if len(powers) > 0 else 0.0,
            powers[1] if len(powers) > 1 else 0.0,
            powers[2] if len(powers) > 2 else 0.0,
            powers[3] if len(powers) > 3 else 0.0,
            powers[4] if len(powers) > 4 else 0.0,
            powers[5] if len(powers) > 5 else 0.0,
            powers[6] if len(powers) > 6 else 0.0,
            powers[7] if len(powers) > 7 else 0.0,
        )

    def _prepare_control(self, data: Dict) -> Tuple:
        """准备控制命令数据"""
        adjustments = data.get('power_adjustments', [0.0] * 8)

        return (
            data.get('device_id', 0),
            data.get('shelf_id', 0),
            data.get('timestamp', datetime.now(timezone.utc).isoformat()),
            adjustments[0] if len(adjustments) > 0 else 0.0,
            adjustments[1] if len(adjustments) > 1 else 0.0,
            adjustments[2] if len(adjustments) > 2 else 0.0,
            adjustments[3] if len(adjustments) > 3 else 0.0,
            adjustments[4] if len(adjustments) > 4 else 0.0,
            adjustments[5] if len(adjustments) > 5 else 0.0,
            adjustments[6] if len(adjustments) > 6 else 0.0,
            adjustments[7] if len(adjustments) > 7 else 0.0,
            data.get('auto_mode', True),
        )

    def _prepare_prediction(self, data: Dict) -> Tuple:
        """准备预测结果数据"""
        return (
            data.get('device_id', 0),
            data.get('batch_id'),
            data.get('timestamp', datetime.now(timezone.utc).isoformat()),
            data.get('moisture_content', 0.0),
            data.get('moisture_confidence', 0.0),
            data.get('moisture_threshold', 3.0),
            data.get('reconstitution_time', 0.0),
            data.get('reconstitution_confidence', 0.0),
            data.get('reconstitution_threshold', 120.0),
            data.get('drying_rate', 0.0),
            data.get('is_qualified', True),
        )

    def _prepare_alarm(self, data: Dict) -> Tuple:
        """准备告警数据"""
        return (
            data.get('alarm_id'),
            data.get('timestamp', datetime.now(timezone.utc).isoformat()),
            data.get('device_id', 0),
            data.get('shelf_id'),
            data.get('alarm_type', ''),
            data.get('severity', ''),
            data.get('message', ''),
            data.get('acknowledged', False),
            data.get('acknowledged_by'),
            data.get('acknowledged_at'),
        )

    async def _insert_telemetry(self, session: AsyncSession, data: List[Tuple]) -> None:
        """批量插入遥测数据"""
        stmt = text("""
            INSERT INTO telemetry (
                timestamp, device_id, shelf_id,
                temp_1, temp_2, temp_3, temp_4, temp_5, temp_6, temp_7, temp_8,
                vacuum_1, vacuum_2, cold_trap_temp,
                power_1, power_2, power_3, power_4, power_5, power_6, power_7, power_8
            ) VALUES (
                :timestamp, :device_id, :shelf_id,
                :temp_1, :temp_2, :temp_3, :temp_4, :temp_5, :temp_6, :temp_7, :temp_8,
                :vacuum_1, :vacuum_2, :cold_trap_temp,
                :power_1, :power_2, :power_3, :power_4, :power_5, :power_6, :power_7, :power_8
            )
            ON CONFLICT (timestamp, device_id, shelf_id) DO UPDATE SET
                temp_1 = EXCLUDED.temp_1,
                temp_2 = EXCLUDED.temp_2,
                temp_3 = EXCLUDED.temp_3,
                temp_4 = EXCLUDED.temp_4,
                temp_5 = EXCLUDED.temp_5,
                temp_6 = EXCLUDED.temp_6,
                temp_7 = EXCLUDED.temp_7,
                temp_8 = EXCLUDED.temp_8,
                vacuum_1 = EXCLUDED.vacuum_1,
                vacuum_2 = EXCLUDED.vacuum_2,
                cold_trap_temp = EXCLUDED.cold_trap_temp,
                power_1 = EXCLUDED.power_1,
                power_2 = EXCLUDED.power_2,
                power_3 = EXCLUDED.power_3,
                power_4 = EXCLUDED.power_4,
                power_5 = EXCLUDED.power_5,
                power_6 = EXCLUDED.power_6,
                power_7 = EXCLUDED.power_7,
                power_8 = EXCLUDED.power_8
        """)

        params = [
            {
                'timestamp': row[0],
                'device_id': row[1],
                'shelf_id': row[2],
                'temp_1': row[3], 'temp_2': row[4], 'temp_3': row[5], 'temp_4': row[6],
                'temp_5': row[7], 'temp_6': row[8], 'temp_7': row[9], 'temp_8': row[10],
                'vacuum_1': row[11], 'vacuum_2': row[12], 'cold_trap_temp': row[13],
                'power_1': row[14], 'power_2': row[15], 'power_3': row[16], 'power_4': row[17],
                'power_5': row[18], 'power_6': row[19], 'power_7': row[20], 'power_8': row[21],
            }
            for row in data
        ]

        await session.execute(stmt, params)

    async def _insert_control(self, session: AsyncSession, data: List[Tuple]) -> None:
        """批量插入控制命令"""
        stmt = text("""
            INSERT INTO control_commands (
                device_id, shelf_id, timestamp,
                power_adj_1, power_adj_2, power_adj_3, power_adj_4,
                power_adj_5, power_adj_6, power_adj_7, power_adj_8,
                auto_mode
            ) VALUES (
                :device_id, :shelf_id, :timestamp,
                :power_adj_1, :power_adj_2, :power_adj_3, :power_adj_4,
                :power_adj_5, :power_adj_6, :power_adj_7, :power_adj_8,
                :auto_mode
            )
        """)

        params = [
            {
                'device_id': row[0],
                'shelf_id': row[1],
                'timestamp': row[2],
                'power_adj_1': row[3], 'power_adj_2': row[4], 'power_adj_3': row[5], 'power_adj_4': row[6],
                'power_adj_5': row[7], 'power_adj_6': row[8], 'power_adj_7': row[9], 'power_adj_8': row[10],
                'auto_mode': row[11],
            }
            for row in data
        ]

        await session.execute(stmt, params)

    async def _insert_prediction(self, session: AsyncSession, data: List[Tuple]) -> None:
        """批量插入预测结果"""
        stmt = text("""
            INSERT INTO prediction_results (
                device_id, batch_id, timestamp,
                moisture_pred, moisture_conf, moisture_threshold,
                reconstitution_pred, reconstitution_conf, reconstitution_threshold,
                drying_rate, is_qualified
            ) VALUES (
                :device_id, :batch_id, :timestamp,
                :moisture_pred, :moisture_conf, :moisture_threshold,
                :reconstitution_pred, :reconstitution_conf, :reconstitution_threshold,
                :drying_rate, :is_qualified
            )
        """)

        params = [
            {
                'device_id': row[0],
                'batch_id': row[1],
                'timestamp': row[2],
                'moisture_pred': row[3],
                'moisture_conf': row[4],
                'moisture_threshold': row[5],
                'reconstitution_pred': row[6],
                'reconstitution_conf': row[7],
                'reconstitution_threshold': row[8],
                'drying_rate': row[9],
                'is_qualified': row[10],
            }
            for row in data
        ]

        await session.execute(stmt, params)

    async def _insert_alarm(self, session: AsyncSession, data: List[Tuple]) -> None:
        """批量插入告警"""
        stmt = text("""
            INSERT INTO alarms (
                id, timestamp, device_id, shelf_id,
                alarm_type, severity, message,
                acknowledged, acknowledged_by, acknowledged_at
            ) VALUES (
                :id, :timestamp, :device_id, :shelf_id,
                :alarm_type, :severity, :message,
                :acknowledged, :acknowledged_by, :acknowledged_at
            )
            ON CONFLICT (id) DO UPDATE SET
                acknowledged = EXCLUDED.acknowledged,
                acknowledged_by = EXCLUDED.acknowledged_by,
                acknowledged_at = EXCLUDED.acknowledged_at
        """)

        params = [
            {
                'id': row[0],
                'timestamp': row[1],
                'device_id': row[2],
                'shelf_id': row[3],
                'alarm_type': row[4],
                'severity': row[5],
                'message': row[6],
                'acknowledged': row[7],
                'acknowledged_by': row[8],
                'acknowledged_at': row[9],
            }
            for row in data
        ]

        await session.execute(stmt, params)

    async def _write_fallback(self, item: WriteItem) -> None:
        """写入降级文件（单条）"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d")
            filename = self._fallback_dir / f"fallback_{timestamp}.jsonl"

            record = {
                'data_type': item.data_type.value,
                'data': item.data,
                'received_at': item.received_at
            }

            with open(filename, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

            self._increment_metric("total_fallback")

        except Exception as e:
            print(f"[{self.service_id}] 降级文件写入失败: {e}")
            self._increment_metric("errors")

    async def _write_fallback_batch(self, batch: List[WriteItem]) -> None:
        """批量写入降级文件"""
        for item in batch:
            await self._write_fallback(item)

    async def _load_fallback_data(self) -> None:
        """加载降级文件中的数据"""
        try:
            loaded_count = 0
            for filepath in sorted(self._fallback_dir.glob("fallback_*.jsonl")):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue

                            record = json.loads(line)
                            item = WriteItem(
                                data_type=DataType(record['data_type']),
                                data=record['data'],
                                received_at=record.get('received_at', 0.0)
                            )

                            await self._write_queue.put(item)
                            loaded_count += 1

                    filepath.unlink()
                    print(f"[{self.service_id}] 已加载降级文件: {filepath.name} ({loaded_count}条)")

                except Exception as e:
                    print(f"[{self.service_id}] 加载降级文件失败 {filepath}: {e}")

            if loaded_count > 0:
                print(f"[{self.service_id}] 共加载降级数据: {loaded_count}条")

        except Exception as e:
            print(f"[{self.service_id}] 加载降级数据异常: {e}")

    async def _reconnect_loop(self) -> None:
        """数据库重连循环"""
        while self._running:
            try:
                if not self._db_connected:
                    await self._reconnect_db()
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{self.service_id}] 重连循环异常: {e}")
                await asyncio.sleep(5)

    async def _flush_queue(self) -> None:
        """刷新队列中的所有数据"""
        print(f"[{self.service_id}] 刷新队列剩余数据...")

        remaining: List[WriteItem] = []
        while not self._write_queue.empty():
            try:
                item = self._write_queue.get_nowait()
                remaining.append(item)
            except asyncio.QueueEmpty:
                break

        if remaining:
            if self._db_connected:
                success = await self._write_batch(remaining)
                if not success:
                    await self._write_fallback_batch(remaining)
            else:
                await self._write_fallback_batch(remaining)

            print(f"[{self.service_id}] 已处理剩余数据: {len(remaining)}条")


async def main() -> None:
    """主函数"""
    redis_config = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0'))
    )

    db_config = DBConfig(
        url=os.environ.get(
            'DATABASE_URL',
            'postgresql+asyncpg://postgres:postgres@localhost:5432/freeze_dryer'
        )
    )

    service = DBWriterService(redis_config, db_config)

    print("=" * 60)
    print("数据库写入微服务启动")
    print(f"服务ID: {service.service_id}")
    print(f"服务类型: {service.service_type}")
    print(f"Redis: {redis_config.host}:{redis_config.port}")
    print(f"数据库URL: {db_config.url}")
    print(f"批量大小: {service._batch_size}")
    print(f"刷新间隔: {service._flush_interval}s")
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
