"""
Profinet 协议模拟器 - 实时性优化版 + 异常注入增强
模拟多台冻干机，每台多层搁板，每层多个温度传感器+真空度传感器
每N秒上报一次数据

优化特性：
- 每设备独立线程 + 独立asyncio事件循环
- aiohttp异步驱动提升并发性能
- 高精度周期补偿，减少抖动
- RT/NRT数据优先级队列
- 时间戳单调递增保证
- 异常注入系统（手动/自动）
"""

import asyncio
import json
import random
import time
import threading
import queue
import aiohttp
import os
import uuid
import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

SIM_NUM_DEVICES = int(os.getenv("SIM_NUM_DEVICES", "10"))
SIM_NUM_SHELVES = int(os.getenv("SIM_NUM_SHELVES", "5"))
SIM_TEMP_SENSORS_PER_SHELF = int(os.getenv("SIM_TEMP_SENSORS_PER_SHELF", "8"))
SIM_VACUUM_SENSORS_PER_SHELF = int(os.getenv("SIM_VACUUM_SENSORS_PER_SHELF", "2"))
SIM_REPORT_INTERVAL = int(os.getenv("SIM_REPORT_INTERVAL", "10"))
SIM_ENABLE_ANOMALY = os.getenv("SIM_ENABLE_ANOMALY", "true").lower() == "true"
SIM_ANOMALY_INTERVAL = float(os.getenv("SIM_ANOMALY_INTERVAL", "120"))

NUM_DEVICES = SIM_NUM_DEVICES
NUM_SHELVES = SIM_NUM_SHELVES
NUM_TEMP_SENSORS = SIM_TEMP_SENSORS_PER_SHELF
NUM_VACUUM_SENSORS = SIM_VACUUM_SENSORS_PER_SHELF
REPORT_INTERVAL = SIM_REPORT_INTERVAL


class AnomalyType(Enum):
    TEMP_SPIKE = "temp_spike"
    TEMP_OFFSET = "temp_offset"
    VACUUM_SPIKE = "vacuum_spike"
    VACUUM_DRIFT = "vacuum_drift"
    SENSOR_FAILURE = "sensor_failure"
    COLD_TRAP_HIGH = "cold_trap_high"
    RANDOM_NOISE = "random_noise"


@dataclass
class ActiveAnomaly:
    id: str
    type: AnomalyType
    device_id: Optional[int] = None
    shelf_id: Optional[int] = None
    sensor_id: Optional[int] = None
    start_time: float = field(default_factory=time.time)
    duration: float = 60.0
    strength: float = 1.0
    params: Dict = field(default_factory=dict)


class DataPriority(Enum):
    RT = 0
    NRT = 1


@dataclass
class ProfinetPacket:
    priority: DataPriority
    device_id: int
    data: Dict
    timestamp: float
    retry_count: int = 0


@dataclass
class ThreadStats:
    packets_sent: int = 0
    packets_dropped: int = 0
    avg_latency_ms: float = 0.0
    max_jitter_ms: float = 0.0
    cycle_deviation_ms: float = 0.0


class AnomalyInjector:
    def __init__(self, enabled: bool = True, auto_interval: float = 120.0):
        self._enabled = enabled
        self._auto_interval = auto_interval
        self._active_anomalies: Dict[str, ActiveAnomaly] = {}
        self._last_auto_inject = 0.0
        self._lock = threading.Lock()
        self._auto_enabled = enabled
        self._running = False
        self._auto_thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        if self._enabled and self._auto_interval > 0:
            self._auto_thread = threading.Thread(target=self._auto_inject_loop, daemon=True, name="Anomaly-Auto-Loop")
            self._auto_thread.start()

    def stop(self):
        self._running = False
        if self._auto_thread:
            self._auto_thread.join(timeout=2.0)

    def inject(self, anomaly_type: AnomalyType, **kwargs) -> str:
        anomaly_id = str(uuid.uuid4())[:8]
        anomaly = ActiveAnomaly(
            id=anomaly_id,
            type=anomaly_type,
            device_id=kwargs.get("device_id"),
            shelf_id=kwargs.get("shelf_id"),
            sensor_id=kwargs.get("sensor_id"),
            duration=kwargs.get("duration", 60.0),
            strength=kwargs.get("strength", 1.0),
            params=kwargs.get("params", {})
        )
        with self._lock:
            self._active_anomalies[anomaly_id] = anomaly
        return anomaly_id

    def clear(self, anomaly_id: str) -> bool:
        with self._lock:
            if anomaly_id in self._active_anomalies:
                del self._active_anomalies[anomaly_id]
                return True
            return False

    def clear_all(self):
        with self._lock:
            self._active_anomalies.clear()

    def list_active(self) -> List[ActiveAnomaly]:
        self._cleanup_expired()
        with self._lock:
            return list(self._active_anomalies.values())

    def set_auto_enabled(self, enabled: bool):
        self._auto_enabled = enabled

    def is_auto_enabled(self) -> bool:
        return self._auto_enabled

    def _cleanup_expired(self):
        now = time.time()
        with self._lock:
            expired = [
                aid for aid, a in self._active_anomalies.items()
                if now - a.start_time > a.duration and a.duration > 0
            ]
            for aid in expired:
                del self._active_anomalies[aid]

    def _get_applicable_anomalies(self, device_id: int, shelf_id: Optional[int] = None,
                                   sensor_idx: Optional[int] = None) -> List[ActiveAnomaly]:
        self._cleanup_expired()
        result = []
        with self._lock:
            for anomaly in self._active_anomalies.values():
                if anomaly.device_id is not None and anomaly.device_id != device_id:
                    continue
                if anomaly.shelf_id is not None and shelf_id is not None and anomaly.shelf_id != shelf_id:
                    continue
                if anomaly.sensor_id is not None and sensor_idx is not None and anomaly.sensor_id != sensor_idx:
                    continue
                result.append(anomaly)
        return result

    def apply_temperature_anomalies(self, device_id: int, shelf_id: int,
                                     sensor_idx: int, base_temp: float) -> Tuple[float, List[Dict]]:
        anomalies = self._get_applicable_anomalies(device_id, shelf_id, sensor_idx)
        applied_anomalies = []
        result_temp = base_temp

        for anomaly in anomalies:
            anomaly_info = {"id": anomaly.id, "type": anomaly.type.value, "strength": anomaly.strength}

            if anomaly.type == AnomalyType.TEMP_SPIKE:
                spike = random.uniform(-5, 5) * anomaly.strength
                result_temp += spike
                anomaly_info["value"] = round(spike, 2)

            elif anomaly.type == AnomalyType.TEMP_OFFSET:
                offset = 3.0 * anomaly.strength
                if "direction" not in anomaly.params:
                    anomaly.params["direction"] = random.choice([1, -1])
                offset *= anomaly.params["direction"]
                result_temp += offset
                anomaly_info["value"] = round(offset, 2)

            elif anomaly.type == AnomalyType.SENSOR_FAILURE:
                if "failure_mode" not in anomaly.params:
                    anomaly.params["failure_mode"] = random.choice(["fixed", "nan"])
                if anomaly.params["failure_mode"] == "nan":
                    result_temp = float('nan')
                else:
                    if "fixed_value" not in anomaly.params:
                        anomaly.params["fixed_value"] = random.uniform(-100, 100)
                    result_temp = anomaly.params["fixed_value"]
                anomaly_info["mode"] = anomaly.params["failure_mode"]

            elif anomaly.type == AnomalyType.RANDOM_NOISE:
                noise = random.gauss(0, 1.0) * anomaly.strength
                result_temp += noise
                anomaly_info["value"] = round(noise, 2)

            applied_anomalies.append(anomaly_info)

        return result_temp, applied_anomalies

    def apply_vacuum_anomalies(self, device_id: int, shelf_id: int,
                                sensor_idx: int, base_vacuum: float) -> Tuple[float, List[Dict]]:
        anomalies = self._get_applicable_anomalies(device_id, shelf_id, sensor_idx)
        applied_anomalies = []
        result_vacuum = base_vacuum

        for anomaly in anomalies:
            anomaly_info = {"id": anomaly.id, "type": anomaly.type.value, "strength": anomaly.strength}

            if anomaly.type == AnomalyType.VACUUM_SPIKE:
                spike = 10.0 * anomaly.strength
                result_vacuum += spike
                anomaly_info["value"] = round(spike, 4)

            elif anomaly.type == AnomalyType.VACUUM_DRIFT:
                elapsed = time.time() - anomaly.start_time
                drift_rate = 0.01 * anomaly.strength
                drift = drift_rate * elapsed
                result_vacuum += drift
                anomaly_info["value"] = round(drift, 4)

            elif anomaly.type == AnomalyType.SENSOR_FAILURE:
                if "failure_mode" not in anomaly.params:
                    anomaly.params["failure_mode"] = random.choice(["fixed", "nan"])
                if anomaly.params["failure_mode"] == "nan":
                    result_vacuum = float('nan')
                else:
                    if "fixed_value" not in anomaly.params:
                        anomaly.params["fixed_value"] = random.uniform(0, 1000)
                    result_vacuum = anomaly.params["fixed_value"]
                anomaly_info["mode"] = anomaly.params["failure_mode"]

            elif anomaly.type == AnomalyType.RANDOM_NOISE:
                noise = random.gauss(0, 0.5) * anomaly.strength
                result_vacuum += noise
                anomaly_info["value"] = round(noise, 4)

            applied_anomalies.append(anomaly_info)

        return max(0.001, result_vacuum), applied_anomalies

    def apply_cold_trap_anomaly(self, device_id: int, base_temp: float) -> Tuple[float, List[Dict]]:
        anomalies = self._get_applicable_anomalies(device_id)
        applied_anomalies = []
        result_temp = base_temp

        for anomaly in anomalies:
            if anomaly.type == AnomalyType.COLD_TRAP_HIGH:
                anomaly_info = {"id": anomaly.id, "type": anomaly.type.value, "strength": anomaly.strength}
                target_min = -40.0 + (5.0 * anomaly.strength)
                if result_temp < target_min:
                    warm_up = target_min - result_temp + random.uniform(0, 5)
                    result_temp += warm_up
                    anomaly_info["value"] = round(warm_up, 2)
                applied_anomalies.append(anomaly_info)

            elif anomaly.type == AnomalyType.SENSOR_FAILURE:
                anomaly_info = {"id": anomaly.id, "type": anomaly.type.value, "strength": anomaly.strength}
                if "failure_mode" not in anomaly.params:
                    anomaly.params["failure_mode"] = random.choice(["fixed", "nan"])
                if anomaly.params["failure_mode"] == "nan":
                    result_temp = float('nan')
                else:
                    if "fixed_value" not in anomaly.params:
                        anomaly.params["fixed_value"] = random.uniform(-100, 50)
                    result_temp = anomaly.params["fixed_value"]
                anomaly_info["mode"] = anomaly.params["failure_mode"]
                applied_anomalies.append(anomaly_info)

        return result_temp, applied_anomalies

    def get_active_anomaly_descriptions(self, device_id: Optional[int] = None,
                                         shelf_id: Optional[int] = None) -> List[Dict]:
        anomalies = self._get_applicable_anomalies(device_id, shelf_id)
        return [
            {
                "id": a.id,
                "type": a.type.value,
                "device_id": a.device_id,
                "shelf_id": a.shelf_id,
                "sensor_id": a.sensor_id,
                "duration": a.duration,
                "strength": a.strength,
                "elapsed": round(time.time() - a.start_time, 1)
            }
            for a in anomalies
        ]

    def _auto_inject_loop(self):
        while self._running:
            if self._auto_enabled:
                now = time.time()
                if now - self._last_auto_inject >= self._auto_interval:
                    self._last_auto_inject = now
                    self._inject_random_anomaly()
            time.sleep(1)

    def _inject_random_anomaly(self) -> str:
        anomaly_types = list(AnomalyType)
        anomaly_type = random.choice(anomaly_types)
        device_id, shelf_id, sensor_id = self._get_random_target()

        duration = random.uniform(30, 180)
        strength = random.uniform(0.5, 2.0)

        anomaly_id = self.inject(
            anomaly_type,
            device_id=device_id,
            shelf_id=shelf_id if anomaly_type not in [AnomalyType.COLD_TRAP_HIGH] else None,
            sensor_id=sensor_id if anomaly_type in [
                AnomalyType.TEMP_SPIKE, AnomalyType.TEMP_OFFSET,
                AnomalyType.VACUUM_SPIKE, AnomalyType.VACUUM_DRIFT,
                AnomalyType.SENSOR_FAILURE, AnomalyType.RANDOM_NOISE
            ] else None,
            duration=duration,
            strength=strength
        )

        print(f"[Anomaly-Auto] 注入异常 {anomaly_id}: {anomaly_type.value} "
              f"设备={device_id} 搁板={shelf_id} 传感器={sensor_id} "
              f"持续={duration:.0f}s 强度={strength:.2f}")

        return anomaly_id

    def _get_random_target(self) -> Tuple[int, int, int]:
        device_id = random.randint(1, NUM_DEVICES)
        shelf_id = random.randint(1, NUM_SHELVES)
        sensor_id = random.randint(0, max(NUM_TEMP_SENSORS, NUM_VACUUM_SENSORS) - 1)
        return device_id, shelf_id, sensor_id


class FreezeDryerSimulator:
    def __init__(self, device_id: int, anomaly_injector: Optional[AnomalyInjector] = None):
        self.device_id = device_id
        self._lock = threading.Lock()
        self._packet_queue: "queue.Queue[ProfinetPacket]" = queue.Queue(maxsize=100)
        self.stats = ThreadStats()
        self.anomaly_injector = anomaly_injector

        with self._lock:
            self.base_temps = self._init_base_temps()
            self.heating_powers = [50.0 for _ in range(NUM_TEMP_SENSORS)]
            self.target_temp = -50.0
            self.base_vacuum = 1.0
            self.cold_trap_temp = -70.0
            self.temp_drift = 0.0
            self.control_adjustments = [0.0 for _ in range(NUM_TEMP_SENSORS)]
            self._last_report_time = time.perf_counter()
            self._cycle_count = 0
            self._batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{device_id:02d}"

    def _init_base_temps(self) -> List[float]:
        temps = []
        for i in range(NUM_SHELVES):
            shelf_base = -50.0 + i * 1.5 + random.uniform(-0.5, 0.5)
            temps.append(shelf_base)
        return temps

    def _generate_temperatures(self, shelf_id: int) -> Tuple[List[float], List[Dict]]:
        with self._lock:
            base = self.base_temps[shelf_id - 1]
            temps = []
            all_anomalies = []

            for i in range(NUM_TEMP_SENSORS):
                power_effect = (self.heating_powers[i] - 50) * 0.08
                control_effect = self.control_adjustments[i] * 0.1
                noise = random.gauss(0, 0.15)
                spatial_variation = (i - 3.5) * 0.1
                temp = base + power_effect + control_effect + noise + spatial_variation

                sensor_anomalies = []
                if self.anomaly_injector:
                    temp, sensor_anomalies = self.anomaly_injector.apply_temperature_anomalies(
                        self.device_id, shelf_id, i, temp
                    )

                if sensor_anomalies:
                    all_anomalies.append({
                        "shelf_id": shelf_id,
                        "sensor_idx": i,
                        "sensor_type": "temperature",
                        "anomalies": sensor_anomalies
                    })

                if math.isnan(temp):
                    temps.append(temp)
                else:
                    temps.append(round(temp, 2))

            return temps, all_anomalies

    def _generate_vacuum(self, shelf_id: int) -> Tuple[List[float], List[Dict]]:
        with self._lock:
            vacuums = []
            all_anomalies = []

            for i in range(NUM_VACUUM_SENSORS):
                variation = random.gauss(0, 0.05)
                vacuum = max(0.01, self.base_vacuum + variation + i * 0.02)

                sensor_anomalies = []
                if self.anomaly_injector:
                    vacuum, sensor_anomalies = self.anomaly_injector.apply_vacuum_anomalies(
                        self.device_id, shelf_id, i, vacuum
                    )

                if sensor_anomalies:
                    all_anomalies.append({
                        "shelf_id": shelf_id,
                        "sensor_idx": i,
                        "sensor_type": "vacuum",
                        "anomalies": sensor_anomalies
                    })

                if math.isnan(vacuum):
                    vacuums.append(vacuum)
                else:
                    vacuums.append(round(vacuum, 4))

            return vacuums, all_anomalies

    def _generate_cold_trap_temp(self) -> Tuple[float, List[Dict]]:
        with self._lock:
            variation = random.gauss(0, 0.5)
            temp = self.cold_trap_temp + variation
            anomalies = []

            if self.anomaly_injector:
                temp, anomalies = self.anomaly_injector.apply_cold_trap_anomaly(
                    self.device_id, temp
                )

            if math.isnan(temp):
                return temp, anomalies
            return round(temp, 2), anomalies

    def _generate_heating_powers(self) -> List[float]:
        with self._lock:
            powers = []
            for i in range(NUM_TEMP_SENSORS):
                variation = random.gauss(0, 1.0)
                power = max(0, min(100, self.heating_powers[i] + variation))
                powers.append(round(power, 1))
            return powers

    def apply_control_adjustment(self, adjustments: List[float]):
        with self._lock:
            for i in range(min(len(adjustments), NUM_TEMP_SENSORS)):
                self.control_adjustments[i] = max(-20, min(20, adjustments[i]))
                self.heating_powers[i] = max(0, min(100, self.heating_powers[i] + adjustments[i] * 0.5))

    def update_state(self):
        with self._lock:
            self.temp_drift += random.gauss(0, 0.01)
            for i in range(len(self.base_temps)):
                self.base_temps[i] += random.gauss(0, 0.02)
                self.base_temps[i] = max(-60, min(-40, self.base_temps[i]))

            if random.random() < 0.02:
                self.base_vacuum += random.uniform(-0.1, 0.1)
                self.base_vacuum = max(0.05, min(5.0, self.base_vacuum))

    def generate_telemetry(self) -> List[Dict]:
        telemetry_list = []
        timestamp = datetime.now(timezone.utc).isoformat()
        cycle_id = self._cycle_count

        for shelf_id in range(1, NUM_SHELVES + 1):
            temps, temp_anomalies = self._generate_temperatures(shelf_id)
            vacuums, vacuum_anomalies = self._generate_vacuum(shelf_id)
            cold_trap_temp, cold_trap_anomalies = self._generate_cold_trap_temp()

            all_anomalies = temp_anomalies + vacuum_anomalies
            if cold_trap_anomalies:
                all_anomalies.append({
                    "sensor_type": "cold_trap",
                    "anomalies": cold_trap_anomalies
                })

            if self.anomaly_injector:
                active_anomalies = self.anomaly_injector.get_active_anomaly_descriptions(
                    self.device_id, shelf_id
                )
            else:
                active_anomalies = []

            telemetry = {
                "device_id": self.device_id,
                "shelf_id": shelf_id,
                "timestamp": timestamp,
                "cycle_id": cycle_id,
                "batch_id": self._batch_id,
                "temperatures": temps,
                "vacuum_levels": vacuums,
                "cold_trap_temp": cold_trap_temp,
                "heating_powers": self._generate_heating_powers(),
                "profinet_flags": {
                    "rt_valid": True,
                    "data_quality": random.choice([0, 0, 0, 1])
                },
                "anomalies": {
                    "applied": all_anomalies,
                    "active": active_anomalies
                } if all_anomalies or active_anomalies else None
            }
            telemetry_list.append(telemetry)

        with self._lock:
            self._cycle_count += 1

        return telemetry_list

    def inject_anomaly(self, anomaly_type: str):
        with self._lock:
            if anomaly_type == "temp_spike":
                shelf = random.randint(0, NUM_SHELVES - 1)
                self.base_temps[shelf] += random.uniform(2, 4)
            elif anomaly_type == "vacuum_leak":
                self.base_vacuum += random.uniform(5, 20)
            elif anomaly_type == "cold_trap_warm":
                self.cold_trap_temp += random.uniform(10, 20)

    def start_new_batch(self):
        with self._lock:
            self._batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d')}-{self.device_id:02d}"
            self._cycle_count = 0
            self.control_adjustments = [0.0 for _ in range(NUM_TEMP_SENSORS)]
            print(f"[Device {self.device_id}] 新批次开始: {self._batch_id}")

    def _enqueue_packet(self, packet: ProfinetPacket):
        try:
            self._packet_queue.put_nowait(packet)
        except queue.Full:
            self.stats.packets_dropped += 1
            if self.stats.packets_dropped % 10 == 0:
                print(f"[Device {self.device_id}] 队列溢出，丢包数: {self.stats.packets_dropped}")


class ProfinetRTThread(threading.Thread):
    def __init__(self, simulator: FreezeDryerSimulator):
        super().__init__(daemon=True, name=f"Profinet-RT-Device-{simulator.device_id}")
        self.simulator = simulator
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session: Optional[aiohttp.ClientSession] = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_run())
        finally:
            self._loop.run_until_complete(self._cleanup())
            self._loop.close()

    async def _async_run(self):
        timeout = aiohttp.ClientTimeout(total=5.0, connect=2.0)
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)

        next_report_time = time.perf_counter()
        cycle_count = 0

        while not self._stop_event.is_set():
            cycle_start = time.perf_counter()
            cycle_deviation = cycle_start - next_report_time
            self.simulator.stats.cycle_deviation_ms = cycle_deviation * 1000

            try:
                await self._rt_cycle()

                if cycle_count % 6 == 0:
                    await self._nrt_cycle()

            except Exception as e:
                print(f"[Device {self.simulator.device_id}] 周期异常: {e}")

            cycle_count += 1

            cycle_duration = time.perf_counter() - cycle_start
            sleep_time = max(0, REPORT_INTERVAL - cycle_duration)

            next_report_time = cycle_start + REPORT_INTERVAL
            await self._high_precision_sleep(sleep_time)

    async def _rt_cycle(self):
        t0 = time.perf_counter()

        telemetry_list = self.simulator.generate_telemetry()
        self.simulator.update_state()

        for telemetry in telemetry_list:
            packet = ProfinetPacket(
                priority=DataPriority.RT,
                device_id=self.simulator.device_id,
                data=telemetry,
                timestamp=time.perf_counter()
            )
            await self._send_packet(packet)

        if random.random() < 0.01:
            await self._fetch_control_commands()

        t1 = time.perf_counter()
        latency = (t1 - t0) * 1000
        self.simulator.stats.avg_latency_ms = (
            self.simulator.stats.avg_latency_ms * 0.95 + latency * 0.05
        )
        self.simulator.stats.max_jitter_ms = max(
            self.simulator.stats.max_jitter_ms, abs(latency - self.simulator.stats.avg_latency_ms)
        )
        self.simulator.stats.packets_sent += len(telemetry_list)

    async def _nrt_cycle(self):
        try:
            async with self._session.get(
                f"{BASE_URL}/api/devices/{self.simulator.device_id}/status"
            ) as response:
                if response.status == 200:
                    status = await response.json()
                    if status.get("needs_batch_reset"):
                        self.simulator.start_new_batch()
        except Exception:
            pass

    async def _send_packet(self, packet: ProfinetPacket):
        if self._session is None:
            return

        try:
            async with self._session.post(
                f"{BASE_URL}/api/data/telemetry",
                json=packet.data,
                headers={"X-Profinet-Priority": str(packet.priority.value)}
            ) as response:
                if response.status != 200:
                    if packet.retry_count < 3:
                        packet.retry_count += 1
                        asyncio.create_task(self._retry_packet(packet))
                    else:
                        self.simulator.stats.packets_dropped += 1
        except aiohttp.ClientError:
            if packet.retry_count < 3:
                packet.retry_count += 1
                asyncio.create_task(self._retry_packet(packet, delay=0.5))
            else:
                self.simulator.stats.packets_dropped += 1

    async def _retry_packet(self, packet: ProfinetPacket, delay: float = 0.1):
        await asyncio.sleep(delay)
        await self._send_packet(packet)

    async def _fetch_control_commands(self):
        if self._session is None:
            return

        try:
            async with self._session.get(
                f"{BASE_URL}/api/control/latest/{self.simulator.device_id}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and data.get("auto_mode"):
                        adjustments = [
                            data.get(f"power_adj_{i}", 0) or 0
                            for i in range(1, NUM_TEMP_SENSORS + 1)
                        ]
                        self.simulator.apply_control_adjustment(adjustments)
        except Exception:
            pass

    async def _high_precision_sleep(self, duration: float):
        if duration <= 0:
            return
        if duration > 0.05:
            await asyncio.sleep(duration - 0.05)
        target = time.perf_counter() + duration
        while time.perf_counter() < target:
            await asyncio.sleep(0)

    async def _cleanup(self):
        if self._session:
            await self._session.close()

    def stop(self):
        self._stop_event.set()


class ControlCommandFetcher(threading.Thread):
    def __init__(self, simulators: Dict[int, FreezeDryerSimulator]):
        super().__init__(daemon=True, name="Control-Command-Fetcher")
        self.simulators = simulators
        self._stop_event = threading.Event()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._fetch_loop())
        finally:
            loop.close()

    async def _fetch_loop(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
            while not self._stop_event.is_set():
                for device_id, sim in self.simulators.items():
                    try:
                        async with session.get(
                            f"{BASE_URL}/api/control/latest/{device_id}"
                        ) as response:
                            if response.status == 200:
                                data = await response.json()
                                if data and data.get("auto_mode"):
                                    adjustments = [
                                        data.get(f"power_adj_{i}", 0) or 0
                                        for i in range(1, NUM_TEMP_SENSORS + 1)
                                    ]
                                    sim.apply_control_adjustment(adjustments)
                    except Exception:
                        pass
                await asyncio.sleep(REPORT_INTERVAL)

    def stop(self):
        self._stop_event.set()


class StatsMonitor(threading.Thread):
    def __init__(self, simulators: Dict[int, FreezeDryerSimulator], anomaly_injector: Optional[AnomalyInjector] = None):
        super().__init__(daemon=True, name="Stats-Monitor")
        self.simulators = simulators
        self.anomaly_injector = anomaly_injector
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=30):
                break

            total_sent = sum(s.stats.packets_sent for s in self.simulators.values())
            total_dropped = sum(s.stats.packets_dropped for s in self.simulators.values())
            avg_latency = sum(s.stats.avg_latency_ms for s in self.simulators.values()) / len(self.simulators)
            max_jitter = max(s.stats.max_jitter_ms for s in self.simulators.values())
            max_deviation = max(s.stats.cycle_deviation_ms for s in self.simulators.values())

            anomaly_info = ""
            if self.anomaly_injector:
                active = self.anomaly_injector.list_active()
                auto_status = "ON" if self.anomaly_injector.is_auto_enabled() else "OFF"
                anomaly_info = f" | 活跃异常: {len(active)} | 自动注入: {auto_status}"

            print("\n" + "=" * 70)
            print(f"[Stats] 运行统计 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  总发送包数: {total_sent:,} | 丢包数: {total_dropped:,}")
            print(f"  平均延迟: {avg_latency:.2f}ms | 最大抖动: {max_jitter:.2f}ms")
            print(f"  最大周期偏差: {max_deviation:.2f}ms")
            print(f"  丢包率: {total_dropped / max(total_sent, 1) * 100:.4f}%{anomaly_info}")
            print("=" * 70 + "\n")

    def stop(self):
        self._stop_event.set()


class StdinHandler(threading.Thread):
    def __init__(self, simulators: Dict[int, FreezeDryerSimulator], anomaly_injector: AnomalyInjector):
        super().__init__(daemon=True, name="Stdin-Handler")
        self.simulators = simulators
        self.anomaly_injector = anomaly_injector
        self._stop_event = threading.Event()
        self._commands = {
            "anomaly": self._handle_anomaly_command,
            "status": self._handle_status,
            "help": self._handle_help,
            "batch": self._handle_batch_command,
        }

    def run(self):
        print("\n[Stdin] 命令行接口已启动，输入 'help' 查看可用命令")
        while not self._stop_event.is_set():
            try:
                line = input("> ").strip()
                if line:
                    self._handle_command(line)
            except EOFError:
                break
            except Exception as e:
                print(f"[Stdin] 命令处理错误: {e}")

    def _handle_command(self, line: str):
        parts = line.split()
        if not parts:
            return

        cmd = parts[0].lower()
        handler = self._commands.get(cmd)
        if handler:
            try:
                handler(parts[1:])
            except Exception as e:
                print(f"[Stdin] 命令执行失败: {e}")
        else:
            print(f"[Stdin] 未知命令: {cmd}，输入 'help' 查看可用命令")

    def _handle_anomaly_command(self, args: List[str]):
        if not args:
            print("[Stdin] 用法: anomaly <inject|clear|list|auto> ...")
            return

        subcmd = args[0].lower()
        if subcmd == "inject":
            self._handle_anomaly_inject(args[1:])
        elif subcmd == "clear":
            self._handle_anomaly_clear(args[1:])
        elif subcmd == "list":
            self._handle_anomaly_list()
        elif subcmd == "auto":
            self._handle_anomaly_auto(args[1:])
        else:
            print(f"[Stdin] 未知子命令: {subcmd}")

    def _parse_anomaly_args(self, args: List[str]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        i = 0
        while i < len(args):
            if args[i] == "--device" and i + 1 < len(args):
                kwargs["device_id"] = int(args[i + 1])
                i += 2
            elif args[i] == "--shelf" and i + 1 < len(args):
                kwargs["shelf_id"] = int(args[i + 1])
                i += 2
            elif args[i] == "--sensor" and i + 1 < len(args):
                kwargs["sensor_id"] = int(args[i + 1])
                i += 2
            elif args[i] == "--duration" and i + 1 < len(args):
                kwargs["duration"] = float(args[i + 1])
                i += 2
            elif args[i] == "--strength" and i + 1 < len(args):
                kwargs["strength"] = float(args[i + 1])
                i += 2
            else:
                i += 1
        return kwargs

    def _handle_anomaly_inject(self, args: List[str]):
        if not args:
            print("[Stdin] 用法: anomaly inject <type> [--device N] [--shelf N] [--sensor N] [--duration S] [--strength F]")
            print("       类型: temp_spike, temp_offset, vacuum_spike, vacuum_drift, sensor_failure, cold_trap_high, random_noise")
            return

        type_str = args[0].lower()
        try:
            anomaly_type = AnomalyType(type_str)
        except ValueError:
            print(f"[Stdin] 未知异常类型: {type_str}")
            return

        kwargs = self._parse_anomaly_args(args[1:])
        anomaly_id = self.anomaly_injector.inject(anomaly_type, **kwargs)

        target_info = []
        if kwargs.get("device_id"):
            target_info.append(f"设备={kwargs['device_id']}")
        else:
            target_info.append("设备=ALL")
        if kwargs.get("shelf_id"):
            target_info.append(f"搁板={kwargs['shelf_id']}")
        if kwargs.get("sensor_id") is not None:
            target_info.append(f"传感器={kwargs['sensor_id']}")

        print(f"[Stdin] 异常已注入: {anomaly_id} [{type_str}] " + " ".join(target_info) +
              f" 持续={kwargs.get('duration', 60)}s 强度={kwargs.get('strength', 1.0)}")

    def _handle_anomaly_clear(self, args: List[str]):
        if not args:
            print("[Stdin] 用法: anomaly clear <anomaly_id>")
            return

        anomaly_id = args[0]
        if self.anomaly_injector.clear(anomaly_id):
            print(f"[Stdin] 异常已清除: {anomaly_id}")
        else:
            print(f"[Stdin] 未找到异常: {anomaly_id}")

    def _handle_anomaly_list(self):
        active = self.anomaly_injector.list_active()
        if not active:
            print("[Stdin] 当前无活跃异常")
            return

        print(f"[Stdin] 活跃异常列表 ({len(active)}个):")
        for a in active:
            elapsed = time.time() - a.start_time
            remaining = a.duration - elapsed if a.duration > 0 else "无限"
            device = a.device_id if a.device_id is not None else "ALL"
            shelf = a.shelf_id if a.shelf_id is not None else "ALL"
            sensor = a.sensor_id if a.sensor_id is not None else "ALL"
            print(f"  {a.id}: {a.type.value:20s} 设备={device:>3s} 搁板={shelf:>3s} 传感器={sensor:>3s} "
                  f"已运行={elapsed:6.1f}s 剩余={remaining if isinstance(remaining, str) else f'{remaining:6.1f}s'} "
                  f"强度={a.strength:.2f}")

    def _handle_anomaly_auto(self, args: List[str]):
        if not args:
            status = "ON" if self.anomaly_injector.is_auto_enabled() else "OFF"
            print(f"[Stdin] 自动异常注入当前状态: {status}")
            return

        mode = args[0].lower()
        if mode == "on":
            self.anomaly_injector.set_auto_enabled(True)
            print("[Stdin] 自动异常注入已开启")
        elif mode == "off":
            self.anomaly_injector.set_auto_enabled(False)
            print("[Stdin] 自动异常注入已关闭")
        else:
            print("[Stdin] 用法: anomaly auto <on|off>")

    def _handle_status(self, args: List[str]):
        total_sent = sum(s.stats.packets_sent for s in self.simulators.values())
        total_dropped = sum(s.stats.packets_dropped for s in self.simulators.values())
        active = self.anomaly_injector.list_active()
        auto_status = "ON" if self.anomaly_injector.is_auto_enabled() else "OFF"

        print("\n" + "-" * 50)
        print("[Status] 模拟器状态")
        print(f"  设备数量: {len(self.simulators)}")
        print(f"  总发送包数: {total_sent:,}")
        print(f"  总丢包数: {total_dropped:,}")
        print(f"  活跃异常数: {len(active)}")
        print(f"  自动异常注入: {auto_status}")
        print(f"  上报间隔: {REPORT_INTERVAL}s")
        print(f"  上报地址: {BASE_URL}")
        print("-" * 50 + "\n")

        if active:
            self._handle_anomaly_list()

    def _handle_batch_command(self, args: List[str]):
        if not args:
            print("[Stdin] 用法: batch new [device_id]")
            return

        subcmd = args[0].lower()
        if subcmd == "new":
            if len(args) > 1:
                device_id = int(args[1])
                if device_id in self.simulators:
                    self.simulators[device_id].start_new_batch()
                else:
                    print(f"[Stdin] 未找到设备: {device_id}")
            else:
                for sim in self.simulators.values():
                    sim.start_new_batch()
                print("[Stdin] 所有设备已开始新批次")

    def _handle_help(self, args: List[str]):
        print("\n" + "=" * 60)
        print("可用命令:")
        print("  status                          查看模拟器状态")
        print("  help                            显示此帮助信息")
        print()
        print("异常注入命令:")
        print("  anomaly inject <type> [options] 注入异常")
        print("    类型: temp_spike, temp_offset, vacuum_spike,")
        print("          vacuum_drift, sensor_failure, cold_trap_high,")
        print("          random_noise")
        print("    选项:")
        print("      --device N      指定设备 (默认所有)")
        print("      --shelf N       指定搁板 (默认所有)")
        print("      --sensor N      指定传感器索引 (默认所有)")
        print("      --duration S    持续秒数 (默认60)")
        print("      --strength F    强度系数 (默认1.0)")
        print("  anomaly clear <id>              清除指定异常")
        print("  anomaly list                    列出活跃异常")
        print("  anomaly auto <on|off>           开关自动异常注入")
        print()
        print("批次命令:")
        print("  batch new [device_id]           开始新批次")
        print("=" * 60 + "\n")

    def stop(self):
        self._stop_event.set()


def main():
    print("=" * 70)
    print("Profinet RT 模拟器启动 - 多线程实时性优化版 + 异常注入增强")
    print(f"设备数量: {NUM_DEVICES}")
    print(f"每台搁板数: {NUM_SHELVES}")
    print(f"温度传感器/层: {NUM_TEMP_SENSORS}")
    print(f"真空度传感器/层: {NUM_VACUUM_SENSORS}")
    print(f"上报间隔: {REPORT_INTERVAL}秒")
    print(f"上报地址: {BASE_URL}")
    print(f"异常注入: {'启用' if SIM_ENABLE_ANOMALY else '禁用'}")
    print(f"自动异常间隔: {SIM_ANOMALY_INTERVAL}秒")
    print(f"Redis: {REDIS_HOST}:{REDIS_PORT}")
    print("\n优化特性:")
    print("  ✓ 每设备独立线程 + 独立事件循环")
    print("  ✓ aiohttp异步HTTP驱动")
    print("  ✓ 高精度周期补偿")
    print("  ✓ RT/NRT优先级队列")
    print("  ✓ 数据包重试机制")
    print("  ✓ 实时性能监控")
    print("  ✓ 异常注入系统（手动/自动）")
    print("  ✓ 命令行交互接口")
    print("=" * 70)

    anomaly_injector = AnomalyInjector(
        enabled=SIM_ENABLE_ANOMALY,
        auto_interval=SIM_ANOMALY_INTERVAL
    )
    anomaly_injector.start()
    print(f"[Start] 异常注入器启动 (自动: {'ON' if SIM_ENABLE_ANOMALY else 'OFF'})")

    simulators = {
        i: FreezeDryerSimulator(i, anomaly_injector) for i in range(1, NUM_DEVICES + 1)
    }

    threads: List[threading.Thread] = []

    for sim in simulators.values():
        rt_thread = ProfinetRTThread(sim)
        threads.append(rt_thread)
        rt_thread.start()
        print(f"[Start] RT线程启动: Device {sim.device_id}")

    control_fetcher = ControlCommandFetcher(simulators)
    threads.append(control_fetcher)
    control_fetcher.start()
    print(f"[Start] 控制指令获取线程启动")

    stats_monitor = StatsMonitor(simulators, anomaly_injector)
    threads.append(stats_monitor)
    stats_monitor.start()
    print(f"[Start] 性能监控线程启动")

    stdin_handler = StdinHandler(simulators, anomaly_injector)
    threads.append(stdin_handler)
    stdin_handler.start()
    print(f"[Start] 命令行接口线程启动")

    print(f"\n所有线程已启动，共 {len(threads)} 个线程")
    print("按 Ctrl+C 停止，或输入命令进行交互...\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n正在停止所有线程...")
        anomaly_injector.stop()
        for t in threads:
            if hasattr(t, 'stop'):
                t.stop()

        for t in threads:
            t.join(timeout=5.0)
            print(f"[Stop] 线程 {t.name} 已停止")

        print("\n模拟器已安全退出")


if __name__ == "__main__":
    main()
