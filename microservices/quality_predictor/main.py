"""
质量预测微服务
基于PLS回归和迁移学习预测冻干产品水分含量和复溶时间

功能：
1. 订阅 Redis telemetry:raw 频道接收遥测数据
2. 维护1小时历史数据窗口
3. 使用PLS回归+迁移学习预测质量参数
4. 发布预测结果到 prediction:result 频道
5. 支持配置热更新和配方切换
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Deque, Tuple
from collections import deque
from dataclasses import dataclass
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from shared import (
    MicroserviceBase, RedisConfig,
    CHANNELS, SERVICE_IDS, MESSAGE_TYPES,
    PredictionResult, MessageFactory,
    validate_message, extract_payload,
    config_loader, ModelConfig
)

from app.services.prediction import AdaptivePLSPredictor, QualityPredictionService


WINDOW_SECONDS = 3600
PREDICTION_INTERVAL = 60
NUM_DEVICES = 10
NUM_SHELVES = 5


@dataclass
class TelemetryWindow:
    temperatures: Deque[List[float]]
    vacuum_levels: Deque[List[float]]
    heating_powers: Deque[List[float]]
    cold_trap_temp: Deque[float]
    timestamps: Deque[datetime]

    def __init__(self, max_seconds: int = WINDOW_SECONDS):
        self.max_seconds = max_seconds
        max_samples = max_seconds // 10 + 10
        self.temperatures = deque(maxlen=max_samples)
        self.vacuum_levels = deque(maxlen=max_samples)
        self.heating_powers = deque(maxlen=max_samples)
        self.cold_trap_temp = deque(maxlen=max_samples)
        self.timestamps = deque(maxlen=max_samples)

    def add(self, temperatures: List[float], vacuum_levels: List[float],
            heating_powers: List[float], cold_trap_temp: float,
            timestamp: datetime):
        self.temperatures.append(temperatures)
        self.vacuum_levels.append(vacuum_levels)
        self.heating_powers.append(heating_powers)
        self.cold_trap_temp.append(cold_trap_temp)
        self.timestamps.append(timestamp)
        self._cleanup()

    def _cleanup(self):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.max_seconds)
        while self.timestamps and self.timestamps[0] < cutoff:
            self.temperatures.popleft()
            self.vacuum_levels.popleft()
            self.heating_powers.popleft()
            self.cold_trap_temp.popleft()
            self.timestamps.popleft()

    def get_data(self) -> Tuple[List[List[float]], List[List[float]], List[List[float]], List[float]]:
        self._cleanup()
        temp_list = [[np.mean(t)] for t in self.temperatures]
        vac_list = [[np.mean(v)] for v in self.vacuum_levels]
        power_list = [[np.mean(p)] for p in self.heating_powers]
        cold_list = list(self.cold_trap_temp)
        return temp_list, vac_list, power_list, cold_list

    def __len__(self) -> int:
        self._cleanup()
        return len(self.timestamps)


class QualityPredictorService(MicroserviceBase):
    def __init__(self, redis_config: Optional[RedisConfig] = None):
        super().__init__(
            service_id=SERVICE_IDS['QUALITY_PREDICTOR'],
            service_type="quality_prediction",
            redis_config=redis_config
        )

        self._prediction_service: Optional[QualityPredictionService] = None
        self._telemetry_windows: Dict[int, Dict[int, TelemetryWindow]] = {}
        self._device_cold_trap: Dict[int, TelemetryWindow] = {}
        self._prediction_interval = PREDICTION_INTERVAL
        self._current_formulas: Dict[int, str] = {}
        self._batch_ids: Dict[int, Optional[str]] = {}
        self._num_devices = NUM_DEVICES
        self._num_shelves = NUM_SHELVES
        self._last_predictions: Dict[int, Dict] = {}

        self._init_data_structures()
        self._load_config()

    def _init_data_structures(self):
        for device_id in range(1, self._num_devices + 1):
            self._telemetry_windows[device_id] = {}
            for shelf_id in range(1, self._num_shelves + 1):
                self._telemetry_windows[device_id][shelf_id] = TelemetryWindow(WINDOW_SECONDS)
            self._device_cold_trap[device_id] = TelemetryWindow(WINDOW_SECONDS)
            self._current_formulas[device_id] = "FORMULA-001"
            self._batch_ids[device_id] = None

    def _load_config(self):
        model_config = config_loader.load_model_config()
        self._apply_model_config(model_config)

    def _apply_model_config(self, model_config: ModelConfig):
        if self._prediction_service is None:
            self._prediction_service = QualityPredictionService(n_devices=self._num_devices)

        pls_config = model_config.pls_model
        transfer_config = model_config.transfer_learning
        adaptive_config = model_config.adaptive_update
        drift_config = model_config.concept_drift

        for device_id in range(1, self._num_devices + 1):
            predictor = self._prediction_service.get_predictor(device_id)

            n_components = pls_config.get('n_components', 6)
            predictor.n_components = n_components

            transfer_alpha = transfer_config.get('transfer_alpha', 0.7)
            predictor._transfer_alpha = transfer_alpha

            initial_adaptation_rate = adaptive_config.get('initial_adaptation_rate', 0.1)
            predictor._adaptation_rate = initial_adaptation_rate

            drift_threshold = drift_config.get('drift_threshold', 0.05)
            predictor._drift_threshold = drift_threshold

        for formula_data in model_config.formulas:
            formula_id = formula_data.get('formula_id')
            formula_name = formula_data.get('formula_name')
            product_type = formula_data.get('product_type')
            target_moisture = formula_data.get('target_moisture', 3.0)
            target_reconstitution = formula_data.get('target_reconstitution', 120.0)
            freeze_curve = formula_data.get('freeze_curve', {})

            if formula_id:
                for device_id in range(1, self._num_devices + 1):
                    predictor = self._prediction_service.get_predictor(device_id)
                    if formula_id not in predictor._formula_library:
                        predictor.register_formula(
                            formula_id, formula_name, product_type,
                            target_moisture, target_reconstitution, freeze_curve
                        )

        print(f"[{self.service_id}] 模型配置已加载")

    async def _subscribe_channels(self):
        await self.subscribe(CHANNELS['TELEMETRY_RAW'], self._handle_telemetry)
        await self.subscribe(CHANNELS['CONFIG_UPDATE'], self._handle_config_update)
        await self.subscribe(CHANNELS['PREDICTION_CONFIG'], self._handle_prediction_config)

    async def _on_start(self):
        print(f"[{self.service_id}] 初始化质量预测服务...")

        for device_id in range(1, self._num_devices + 1):
            formula_id = self._current_formulas.get(device_id, "FORMULA-001")
            if self._prediction_service:
                self._prediction_service.set_formula(device_id, formula_id)

        prediction_task = asyncio.create_task(self._prediction_loop())
        self._sub_tasks.append(prediction_task)

        await asyncio.sleep(1)
        print(f"[{self.service_id}] 质量预测服务初始化完成")
        print(f"[{self.service_id}] 预测间隔: {self._prediction_interval}s")
        print(f"[{self.service_id}] 历史窗口: {WINDOW_SECONDS}s ({WINDOW_SECONDS / 3600}小时)")

    async def _handle_telemetry(self, message: Dict):
        if not validate_message(message, MESSAGE_TYPES['TELEMETRY']):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')
        shelf_id = payload.get('shelf_id')

        if device_id is None or shelf_id is None:
            return

        self._increment_metric("messages_received")

        try:
            temperatures = payload.get('temperatures', [])
            vacuum_levels = payload.get('vacuum_levels', [])
            heating_powers = payload.get('heating_powers', [])
            cold_trap_temp = payload.get('cold_trap_temp', -70.0)
            batch_id = payload.get('batch_id')
            timestamp_str = payload.get('timestamp', datetime.now(timezone.utc).isoformat())

            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                timestamp = datetime.now(timezone.utc)

            if batch_id:
                self._batch_ids[device_id] = batch_id

            if device_id in self._telemetry_windows and shelf_id in self._telemetry_windows[device_id]:
                self._telemetry_windows[device_id][shelf_id].add(
                    temperatures, vacuum_levels, heating_powers, cold_trap_temp, timestamp
                )

            if device_id in self._device_cold_trap:
                self._device_cold_trap[device_id].add(
                    temperatures, vacuum_levels, heating_powers, cold_trap_temp, timestamp
                )

            if self._prediction_service:
                self._prediction_service.add_telemetry(
                    device_id, shelf_id, temperatures, vacuum_levels,
                    heating_powers, cold_trap_temp
                )

        except Exception as e:
            print(f"[{self.service_id}] 处理遥测数据失败 (设备{device_id}): {e}")
            self._increment_metric("errors")

    async def _handle_config_update(self, message: Dict):
        if not validate_message(message, MESSAGE_TYPES['CONFIG']):
            return

        payload = extract_payload(message)
        config_type = payload.get('config_type')

        if config_type in ['prediction', 'model', 'all']:
            print(f"[{self.service_id}] 收到配置更新通知")
            try:
                config_loader.reload_all()
                model_config = config_loader.load_model_config()
                self._apply_model_config(model_config)
                self._increment_metric("config_updates")
            except Exception as e:
                print(f"[{self.service_id}] 配置更新失败: {e}")
                self._increment_metric("errors")

    async def _handle_prediction_config(self, message: Dict):
        if not validate_message(message, MESSAGE_TYPES['CONFIG']):
            return

        payload = extract_payload(message)
        config_data = payload.get('config_data', {})

        device_id = config_data.get('device_id')
        formula_id = config_data.get('formula_id')
        prediction_interval = config_data.get('prediction_interval')

        if prediction_interval and isinstance(prediction_interval, int) and prediction_interval > 0:
            self._prediction_interval = prediction_interval
            print(f"[{self.service_id}] 预测间隔已更新为 {prediction_interval}s")

        if device_id and formula_id:
            success = self.set_formula(device_id, formula_id)
            if success:
                print(f"[{self.service_id}] 设备{device_id}已切换到配方 {formula_id}")
            else:
                print(f"[{self.service_id}] 设备{device_id}切换配方失败: 配方{formula_id}不存在")

        self._increment_metric("config_updates")

    async def _prediction_loop(self):
        while self._running:
            loop_start = asyncio.get_event_loop().time()

            try:
                for device_id in range(1, self._num_devices + 1):
                    await self._run_prediction(device_id)

            except Exception as e:
                print(f"[{self.service_id}] 预测循环异常: {e}")
                self._increment_metric("errors")

            loop_duration = asyncio.get_event_loop().time() - loop_start
            sleep_time = max(0, self._prediction_interval - loop_duration)
            await asyncio.sleep(sleep_time)

    async def _run_prediction(self, device_id: int):
        if not self._prediction_service:
            return

        try:
            window = self._get_device_window(device_id)
            if len(window) < 10:
                return

            batch_id = self._batch_ids.get(device_id)
            formula_id = self._current_formulas.get(device_id)

            prediction = self._prediction_service.predict(device_id, batch_id)

            result_msg = PredictionResult(
                device_id=device_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                moisture_content=prediction.moisture_content,
                moisture_confidence=prediction.moisture_confidence,
                reconstitution_time=prediction.reconstitution_time,
                reconstitution_confidence=prediction.reconstitution_confidence,
                drying_rate=prediction.drying_rate,
                is_qualified=prediction.is_qualified,
                moisture_threshold=prediction.moisture_threshold,
                reconstitution_threshold=prediction.reconstitution_threshold,
                formula_id=formula_id,
                batch_id=batch_id,
                drift_detected=prediction.drift_detected,
                adaptation_level=prediction.adaptation_level,
                model_version=prediction.model_version
            )

            message = MessageFactory.create_prediction(result_msg, self.service_id)

            await self.publish(CHANNELS['PREDICTION_RESULT'], message)
            self._increment_metric("messages_published")

            specific_channel = f"{CHANNELS['PREDICTION_RESULT']}:{device_id}"
            await self.publish(specific_channel, message)

            self._last_predictions[device_id] = {
                "moisture_content": prediction.moisture_content,
                "reconstitution_time": prediction.reconstitution_time,
                "is_qualified": prediction.is_qualified,
                "confidence": prediction.moisture_confidence,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            print(f"[{self.service_id}] 设备{device_id}预测失败: {e}")
            self._increment_metric("errors")

    def _get_device_window(self, device_id: int) -> TelemetryWindow:
        return self._device_cold_trap.get(device_id, TelemetryWindow())

    def set_formula(self, device_id: int, formula_id: str) -> bool:
        if not self._prediction_service:
            return False

        success = self._prediction_service.set_formula(device_id, formula_id)
        if success:
            self._current_formulas[device_id] = formula_id
        return success

    def get_prediction_service(self) -> Optional[QualityPredictionService]:
        return self._prediction_service

    def get_last_prediction(self, device_id: int) -> Optional[Dict]:
        return self._last_predictions.get(device_id)


async def main():
    redis_config = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0')),
        password=os.environ.get('REDIS_PASSWORD', None)
    )

    service = QualityPredictorService(redis_config)

    print("=" * 60)
    print("质量预测微服务启动")
    print(f"服务ID: {service.service_id}")
    print(f"服务类型: {service.service_type}")
    print(f"Redis: {redis_config.host}:{redis_config.port}")
    print(f"设备数: {service._num_devices}")
    print(f"预测间隔: {service._prediction_interval}s")
    print(f"历史窗口: {WINDOW_SECONDS}s ({WINDOW_SECONDS / 3600}小时)")
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
