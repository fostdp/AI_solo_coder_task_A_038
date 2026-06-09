"""
温度均匀性控制服务 - 批次优化版
实现模糊控制和迭代学习控制算法
动态调节各加热丝功率，使搁板间温差 < 1℃

优化特性：
- 批次管理与自动重置机制
- 指数遗忘因子，防止历史偏差累积
- 历史批次知识迁移，新批次快速收敛
- 批次切换检测与自动初始化
"""

import numpy as np
from typing import List, Tuple, Dict, Optional
from collections import deque
from dataclasses import dataclass, field
import time
from datetime import datetime


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
        
        print(f"[ILC] 批次重置: {batch_id}, 初始控制: {[round(c, 2) for c in initial_controls}}")
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
