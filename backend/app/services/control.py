"""
温度均匀性控制服务
实现模糊控制和迭代学习控制算法
动态调节各加热丝功率，使搁板间温差 < 1℃
"""

import numpy as np
from typing import List, Tuple, Dict
from collections import deque
from dataclasses import dataclass, field
import time


@dataclass
class FuzzyRule:
    temp_error_range: Tuple[float, float]
    temp_change_rate_range: Tuple[float, float]
    output: float


class FuzzyController:
    def __init__(self, target_temp: float = -50.0):
        self.target_temp = target_temp
        self.prev_temperatures = deque(maxlen=10)
        self.prev_errors = deque(maxlen=10)
        
        self.rules = self._init_rules()
        
        self.error_scale = 1.0
        self.change_rate_scale = 1.0
        self.output_scale = 5.0

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
    def __init__(self, n_heaters: int = 8, learning_rate: float = 0.1):
        self.n_heaters = n_heaters
        self.learning_rate = learning_rate
        self.history_errors: Dict[int, deque] = {i: deque(maxlen=50) for i in range(n_heaters)}
        self.history_controls: Dict[int, deque] = {i: deque(maxlen=50) for i in range(n_heaters)}

    def update(self, heater_idx: int, error: float, prev_control: float) -> float:
        self.history_errors[heater_idx].append(error)
        self.history_controls[heater_idx].append(prev_control)
        
        if len(self.history_errors[heater_idx]) < 2:
            return prev_control
        
        errors = np.array(self.history_errors[heater_idx])
        controls = np.array(self.history_controls[heater_idx])
        
        learning_term = self.learning_rate * error
        
        if len(errors) >= 5:
            error_derivative = np.mean(np.diff(errors[-5:]))
            learning_term += 0.05 * error_derivative
        
        new_control = controls[-1] + learning_term
        return max(-20, min(20, new_control))


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

    def set_target_temp(self, shelf_id: int, target: float):
        for heater in range(self.n_heaters_per_shelf):
            self.fuzzy_controllers[(shelf_id, heater)].target_temp = target

    def calculate_power_adjustments(
        self,
        shelf_id: int,
        temperatures: List[float],
        prev_powers: List[float]
    ) -> List[float]:
        if not self.auto_mode:
            return [0.0] * self.n_heaters_per_shelf
        
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
        
        return adjustments

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
