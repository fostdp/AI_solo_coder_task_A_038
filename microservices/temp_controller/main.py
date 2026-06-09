"""
温度控制微服务
实现模糊控制+迭代学习控制算法，动态调节各加热丝功率
使搁板间温差 < 1℃
"""

import asyncio
import sys
import os
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from collections import deque
from dataclasses import dataclass, field
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import (
    MicroserviceBase, RedisConfig,
    CHANNELS, SERVICE_IDS, MESSAGE_TYPES,
    TelemetryData, ControlCommand, ControlStatus,
    MessageFactory, validate_message, extract_payload,
    config_loader, ControlConfig
)


@dataclass
class FuzzyRule:
    temp_error_range: Tuple[float, float]
    temp_change_rate_range: Tuple[float, float]
    output: float


@dataclass
class BatchInfo:
    batch_id: str
    start_time: float
    start_temperatures: List[float]
    initial_controls: List[float]
    steady_state_controls: Optional[List[float]] = None
    steady_state_error: Optional[float] = None


@dataclass
class ILCMetrics:
    current_batch: str
    batch_cycle: int
    forgetting_factor: float
    avg_error: float
    control_magnitude: float
    historical_batches: int


class FuzzyController:
    def __init__(self, target_temp: float = -50.0):
        self.target_temp = target_temp
        self.prev_temperatures = deque(maxlen=10)
        self.prev_errors = deque(maxlen=10)
        
        self.rules = self._init_rules()
        
        self.error_scale = 1.0
        self.change_rate_scale = 1.0
        self.output_scale = 5.0
        
        self._current_batch: Optional[str] = None
        self._batch_start_time: float = 0.0

    def _init_rules(self) -> List[FuzzyRule]:
        rules = []
        
        error_levels = [(-float('inf'), -1.0), (-1.0, -0.5), (-0.5, -0.2), 
                       (-0.2, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, float('inf'))]
        
        change_rate_levels = [(-float('inf'), -0.5), (-0.5, -0.1), (-0.1, 0.1),
                             (0.1, 0.5), (0.5, float('inf'))]
        
        outputs = [
            [-20, -15, -10, -5, 0],
            [-15, -10, -5, 0, 5],
            [-10, -5, 0, 0, 5],
            [-5, 0, 0, 0, 5],
            [-5, 0, 0, 5, 10],
            [0, 0, 5, 10, 15],
            [0, 5, 10, 15, 20],
        ]
        
        for i, err_range in enumerate(error_levels):
            for j, cr_range in enumerate(change_rate_levels):
                rules.append(FuzzyRule(err_range, cr_range, outputs[i][j]))
        
        return rules

    def _fuzzify(self, value: float, ranges: List[Tuple[float, float]]) -> np.ndarray:
        membership = np.zeros(len(ranges))
        for i, (low, high) in enumerate(ranges):
            if low < value <= high:
                center = (low + high) / 2 if low != -float('inf') and high != float('inf') else value
                width = high - low if low != -float('inf') and high != float('inf') else 1.0
                membership[i] = max(0, 1 - abs(value - center) / (width / 2))
        return membership if np.sum(membership) > 0 else np.ones(len(ranges)) / len(ranges)

    def calculate(self, current_temp: float, avg_temp: float = None) -> float:
        if avg_temp is None:
            avg_temp = current_temp
            
        error = self.target_temp - current_temp
        relative_error = avg_temp - current_temp
        
        self.prev_errors.append(relative_error)
        
        if len(self.prev_errors) >= 2:
            change_rate = self.prev_errors[-1] - self.prev_errors[-2]
        else:
            change_rate = 0
        
        error_scaled = relative_error * self.error_scale
        change_rate_scaled = change_rate * self.change_rate_scale
        
        error_levels = [(-float('inf'), -1.0), (-1.0, -0.5), (-0.5, -0.2), 
                       (-0.2, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, float('inf'))]
        change_rate_levels = [(-float('inf'), -0.5), (-0.5, -0.1), (-0.1, 0.1),
                             (0.1, 0.5), (0.5, float('inf'))]
        
        error_membership = self._fuzzify(error_scaled, error_levels)
        change_rate_membership = self._fuzzify(change_rate_scaled, change_rate_levels)
        
        output = 0.0
        total_weight = 0.0
        rule_idx = 0
        
        for i in range(len(error_levels)):
            for j in range(len(change_rate_levels)):
                weight = error_membership[i] * change_rate_membership[j]
                if weight > 0:
                    output += weight * self.rules[rule_idx].output
                    total_weight += weight
                rule_idx += 1
        
        if total_weight > 0:
            output = output / total_weight
        
        return output * self.output_scale


class IterativeLearningController:
    def __init__(self, n_heaters: int = 8, learning_rate: float = 0.1, 
                 forgetting_factor: float = 0.95, max_history: int = 100):
        self.n_heaters = n_heaters
        self.learning_rate = learning_rate
        self.forgetting_factor = forgetting_factor
        self.max_history = max_history
        
        self.history_errors: Dict[int, deque] = {i: deque(maxlen=max_history) for i in range(n_heaters)}
        self.history_controls: Dict[int, deque] = {i: deque(maxlen=max_history) for i in range(n_heaters)}
        self.history_weights: Dict[int, deque] = {i: deque(maxlen=max_history) for i in range(n_heaters)}
        
        self._current_batch: Optional[str] = None
        self._batch_cycle: int = 0
        self._batch_start_time: float = 0.0
        self._batch_initial_control: List[float] = [0.0] * n_heaters
        
        self._historical_batches: deque = deque(maxlen=20)
        self._knowledge_base: Dict[str, BatchInfo] = {}
        
        self._auto_detect_batch = True
        self._batch_change_threshold = 5.0
        self._last_temp_avg: float = 0.0

    def reset_batch(self, batch_id: Optional[str] = None, 
                    initial_temperatures: Optional[List[float]] = None,
                    use_historical_knowledge: bool = True) -> List[float]:
        if batch_id is None:
            batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        self._current_batch = batch_id
        self._batch_cycle = 0
        self._batch_start_time = time.time()
        
        for i in range(self.n_heaters):
            self.history_errors[i].clear()
            self.history_controls[i].clear()
            self.history_weights[i].clear()
        
        initial_controls = [0.0] * self.n_heaters
        
        if use_historical_knowledge and self._historical_batches:
            initial_controls = self._get_historical_initial_control(initial_temperatures)
        
        self._batch_initial_control = initial_controls.copy()
        
        if initial_temperatures is not None:
            batch_info = BatchInfo(
                batch_id=batch_id,
                start_time=self._batch_start_time,
                start_temperatures=initial_temperatures.copy(),
                initial_controls=initial_controls.copy()
            )
            self._knowledge_base[batch_id] = batch_info
        
        print(f"[ILC] 批次重置: {batch_id}, 初始控制: {[round(c, 2) for c in initial_controls]}")
        return initial_controls

    def _get_historical_initial_control(self, 
                                        current_temps: Optional[List[float]]) -> List[float]:
        if not self._historical_batches or current_temps is None:
            return [0.0] * self.n_heaters
        
        best_batch: Optional[BatchInfo] = None
        best_similarity = -1.0
        
        current_temp_array = np.array(current_temps)
        
        for batch_id in reversed(self._historical_batches):
            batch = self._knowledge_base.get(batch_id)
            if batch is None or batch.steady_state_controls is None:
                continue
            
            start_temp_array = np.array(batch.start_temperatures)
            similarity = -np.linalg.norm(current_temp_array - start_temp_array)
            
            if similarity > best_similarity:
                best_batch = batch
                best_similarity = similarity
        
        if best_batch and best_batch.steady_state_controls is not None:
            print(f"[ILC] 使用历史批次知识: {best_batch.batch_id}, 相似度: {-best_similarity:.3f}")
            return [c * 0.8 for c in best_batch.steady_state_controls]
        
        return [0.0] * self.n_heaters

    def detect_batch_change(self, current_temps: List[float]) -> bool:
        if not self._auto_detect_batch:
            return False
        
        current_avg = np.mean(current_temps)
        
        if self._last_temp_avg == 0.0:
            self._last_temp_avg = current_avg
            return False
        
        temp_change = abs(current_avg - self._last_temp_avg)
        self._last_temp_avg = current_avg
        
        if temp_change > self._batch_change_threshold:
            print(f"[ILC] 检测到批次切换, 温度变化: {temp_change:.2f}℃")
            return True
        
        if self._current_batch and (time.time() - self._batch_start_time > 4 * 3600):
            print(f"[ILC] 批次超时(>4小时), 自动重置")
            return True
        
        return False

    def update(self, heater_idx: int, error: float, prev_control: float) -> float:
        if self._current_batch is None:
            self.reset_batch()
        
        self._batch_cycle += 1
        
        weight = self.forgetting_factor ** len(self.history_errors[heater_idx])
        self.history_errors[heater_idx].append(error)
        self.history_controls[heater_idx].append(prev_control)
        self.history_weights[heater_idx].append(weight)
        
        if len(self.history_errors[heater_idx]) < 2:
            return prev_control
        
        errors = np.array(self.history_errors[heater_idx])
        controls = np.array(self.history_controls[heater_idx])
        weights = np.array(self.history_weights[heater_idx])
        
        normalized_weights = weights / np.sum(weights)
        
        weighted_error = np.sum(errors * normalized_weights)
        
        learning_term = self.learning_rate * error
        
        if len(errors) >= 5:
            recent_errors = errors[-5:]
            recent_weights = normalized_weights[-5:] / np.sum(normalized_weights[-5:])
            error_derivative = np.sum(np.diff(recent_errors) * recent_weights[-1])
            learning_term += 0.05 * error_derivative
        
        if len(errors) >= 10:
            recent_errors = errors[-10:]
            recent_weights = normalized_weights[-10:] / np.sum(normalized_weights[-10:])
            integral_term = 0.01 * np.sum(recent_errors * recent_weights)
            learning_term += integral_term
        
        new_control = controls[-1] + learning_term
        
        if self._batch_cycle < 10:
            warmup_factor = min(1.0, self._batch_cycle / 5.0)
            new_control = self._batch_initial_control[heater_idx] + (new_control - self._batch_initial_control[heater_idx]) * warmup_factor
        
        return max(-20, min(20, new_control))

    def finalize_batch(self, steady_state_error: Optional[float]):
        if self._current_batch is None:
            return
        
        if self._current_batch in self._knowledge_base:
            batch_info = self._knowledge_base[self._current_batch]
            batch_info.steady_state_controls = [
            np.mean(self.history_controls[i]) if len(self.history_controls[i]) > 10 else 0.0
            for i in range(self.n_heaters)
        ]
            batch_info.steady_state_error = steady_state_error
            self._historical_batches.append(self._current_batch)
            print(f"[ILC] 批次完成: {self._current_batch}, 稳态误差: {steady_state_error:.3f}℃")

    def get_metrics(self) -> ILCMetrics:
        avg_error = 0.0
        avg_control = 0.0
        count = 0
        
        for i in range(self.n_heaters):
            if self.history_errors[i]:
                avg_error += np.mean(np.abs(self.history_errors[i]))
                avg_control += np.mean(np.abs(self.history_controls[i]))
                count += 1
        
        if count > 0:
            avg_error /= count
            avg_control /= count
        
        return ILCMetrics(
            current_batch=self._current_batch or "N/A",
            batch_cycle=self._batch_cycle,
            forgetting_factor=self.forgetting_factor,
            avg_error=avg_error,
            control_magnitude=avg_control,
            historical_batches=len(self._historical_batches)
        )

    def set_forgetting_factor(self, factor: float):
        self.forgetting_factor = max(0.8, min(0.99, factor))


class TemperatureUniformityController:
    def __init__(self, n_shelves: int = 5, n_heaters_per_shelf: int = 8):
        self.n_shelves = n_shelves
        self.n_heaters_per_shelf = n_heaters_per_shelf
        
        self.fuzzy_controllers: Dict[Tuple[int, int], FuzzyController] = {}
        self.ilc_controllers: Dict[Tuple[int, int], IterativeLearningController] = {}
        
        for shelf in range(1, n_shelves + 1):
            for heater in range(n_heaters_per_shelf):
                key = (shelf, heater)
                self.fuzzy_controllers[key] = FuzzyController()
                self.ilc_controllers[key] = IterativeLearningController()
        
        self.auto_mode = True
        self.temp_diff_threshold = 1.0
        self.weights = {"fuzzy": 0.6, "ilc": 0.4}
        
        self._device_id: Optional[int] = None
        self._current_batch_id: Optional[str] = None
        self._batch_temps_history: deque = deque(maxlen=20)

    def set_target_temp(self, shelf_id: int, target: float):
        for heater in range(self.n_heaters_per_shelf):
            self.fuzzy_controllers[(shelf_id, heater)].target_temp = target

    def reset_batch(self, batch_id: Optional[str] = None, 
                    initial_temperatures: Optional[List[float]] = None) -> None:
        if batch_id is None:
            batch_id = f"BATCH-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        self._current_batch_id = batch_id
        self._batch_temps_history.clear()
        
        for shelf in range(1, self.n_shelves + 1):
            for heater in range(self.n_heaters_per_shelf):
                key = (shelf, heater)
                self.ilc_controllers[key].reset_batch(
                    batch_id=batch_id,
                    initial_temperatures=initial_temperatures
                )
                self.fuzzy_controllers[key]._current_batch = batch_id
                self.fuzzy_controllers[key]._batch_start_time = time.time()
        
        print(f"[Controller] 批次重置完成: {batch_id}")

    def _check_batch_switch(self, temperatures: List[float]) -> bool:
        if not self.ilc_controllers:
            return False
        
        sample_ilc = next(iter(self.ilc_controllers.values()))
        return sample_ilc.detect_batch_change(temperatures)

    def calculate_power_adjustments(
        self,
        shelf_id: int,
        temperatures: List[float],
        prev_powers: List[float],
        batch_id: Optional[str] = None
    ) -> List[float]:
        if not self.auto_mode:
            return [0.0] * self.n_heaters_per_shelf
        
        if batch_id is not None and batch_id != self._current_batch_id:
            self.reset_batch(batch_id, temperatures)
        
        if self._check_batch_switch(temperatures):
            self.reset_batch(initial_temperatures=temperatures)
        
        self._batch_temps_history.append(temperatures.copy())
        
        avg_temp = np.mean(temperatures)
        temp_diff = max(temperatures) - min(temperatures)
        
        adjustments = []
        
        for i in range(self.n_heaters_per_shelf):
            key = (shelf_id, i)
            
            fuzzy_output = self.fuzzy_controllers[key].calculate(temperatures[i], avg_temp)
            
            error = avg_temp - temperatures[i]
            ilc_output = self.ilc_controllers[key].update(i, error, prev_powers[i] - 50)
            
            combined = (self.weights["fuzzy"] * fuzzy_output + 
                       self.weights["ilc"] * ilc_output)
            
            if temp_diff < self.temp_diff_threshold * 0.5:
                combined *= 0.3
            elif temp_diff < self.temp_diff_threshold * 0.8:
                combined *= 0.7
            
            adjustments.append(max(-20, min(20, combined)))
        
        if temp_diff < 0.5 and len(self._batch_temps_history) >= 10:
            self._finalize_batch_if_steady(temp_diff)
        
        return adjustments

    def _finalize_batch_if_steady(self, current_error: float):
        if not self._current_batch_id:
            return
        
        recent_errors = []
        for temps in list(self._batch_temps_history)[-10:]:
            recent_errors.append(max(temps) - min(temps))
        
        avg_recent_error = np.mean(recent_errors)
        
        if avg_recent_error < 0.5:
            for shelf in range(1, self.n_shelves + 1):
                for heater in range(self.n_heaters_per_shelf):
                    key = (shelf, heater)
                    self.ilc_controllers[key].finalize_batch(avg_recent_error)
            
            print(f"[Controller] 批次达到稳态，平均误差: {avg_recent_error:.3f}℃")

    def get_control_metrics(self) -> Dict:
        metrics = {}
        if self.ilc_controllers:
            sample_ilc = next(iter(self.ilc_controllers.values()))
            ilc_metrics = sample_ilc.get_metrics()
            metrics["ilc"] = {
                "current_batch": ilc_metrics.current_batch,
                "batch_cycle": ilc_metrics.batch_cycle,
                "forgetting_factor": ilc_metrics.forgetting_factor,
                "avg_error": round(ilc_metrics.avg_error, 4),
                "historical_batches": ilc_metrics.historical_batches
            }
        return metrics

    def get_temperature_uniformity(self, temperatures: List[float]) -> Dict:
        temps = np.array(temperatures)
        return {
            "max_temp": float(np.max(temps)),
            "min_temp": float(np.min(temps)),
            "avg_temp": float(np.mean(temps)),
            "std_temp": float(np.std(temps)),
            "temp_diff": float(np.max(temps) - np.min(temps)),
            "uniformity_ok": float(np.max(temps) - np.min(temps)) < self.temp_diff_threshold
        }

    def set_auto_mode(self, enabled: bool):
        self.auto_mode = enabled

    def set_threshold(self, threshold: float):
        self.temp_diff_threshold = threshold


class TemperatureControllerService(MicroserviceBase):
    """温度控制微服务"""

    def __init__(self, redis_config: RedisConfig = None):
        super().__init__(
            service_id=SERVICE_IDS['TEMP_CONTROLLER'],
            service_type="temperature_control",
            redis_config=redis_config
        )

        self._controllers: Dict[int, TemperatureUniformityController] = {}
        self._auto_mode: Dict[int, bool] = {}
        self._latest_telemetry: Dict[Tuple[int, int], Dict] = {}
        self._target_temps: Dict[Tuple[int, int], float] = {}
        self._control_interval: float = 10.0
        self._num_devices: int = 10
        self._num_shelves: int = 5
        self._num_heaters_per_shelf: int = 8

        self._control_cycle_stats = {
            "control_cycles": 0,
            "commands_published": 0,
            "telemetry_received": 0,
            "config_updates": 0
        }

        self._load_config()
        self._init_controllers()

    def _init_controllers(self):
        """初始化所有设备的控制器"""
        for device_id in range(1, self._num_devices + 1):
            controller = TemperatureUniformityController(
                n_shelves=self._num_shelves,
                n_heaters_per_shelf=self._num_heaters_per_shelf
            )
            self._controllers[device_id] = controller
            self._auto_mode[device_id] = True

            for shelf_id in range(1, self._num_shelves + 1):
                self._target_temps[(device_id, shelf_id)] = self._default_target_temp

        print(f"[{self.service_id}] 初始化{self._num_devices}个设备控制器完成")

    def _load_config(self):
        """从YAML配置文件加载控制参数"""
        try:
            control_config: ControlConfig = config_loader.load_control_config()

            fuzzy_params = control_config.fuzzy_control
            ilc_params = control_config.ilc_control
            power_params = control_config.power_allocation
            temp_limits = control_config.temperature_limits

            self._default_target_temp = float(temp_limits.get('target', -50.0))
            self._min_temp = float(temp_limits.get('min_allowed', -60.0))
            self._max_temp = float(temp_limits.get('max_allowed', -40.0))
            self._uniformity_threshold = float(temp_limits.get('uniformity_threshold', 1.0))

            self._fuzzy_output_scale = float(fuzzy_params.get('output_scale', 5.0))
            self._fuzzy_error_scale = float(fuzzy_params.get('error_scale', 1.0))
            self._fuzzy_change_rate_scale = float(fuzzy_params.get('change_rate_scale', 1.0))

            self._ilc_learning_rate = float(ilc_params.get('learning_rate', 0.1))
            self._ilc_forgetting_factor = float(ilc_params.get('forgetting_factor', 0.95))
            self._ilc_max_history = int(ilc_params.get('max_history', 100))

            self._fuzzy_weight = float(power_params.get('fuzzy_weight', 0.6))
            self._ilc_weight = float(power_params.get('ilc_weight', 0.4))

            gain_scheduling = power_params.get('gain_scheduling', {})
            self._low_diff = float(gain_scheduling.get('low_diff', 0.5))
            self._low_diff_gain = float(gain_scheduling.get('low_diff_gain', 0.3))
            self._medium_diff = float(gain_scheduling.get('medium_diff', 0.8))
            self._medium_diff_gain = float(gain_scheduling.get('medium_diff_gain', 0.7))
            self._high_diff_gain = float(gain_scheduling.get('high_diff_gain', 1.0))

            self._min_adjustment = float(power_params.get('min_adjustment', -20.0))
            self._max_adjustment = float(power_params.get('max_adjustment', 20.0))
            self._min_power = float(power_params.get('min_power', 0.0))
            self._max_power = float(power_params.get('max_power', 100.0))

            self._apply_config_to_controllers()

            print(f"[{self.service_id}] 控制参数加载完成")
            print(f"  目标温度: {self._default_target_temp}℃")
            print(f"  均匀性阈值: {self._uniformity_threshold}℃")
            print(f"  模糊权重: {self._fuzzy_weight}, ILC权重: {self._ilc_weight}")

        except Exception as e:
            print(f"[{self.service_id}] 配置加载失败: {e}")
            self._load_default_config()

    def _load_default_config(self):
        """加载默认配置"""
        self._default_target_temp = -50.0
        self._min_temp = -60.0
        self._max_temp = -40.0
        self._uniformity_threshold = 1.0
        self._fuzzy_output_scale = 5.0
        self._fuzzy_error_scale = 1.0
        self._fuzzy_change_rate_scale = 1.0
        self._ilc_learning_rate = 0.1
        self._ilc_forgetting_factor = 0.95
        self._ilc_max_history = 100
        self._fuzzy_weight = 0.6
        self._ilc_weight = 0.4
        self._low_diff = 0.5
        self._low_diff_gain = 0.3
        self._medium_diff = 0.8
        self._medium_diff_gain = 0.7
        self._high_diff_gain = 1.0
        self._min_adjustment = -20.0
        self._max_adjustment = 20.0
        self._min_power = 0.0
        self._max_power = 100.0

    def _apply_config_to_controllers(self):
        """将配置应用到所有控制器"""
        for device_id, controller in self._controllers.items():
            controller.temp_diff_threshold = self._uniformity_threshold
            controller.weights = {"fuzzy": self._fuzzy_weight, "ilc": self._ilc_weight}

            for shelf_id in range(1, self._num_shelves + 1):
                controller.set_target_temp(shelf_id, self._default_target_temp)
                self._target_temps[(device_id, shelf_id)] = self._default_target_temp

                for heater in range(self._num_heaters_per_shelf):
                    key = (shelf_id, heater)
                    if key in controller.fuzzy_controllers:
                        fc = controller.fuzzy_controllers[key]
                        fc.output_scale = self._fuzzy_output_scale
                        fc.error_scale = self._fuzzy_error_scale
                        fc.change_rate_scale = self._fuzzy_change_rate_scale

                    if key in controller.ilc_controllers:
                        ilc = controller.ilc_controllers[key]
                        ilc.learning_rate = self._ilc_learning_rate
                        ilc.set_forgetting_factor(self._ilc_forgetting_factor)

    async def _subscribe_channels(self):
        """订阅需要的频道"""
        await self.subscribe(CHANNELS['TELEMETRY_RAW'], self._handle_telemetry)
        await self.subscribe(CHANNELS['CONFIG_UPDATE'], self._handle_config_update)
        await self.subscribe(CHANNELS['CONTROL_COMMAND'], self._handle_external_control)

    async def _on_start(self):
        """启动时执行"""
        print(f"[{self.service_id}] 启动温度控制循环...")

        control_loop_task = asyncio.create_task(self._control_loop())
        self._sub_tasks.append(control_loop_task)

        print(f"[{self.service_id}] 温度控制服务启动完成")
        print(f"  控制间隔: {self._control_interval}s")
        print(f"  设备数: {self._num_devices}")
        print(f"  搁板数: {self._num_shelves}")
        print(f"  每搁板加热丝数: {self._num_heaters_per_shelf}")

    async def _on_stop(self):
        """停止时执行"""
        print(f"[{self.service_id}] 停止温度控制服务...")

    async def _handle_telemetry(self, message: Dict):
        """处理遥测数据"""
        if not validate_message(message, MESSAGE_TYPES['TELEMETRY']):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')
        shelf_id = payload.get('shelf_id')

        if device_id is None or shelf_id is None:
            return

        self._latest_telemetry[(device_id, shelf_id)] = payload
        self._control_cycle_stats["telemetry_received"] += 1
        self._increment_metric("messages_received")

    async def _handle_config_update(self, message: Dict):
        """处理配置更新"""
        if not validate_message(message, MESSAGE_TYPES['CONFIG']):
            return

        payload = extract_payload(message)
        config_type = payload.get('config_type')

        if config_type == 'control':
            print(f"[{self.service_id}] 收到控制配置更新")
            config_loader.reload_all()
            self._load_config()
            self._control_cycle_stats["config_updates"] += 1
            self._increment_metric("config_updates")

    async def _handle_external_control(self, message: Dict):
        """处理外部控制命令（如手动模式切换）"""
        if not validate_message(message, MESSAGE_TYPES['CONTROL_COMMAND']):
            return

        payload = extract_payload(message)
        device_id = payload.get('device_id')
        auto_mode = payload.get('auto_mode')
        target_temp = payload.get('target_temp')

        if device_id is None:
            return

        if auto_mode is not None and device_id in self._auto_mode:
            self._auto_mode[device_id] = auto_mode
            if device_id in self._controllers:
                self._controllers[device_id].set_auto_mode(auto_mode)
            print(f"[{self.service_id}] 设备{device_id}自动模式设置为: {auto_mode}")

        if target_temp is not None:
            for shelf_id in range(1, self._num_shelves + 1):
                self._target_temps[(device_id, shelf_id)] = target_temp
                if device_id in self._controllers:
                    self._controllers[device_id].set_target_temp(shelf_id, target_temp)
            print(f"[{self.service_id}] 设备{device_id}目标温度设置为: {target_temp}℃")

    async def _control_loop(self):
        """控制计算循环 - 每10秒执行一次"""
        while self._running:
            cycle_start = asyncio.get_event_loop().time()

            try:
                for device_id in range(1, self._num_devices + 1):
                    for shelf_id in range(1, self._num_shelves + 1):
                        await self._process_shelf_control(device_id, shelf_id)

                self._control_cycle_stats["control_cycles"] += 1
                self._metrics.update(self._control_cycle_stats)

            except Exception as e:
                print(f"[{self.service_id}] 控制循环异常: {e}")
                self._increment_metric("errors")

            cycle_duration = asyncio.get_event_loop().time() - cycle_start
            sleep_time = max(0, self._control_interval - cycle_duration)
            await asyncio.sleep(sleep_time)

    async def _process_shelf_control(self, device_id: int, shelf_id: int):
        """处理单个搁板的控制计算"""
        telemetry_key = (device_id, shelf_id)
        telemetry_data = self._latest_telemetry.get(telemetry_key)

        if not telemetry_data:
            return

        temperatures = telemetry_data.get('temperatures', [])
        prev_powers = telemetry_data.get('heating_powers', [])
        batch_id = telemetry_data.get('batch_id')

        if len(temperatures) < self._num_heaters_per_shelf:
            temperatures = temperatures + [0.0] * (self._num_heaters_per_shelf - len(temperatures))
        if len(prev_powers) < self._num_heaters_per_shelf:
            prev_powers = prev_powers + [50.0] * (self._num_heaters_per_shelf - len(prev_powers))

        controller = self._controllers.get(device_id)
        if not controller:
            return

        auto_mode = self._auto_mode.get(device_id, True)
        controller.set_auto_mode(auto_mode)

        adjustments = controller.calculate_power_adjustments(
            shelf_id=shelf_id,
            temperatures=temperatures,
            prev_powers=prev_powers,
            batch_id=batch_id
        )

        target_temp = self._target_temps.get(telemetry_key, self._default_target_temp)

        control_cmd = ControlCommand(
            device_id=device_id,
            shelf_id=shelf_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            auto_mode=auto_mode,
            power_adjustments=adjustments,
            target_temp=target_temp,
            batch_id=batch_id,
            command_id=str(uuid4())
        )

        message = MessageFactory.create_control_command(control_cmd, self.service_id)
        success = await self.publish(CHANNELS['CONTROL_COMMAND'], message)

        if success:
            self._control_cycle_stats["commands_published"] += 1
            self._increment_metric("messages_published")

        await self._publish_control_status(
            device_id, shelf_id, temperatures, prev_powers,
            adjustments, auto_mode, batch_id
        )

        if self._control_cycle_stats["control_cycles"] % 6 == 0:
            uniformity = controller.get_temperature_uniformity(temperatures)
            print(f"[{self.service_id}] 设备{device_id}-搁板{shelf_id} "
                  f"温度均匀性: {uniformity['temp_diff']:.2f}℃ "
                  f"目标: {target_temp}℃ "
                  f"调整量: {[round(a, 1) for a in adjustments[:3]]}...")

    async def _publish_control_status(self, device_id: int, shelf_id: int,
                                       temperatures: List[float], prev_powers: List[float],
                                       adjustments: List[float], auto_mode: bool,
                                       batch_id: Optional[str]):
        """发布控制状态"""
        try:
            temps = np.array(temperatures)
            avg_temp = float(np.mean(temps))
            temp_diff = float(np.max(temps) - np.min(temps))

            status = ControlStatus(
                device_id=device_id,
                shelf_id=shelf_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                auto_mode=auto_mode,
                current_powers=prev_powers,
                temperature_diff=temp_diff,
                avg_temperature=avg_temp,
                adjustments=adjustments,
                batch_id=batch_id
            )

            message = {
                "header": {
                    "message_id": str(uuid4()),
                    "message_type": MESSAGE_TYPES['CONTROL_STATUS'],
                    "source_service": self.service_id,
                    "target_service": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "version": "1.0"
                },
                "payload": status.to_dict()
            }

            await self.publish(CHANNELS['CONTROL_STATUS'], message)

        except Exception as e:
            print(f"[{self.service_id}] 发布控制状态失败: {e}")


async def main():
    """主函数"""
    redis_config = RedisConfig(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', '6379')),
        db=int(os.environ.get('REDIS_DB', '0'))
    )

    service = TemperatureControllerService(redis_config)

    print("=" * 60)
    print("温度控制微服务启动")
    print(f"服务ID: {service.service_id}")
    print(f"服务类型: {service.service_type}")
    print(f"Redis: {redis_config.host}:{redis_config.port}")
    print(f"控制间隔: {service._control_interval}s")
    print(f"设备数: {service._num_devices}")
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
