"""
Profinet 协议模拟器
模拟10台冻干机，每台5层搁板，每层8个温度传感器+2个真空度传感器
每10秒上报一次数据
"""

import asyncio
import json
import random
import time
from datetime import datetime, timezone
from typing import List, Dict, Tuple
import httpx

BASE_URL = "http://localhost:8000"
NUM_DEVICES = 10
NUM_SHELVES = 5
NUM_TEMP_SENSORS = 8
NUM_VACUUM_SENSORS = 2
REPORT_INTERVAL = 10


class FreezeDryerSimulator:
    def __init__(self, device_id: int):
        self.device_id = device_id
        self.base_temps = self._init_base_temps()
        self.heating_powers = [50.0 for _ in range(NUM_TEMP_SENSORS)]
        self.target_temp = -50.0
        self.base_vacuum = 1.0
        self.cold_trap_temp = -70.0
        self.temp_drift = 0.0
        self.control_adjustments = [0.0 for _ in range(NUM_TEMP_SENSORS)]

    def _init_base_temps(self) -> List[float]:
        temps = []
        for i in range(NUM_SHELVES):
            shelf_base = -50.0 + i * 1.5 + random.uniform(-0.5, 0.5)
            temps.append(shelf_base)
        return temps

    def _generate_temperatures(self, shelf_id: int) -> List[float]:
        base = self.base_temps[shelf_id - 1]
        temps = []
        for i in range(NUM_TEMP_SENSORS):
            power_effect = (self.heating_powers[i] - 50) * 0.08
            control_effect = self.control_adjustments[i] * 0.1
            noise = random.gauss(0, 0.15)
            spatial_variation = (i - 3.5) * 0.1
            temp = base + power_effect + control_effect + noise + spatial_variation
            temps.append(round(temp, 2))
        return temps

    def _generate_vacuum(self) -> List[float]:
        vacuums = []
        for i in range(NUM_VACUUM_SENSORS):
            variation = random.gauss(0, 0.05)
            vacuum = max(0.01, self.base_vacuum + variation + i * 0.02)
            vacuums.append(round(vacuum, 4))
        return vacuums

    def _generate_cold_trap_temp(self) -> float:
        variation = random.gauss(0, 0.5)
        return round(self.cold_trap_temp + variation, 2)

    def _generate_heating_powers(self) -> List[float]:
        powers = []
        for i in range(NUM_TEMP_SENSORS):
            variation = random.gauss(0, 1.0)
            power = max(0, min(100, self.heating_powers[i] + variation))
            powers.append(round(power, 1))
        return powers

    def apply_control_adjustment(self, adjustments: List[float]):
        for i in range(min(len(adjustments), NUM_TEMP_SENSORS)):
            self.control_adjustments[i] = max(-20, min(20, adjustments[i]))
            self.heating_powers[i] = max(0, min(100, self.heating_powers[i] + adjustments[i] * 0.5))

    def update_state(self):
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

        for shelf_id in range(1, NUM_SHELVES + 1):
            telemetry = {
                "device_id": self.device_id,
                "shelf_id": shelf_id,
                "timestamp": timestamp,
                "temperatures": self._generate_temperatures(shelf_id),
                "vacuum_levels": self._generate_vacuum(),
                "cold_trap_temp": self._generate_cold_trap_temp(),
                "heating_powers": self._generate_heating_powers()
            }
            telemetry_list.append(telemetry)

        return telemetry_list

    def inject_anomaly(self, anomaly_type: str):
        if anomaly_type == "temp_spike":
            shelf = random.randint(0, NUM_SHELVES - 1)
            self.base_temps[shelf] += random.uniform(2, 4)
        elif anomaly_type == "vacuum_leak":
            self.base_vacuum += random.uniform(5, 20)
        elif anomaly_type == "cold_trap_warm":
            self.cold_trap_temp += random.uniform(10, 20)


async def fetch_control_commands(simulators: Dict[int, FreezeDryerSimulator]):
    while True:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                for device_id, sim in simulators.items():
                    try:
                        response = await client.get(
                            f"{BASE_URL}/api/control/latest/{device_id}"
                        )
                        if response.status_code == 200:
                            data = response.json()
                            if data and data.get("auto_mode"):
                                adjustments = [
                                    data.get(f"power_adj_{i}", 0) or 0
                                    for i in range(1, NUM_TEMP_SENSORS + 1)
                                ]
                                sim.apply_control_adjustment(adjustments)
                    except Exception as e:
                        pass
        except Exception as e:
            pass
        await asyncio.sleep(REPORT_INTERVAL)


async def send_telemetry(simulator: FreezeDryerSimulator):
    while True:
        try:
            telemetry_list = simulator.generate_telemetry()
            simulator.update_state()

            async with httpx.AsyncClient(timeout=5.0) as client:
                for telemetry in telemetry_list:
                    try:
                        response = await client.post(
                            f"{BASE_URL}/api/data/telemetry",
                            json=telemetry
                        )
                        if response.status_code != 200:
                            print(f"Device {simulator.device_id} send failed: {response.status_code}")
                    except Exception as e:
                        pass
        except Exception as e:
            print(f"Device {simulator.device_id} error: {e}")

        await asyncio.sleep(REPORT_INTERVAL)


async def anomaly_injector(simulators: Dict[int, FreezeDryerSimulator]):
    anomaly_types = ["temp_spike", "vacuum_leak", "cold_trap_warm"]
    while True:
        await asyncio.sleep(random.randint(60, 180))
        if random.random() < 0.3:
            device_id = random.choice(list(simulators.keys()))
            anomaly = random.choice(anomaly_types)
            print(f"Injecting {anomaly} into device {device_id}")
            simulators[device_id].inject_anomaly(anomaly)


async def main():
    print("=" * 60)
    print("Profinet 模拟器启动")
    print(f"设备数量: {NUM_DEVICES}")
    print(f"每台搁板数: {NUM_SHELVES}")
    print(f"温度传感器/层: {NUM_TEMP_SENSORS}")
    print(f"真空度传感器/层: {NUM_VACUUM_SENSORS}")
    print(f"上报间隔: {REPORT_INTERVAL}秒")
    print(f"上报地址: {BASE_URL}")
    print("=" * 60)

    simulators = {
        i: FreezeDryerSimulator(i) for i in range(1, NUM_DEVICES + 1)
    }

    tasks = []
    for sim in simulators.values():
        tasks.append(asyncio.create_task(send_telemetry(sim)))

    tasks.append(asyncio.create_task(fetch_control_commands(simulators)))
    tasks.append(asyncio.create_task(anomaly_injector(simulators)))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n模拟器停止")


if __name__ == "__main__":
    asyncio.run(main())
