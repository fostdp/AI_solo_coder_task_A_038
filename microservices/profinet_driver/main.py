"""
Profinet Driver 微服务
负责Profinet协议数据采集和解析
从模拟器接收数据 → 协议解析 → Redis发布
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import (
    MicroserviceBase, RedisConfig,
    CHANNELS, SERVICE_IDS,
    TelemetryData, MessageFactory,
    validate_message, extract_payload,
    config_loader
)
from profinet_simulator import FreezeDryerSimulator, ProfinetRTThread


class ProfinetDriverService(MicroserviceBase):
    """Profinet驱动服务"""

    def __init__(self, redis_config: RedisConfig = None):
        super().__init__(
            service_id=SERVICE_IDS['PROFINET_DRIVER'],
            service_type="data_acquisition",
            redis_config=redis_config
        )

        self._simulators: Dict[int, FreezeDryerSimulator] = {}
        self._rt_threads: Dict[int, ProfinetRTThread] = {}
        self._control_commands: Dict[int, Dict] = {}
        self._control_callbacks = {}

        self._num_devices = 10
        self._num_shelves = 5
        self._report_interval = 10

        self._last_cycle_stats = {
            "packets_parsed": 0,
            "packets_published": 0,
            "parse_errors": 0,
            "control_commands_received": 0
        }

    async def _subscribe_channels(self):
        """订阅控制命令频道"""
        await self.subscribe(CHANNELS['CONTROL_COMMAND'], self._handle_control_command)
        await self.subscribe(CHANNELS['CONFIG_UPDATE'], self._handle_config_update)

    async def _on_start(self):
        """启动模拟器和RT线程"""
        print(f"[{self.service_id}] 初始化Profinet模拟器...")

        for device_id in range(1, self._num_devices + 1):
            simulator = FreezeDryerSimulator(device_id)
            self._simulators[device_id] = simulator

            rt_thread = ProfinetRTThread(simulator)
            self._rt_threads[device_id] = rt_thread
            rt_thread.daemon = True
            rt_thread.start()

            print(f"[{self.service_id}] 设备{device_id} RT线程已启动")

        data_cycle_task = asyncio.create_task(self._data_collection_cycle())
        self._sub_tasks.append(data_cycle_task)

        control_apply_task = asyncio.create_task(self._control_application_cycle())
        self._sub_tasks.append(control_apply_task)

        await asyncio.sleep(2)
        print(f"[{self.service_id}] 所有设备初始化完成")

    async def _on_stop(self):
        """停止所有RT线程"""
        print(f"[{self.service_id}] 停止所有RT线程...")
        for device_id, rt_thread in self._rt_threads.items():
            if hasattr(rt_thread, 'stop'):
                rt_thread.stop()

        await asyncio.sleep(1)
        print(f"[{self.service_id}] 所有RT线程已停止")

    async def _data_collection_cycle(self):
        """数据采集循环"""
        while self._running:
            cycle_start = asyncio.get_event_loop().time()

            try:
                for device_id in range(1, self._num_devices + 1):
                    simulator = self._simulators.get(device_id)
                    if not simulator:
                        continue

                    try:
                        telemetry_list = simulator.generate_telemetry()
                        simulator.update_state()

                        for telemetry in telemetry_list:
                            parsed_data = self._parse_profinet_data(device_id, telemetry)
                            if parsed_data:
                                await self._publish_telemetry(parsed_data)
                                self._last_cycle_stats["packets_parsed"] += 1
                                self._last_cycle_stats["packets_published"] += 1

                    except Exception as e:
                        print(f"[{self.service_id}] 设备{device_id}数据采集失败: {e}")
                        self._last_cycle_stats["parse_errors"] += 1

                self._metrics.update(self._last_cycle_stats)

            except Exception as e:
                print(f"[{self.service_id}] 数据采集循环异常: {e}")

            cycle_duration = asyncio.get_event_loop().time() - cycle_start
            sleep_time = max(0, self._report_interval - cycle_duration)
            await asyncio.sleep(sleep_time)

    def _parse_profinet_data(self, device_id: int, raw_data: Dict) -> TelemetryData:
        """解析Profinet数据"""
        try:
            temperatures = raw_data.get('temperatures', [0.0] * 8)
            vacuum_levels = raw_data.get('vacuum_levels', [0.0] * 2)
            heating_powers = raw_data.get('heating_powers', [0.0] * 8)

            profinet_flags = raw_data.get('profinet_flags', {})
            data_quality = profinet_flags.get('data_quality', 0)

            if data_quality != 0:
                print(f"[{self.service_id}] 设备{device_id}数据质量警告: {data_quality}")

            telemetry = TelemetryData(
                device_id=device_id,
                shelf_id=raw_data.get('shelf_id', 1),
                timestamp=raw_data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                temperatures=[float(t) for t in temperatures],
                vacuum_levels=[float(v) for v in vacuum_levels],
                cold_trap_temp=float(raw_data.get('cold_trap_temp', -70.0)),
                heating_powers=[float(p) for p in heating_powers],
                batch_id=raw_data.get('batch_id'),
                cycle_id=raw_data.get('cycle_id'),
                data_quality=data_quality
            )

            return telemetry

        except (KeyError, TypeError, ValueError) as e:
            print(f"[{self.service_id}] 数据解析失败: {e}")
            return None

    async def _publish_telemetry(self, telemetry: TelemetryData):
        """发布遥测数据到Redis"""
        message = MessageFactory.create_telemetry(
            telemetry,
            self.service_id
        )

        success = await self.publish(CHANNELS['TELEMETRY_RAW'], message)
        if success:
            self._increment_metric("messages_published")

            specific_channel = f"{CHANNELS['TELEMETRY_RAW']}:{telemetry.device_id}"
            await self.publish(specific_channel, message)

    async def _handle_control_command(self, message: Dict):
        """处理控制命令"""
        if not validate_message(message, 'control_command'):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')

        if device_id is None:
            return

        self._control_commands[device_id] = payload
        self._last_cycle_stats["control_commands_received"] += 1
        self._increment_metric("messages_received")

        print(f"[{self.service_id}] 收到设备{device_id}控制命令: "
              f"调整量={[round(a, 2) for a in payload.get('power_adjustments', [])]}")

    async def _control_application_cycle(self):
        """控制命令应用循环"""
        while self._running:
            try:
                for device_id, command in list(self._control_commands.items()):
                    simulator = self._simulators.get(device_id)
                    if not simulator:
                        continue

                    adjustments = command.get('power_adjustments', [0.0] * 8)
                    auto_mode = command.get('auto_mode', True)

                    if auto_mode and adjustments:
                        simulator.apply_control_adjustment(adjustments)

                    del self._control_commands[device_id]

            except Exception as e:
                print(f"[{self.service_id}] 控制命令应用异常: {e}")

            await asyncio.sleep(self._report_interval)

    async def _handle_config_update(self, message: Dict):
        """处理配置更新"""
        if not validate_message(message, 'config'):
            return

        payload = extract_payload(message)
        config_type = payload.get('config_type')

        if config_type == 'profinet':
            print(f"[{self.service_id}] 收到配置更新: {payload.get('config_data', {})}")
            config_data = payload.get('config_data', {})
            if 'report_interval' in config_data:
                self._report_interval = config_data['report_interval']

            config_loader.reload_all()
            self._increment_metric("config_updates")


async def main():
    """主函数"""
    redis_config = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0'))
    )

    service = ProfinetDriverService(redis_config)

    print("=" * 60)
    print("Profinet Driver 微服务启动")
    print(f"服务ID: {service.service_id}")
    print(f"Redis: {redis_config.host}:{redis_config.port}")
    print(f"设备数: {service._num_devices}")
    print(f"上报间隔: {service._report_interval}s")
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
