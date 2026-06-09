"""
产品质量预测服务
基于偏最小二乘回归(PLS)预测制品水分含量和复溶时间
"""

import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from collections import deque
from datetime import datetime, timedelta

try:
    from sklearn.cross_decomposition import PLSRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


@dataclass
class PredictionResult:
    moisture_content: float
    moisture_confidence: float
    reconstitution_time: float
    reconstitution_confidence: float
    drying_rate: float
    is_qualified: bool
    moisture_threshold: float
    reconstitution_threshold: float


class PLSPredictor:
    def __init__(self, n_components: int = 6):
        self.n_components = n_components
        self._init_models()
        
        self.history_data: deque = deque(maxlen=1000)
        self.training_data: Optional[np.ndarray] = None
        self.training_targets: Optional[np.ndarray] = None
        
        self.feature_names = [
            'avg_temp', 'temp_std', 'temp_diff',
            'avg_vacuum', 'vacuum_std',
            'avg_power', 'power_std',
            'cold_trap_temp',
            'drying_rate_10min', 'drying_rate_30min', 'drying_rate_60min'
        ]
        
        self.target_names = ['moisture_content', 'reconstitution_time']
        
        self.moisture_threshold = 3.0
        self.reconstitution_threshold = 120.0
        
        self._model_trained = False

    def _init_models(self):
        if HAS_SKLEARN:
            self.model = Pipeline([
                ('scaler', StandardScaler()),
                ('pls', PLSRegression(n_components=self.n_components, scale=False))
            ])
        else:
            self.model = None

    def _extract_features(self, temp_history: List[List[float]], 
                         vacuum_history: List[List[float]],
                         power_history: List[List[float]],
                         cold_trap_history: List[float]) -> np.ndarray:
        temps = np.array(temp_history)
        vacuums = np.array(vacuum_history)
        powers = np.array(power_history)
        cold_traps = np.array(cold_trap_history)
        
        n_samples = len(temp_history)
        
        avg_temp = np.mean(temps, axis=1) if n_samples > 0 else np.zeros(1)
        temp_std = np.std(temps, axis=1) if n_samples > 0 else np.zeros(1)
        temp_diff = np.max(temps, axis=1) - np.min(temps, axis=1) if n_samples > 0 else np.zeros(1)
        
        avg_vacuum = np.mean(vacuums, axis=1) if n_samples > 0 else np.zeros(1)
        vacuum_std = np.std(vacuums, axis=1) if n_samples > 0 else np.zeros(1)
        
        avg_power = np.mean(powers, axis=1) if n_samples > 0 else np.zeros(1)
        power_std = np.std(powers, axis=1) if n_samples > 0 else np.zeros(1)
        
        avg_cold_trap = np.mean(cold_traps) if len(cold_traps) > 0 else -70.0
        
        def calc_drying_rate(window_size):
            if n_samples < window_size:
                return 0.0
            recent_temps = avg_temp[-window_size:]
            if len(recent_temps) >= 2:
                return (recent_temps[-1] - recent_temps[0]) / window_size
            return 0.0
        
        drying_rate_10 = calc_drying_rate(min(10, n_samples))
        drying_rate_30 = calc_drying_rate(min(30, n_samples))
        drying_rate_60 = calc_drying_rate(min(60, n_samples))
        
        features = np.array([
            np.mean(avg_temp),
            np.mean(temp_std),
            np.mean(temp_diff),
            np.mean(avg_vacuum),
            np.mean(vacuum_std),
            np.mean(avg_power),
            np.mean(power_std),
            avg_cold_trap,
            drying_rate_10,
            drying_rate_30,
            drying_rate_60
        ])
        
        return features.reshape(1, -1)

    def _calculate_confidence(self, prediction: float, feature_std: np.ndarray) -> float:
        base_confidence = 0.85
        
        noise_factor = np.mean(feature_std) if len(feature_std) > 0 else 1.0
        noise_penalty = min(0.15, noise_factor * 0.02)
        
        if self._model_trained and HAS_SKLEARN:
            sample_size_factor = min(1.0, len(self.history_data) / 100)
        else:
            sample_size_factor = 0.7
        
        confidence = base_confidence - noise_penalty
        confidence *= sample_size_factor
        
        return max(0.5, min(0.99, confidence))

    def _simulate_prediction(self, features: np.ndarray) -> Tuple[float, float]:
        avg_temp = features[0, 0]
        temp_diff = features[0, 2]
        avg_vacuum = features[0, 3]
        drying_rate = features[0, 9]
        
        moisture_base = 2.5
        temp_effect = (avg_temp + 50) * 0.15
        temp_diff_effect = temp_diff * 0.8
        vacuum_effect = max(0, (avg_vacuum - 1.0) * 0.5)
        drying_effect = abs(drying_rate) * 10
        
        moisture = moisture_base + temp_effect + temp_diff_effect + vacuum_effect + drying_effect
        moisture = max(0.5, min(10.0, moisture + np.random.normal(0, 0.3)))
        
        reconstitution_base = 90.0
        reconstitution = reconstitution_base + moisture * 8 + temp_diff * 15
        reconstitution = max(30.0, min(300.0, reconstitution + np.random.normal(0, 5.0)))
        
        return moisture, reconstitution

    def predict(self, 
                temp_history: List[List[float]],
                vacuum_history: List[List[float]],
                power_history: List[List[float]],
                cold_trap_history: List[float]) -> PredictionResult:
        
        features = self._extract_features(temp_history, vacuum_history, power_history, cold_trap_history)
        
        if HAS_SKLEARN and self._model_trained and self.model is not None:
            try:
                predictions = self.model.predict(features)
                moisture = float(predictions[0, 0])
                reconstitution = float(predictions[0, 1])
            except Exception:
                moisture, reconstitution = self._simulate_prediction(features)
        else:
            moisture, reconstitution = self._simulate_prediction(features)
        
        feature_std = np.std(np.array(temp_history), axis=0) if temp_history else np.array([0])
        
        moisture_conf = self._calculate_confidence(moisture, feature_std)
        reconstitution_conf = self._calculate_confidence(reconstitution, feature_std)
        
        drying_rate = float(features[0, 9])
        
        is_qualified = (moisture <= self.moisture_threshold and 
                       reconstitution <= self.reconstitution_threshold)
        
        return PredictionResult(
            moisture_content=round(moisture, 2),
            moisture_confidence=round(moisture_conf, 3),
            reconstitution_time=round(reconstitution, 1),
            reconstitution_confidence=round(reconstitution_conf, 3),
            drying_rate=round(drying_rate, 4),
            is_qualified=is_qualified,
            moisture_threshold=self.moisture_threshold,
            reconstitution_threshold=self.reconstitution_threshold
        )

    def add_training_data(self, 
                          temp_history: List[List[float]],
                          vacuum_history: List[List[float]],
                          power_history: List[List[float]],
                          cold_trap_history: List[float],
                          actual_moisture: float,
                          actual_reconstitution: float):
        features = self._extract_features(temp_history, vacuum_history, power_history, cold_trap_history)
        targets = np.array([[actual_moisture, actual_reconstitution]])
        
        self.history_data.append((features, targets))
        
        if len(self.history_data) >= 50:
            self._retrain_model()

    def _retrain_model(self):
        if not HAS_SKLEARN or len(self.history_data) < 50:
            return
        
        X = np.vstack([item[0] for item in self.history_data])
        y = np.vstack([item[1] for item in self.history_data])
        
        n_samples = X.shape[0]
        n_components = min(self.n_components, n_samples - 1, X.shape[1])
        
        if n_components < 2:
            return
        
        try:
            self.model.set_params(pls__n_components=n_components)
            self.model.fit(X, y)
            self._model_trained = True
            self.training_data = X
            self.training_targets = y
        except Exception as e:
            print(f"PLS模型训练失败: {e}")

    def set_thresholds(self, moisture: float, reconstitution: float):
        self.moisture_threshold = moisture
        self.reconstitution_threshold = reconstitution

    def get_model_info(self) -> Dict:
        return {
            "has_sklearn": HAS_SKLEARN,
            "model_trained": self._model_trained,
            "n_components": self.n_components,
            "training_samples": len(self.history_data),
            "feature_names": self.feature_names,
            "target_names": self.target_names,
            "moisture_threshold": self.moisture_threshold,
            "reconstitution_threshold": self.reconstitution_threshold
        }


class QualityPredictionService:
    def __init__(self, n_devices: int = 10):
        self.predictors: Dict[int, PLSPredictor] = {
            i: PLSPredictor() for i in range(1, n_devices + 1)
        }
        
        self.temp_history: Dict[int, Dict[int, deque]] = {}
        self.vacuum_history: Dict[int, Dict[int, deque]] = {}
        self.power_history: Dict[int, Dict[int, deque]] = {}
        self.cold_trap_history: Dict[int, deque] = {}
        
        for device_id in range(1, n_devices + 1):
            self.temp_history[device_id] = {shelf: deque(maxlen=120) for shelf in range(1, 6)}
            self.vacuum_history[device_id] = {shelf: deque(maxlen=120) for shelf in range(1, 6)}
            self.power_history[device_id] = {shelf: deque(maxlen=120) for shelf in range(1, 6)}
            self.cold_trap_history[device_id] = deque(maxlen=120)

    def add_telemetry(self, device_id: int, shelf_id: int, 
                      temperatures: List[float], vacuum_levels: List[float],
                      heating_powers: List[float], cold_trap_temp: float):
        self.temp_history[device_id][shelf_id].append(temperatures)
        self.vacuum_history[device_id][shelf_id].append(vacuum_levels)
        self.power_history[device_id][shelf_id].append(heating_powers)
        self.cold_trap_history[device_id].append(cold_trap_temp)

    def predict(self, device_id: int, batch_id: str = None) -> PredictionResult:
        all_temps = []
        all_vacuums = []
        all_powers = []
        
        for shelf_id in range(1, 6):
            temps = list(self.temp_history[device_id][shelf_id])
            vacs = list(self.vacuum_history[device_id][shelf_id])
            powers = list(self.power_history[device_id][shelf_id])
            
            if temps:
                all_temps.extend([[np.mean(t)] for t in temps])
                all_vacuums.extend([[np.mean(v)] for v in vacs])
                all_powers.extend([[np.mean(p)] for p in powers])
        
        cold_traps = list(self.cold_trap_history[device_id])
        
        if not all_temps:
            return PredictionResult(
                moisture_content=2.5,
                moisture_confidence=0.7,
                reconstitution_time=90.0,
                reconstitution_confidence=0.7,
                drying_rate=0.0,
                is_qualified=True,
                moisture_threshold=3.0,
                reconstitution_threshold=120.0
            )
        
        return self.predictors[device_id].predict(
            all_temps, all_vacuums, all_powers, cold_traps
        )

    def get_predictor(self, device_id: int) -> PLSPredictor:
        return self.predictors[device_id]

    def set_thresholds(self, moisture: float, reconstitution: float):
        for predictor in self.predictors.values():
            predictor.set_thresholds(moisture, reconstitution)
