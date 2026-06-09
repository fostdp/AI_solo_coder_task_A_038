"""
产品质量预测服务 - 迁移学习优化版
基于偏最小二乘回归(PLS)预测制品水分含量和复溶时间

优化特性：
- 配方管理与配方特征跟踪
- 迁移学习：源域模型 + 配方差异校正
- 增量学习与自适应更新
- 概念漂移检测与模型自动调整
- 多模型集成，按配方相似度加权
"""

import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

try:
    from sklearn.cross_decomposition import PLSRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import mean_absolute_error, r2_score
    from sklearn.base import clone
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


@dataclass
class FormulaInfo:
    formula_id: str
    formula_name: str
    product_type: str
    target_moisture: float
    target_reconstitution: float
    freeze_curve: Dict[str, float]
    feature_centroid: np.ndarray
    sample_count: int = 0
    creation_time: float = field(default_factory=lambda: datetime.now().timestamp())


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
    formula_id: Optional[str] = None
    model_version: str = "v2.0-transfer"
    adaptation_level: float = 0.0
    drift_detected: bool = False


@dataclass
class ModelPerformance:
    mae_moisture: float
    mae_reconstitution: float
    r2_moisture: float
    r2_reconstitution: float
    sample_count: int
    last_update: float


@dataclass
class DriftDetectionResult:
    has_drift: bool
    drift_magnitude: float
    feature_shift: np.ndarray
    p_value: float


class AdaptivePLSPredictor:
    def __init__(self, n_components: int = 6):
        self.n_components = n_components
        
        self._source_model: Optional[Pipeline] = None
        self._target_model: Optional[Pipeline] = None
        self._ensemble_weights: Dict[str, float] = {}
        
        self._formula_library: Dict[str, FormulaInfo] = {}
        self._current_formula: Optional[FormulaInfo] = None
        
        self._history_data: deque = deque(maxlen=2000)
        self._recent_data: deque = deque(maxlen=200)
        self._labeled_data: deque = deque(maxlen=500)
        
        self._adaptation_rate: float = 0.1
        self._transfer_alpha: float = 0.7
        self._drift_threshold: float = 0.05
        
        self._performance_history: deque = deque(maxlen=50)
        self._last_performance: Optional[ModelPerformance] = None
        
        self._feature_names = [
            'avg_temp', 'temp_std', 'temp_diff',
            'avg_vacuum', 'vacuum_std',
            'avg_power', 'power_std',
            'cold_trap_temp',
            'drying_rate_10min', 'drying_rate_30min', 'drying_rate_60min'
        ]
        
        self._target_names = ['moisture_content', 'reconstitution_time']
        
        self.moisture_threshold = 3.0
        self.reconstitution_threshold = 120.0
        
        self._source_trained = False
        self._target_trained = False
        self._model_version = "v2.0-transfer"
        
        self._init_models()
    
    def _init_models(self):
        if HAS_SKLEARN:
            self._source_model = Pipeline([
                ('scaler', StandardScaler()),
                ('pls', PLSRegression(n_components=self.n_components, scale=False))
            ])
            self._target_model = Pipeline([
                ('scaler', StandardScaler()),
                ('pls', PLSRegression(n_components=self.n_components, scale=False))
            ])
        else:
            self._source_model = None
            self._target_model = None
    
    def register_formula(self, formula_id: str, formula_name: str,
                        product_type: str, target_moisture: float,
                        target_reconstitution: float,
                        freeze_curve: Dict[str, float]) -> FormulaInfo:
        formula = FormulaInfo(
            formula_id=formula_id,
            formula_name=formula_name,
            product_type=product_type,
            target_moisture=target_moisture,
            target_reconstitution=target_reconstitution,
            freeze_curve=freeze_curve,
            feature_centroid=np.zeros(len(self._feature_names))
        )
        self._formula_library[formula_id] = formula
        print(f"[PLS] 注册配方: {formula_id} ({formula_name})")
        return formula
    
    def set_current_formula(self, formula_id: str) -> bool:
        if formula_id in self._formula_library:
            self._current_formula = self._formula_library[formula_id]
            print(f"[PLS] 切换到配方: {formula_id}")
            return True
        return False
    
    def _calculate_formula_similarity(self, features: np.ndarray,
                                      formula: FormulaInfo) -> float:
        if formula.sample_count == 0:
            return 0.5
        
        centroid = formula.feature_centroid
        distance = np.linalg.norm(features - centroid)
        similarity = np.exp(-distance / (2 * len(self._feature_names)))
        return float(similarity)
    
    def _find_best_matching_formula(self, features: np.ndarray) -> Tuple[Optional[str], float]:
        best_formula = None
        best_similarity = 0.0
        
        for formula_id, formula in self._formula_library.items():
            if formula.sample_count < 10:
                continue
            
            similarity = self._calculate_formula_similarity(features, formula)
            if similarity > best_similarity:
                best_similarity = similarity
                best_formula = formula_id
        
        return best_formula, best_similarity
    
    def _detect_concept_drift(self, features: np.ndarray) -> DriftDetectionResult:
        if len(self._history_data) < 50:
            return DriftDetectionResult(False, 0.0, np.zeros(features.shape[1]), 1.0)
        
        historical_features = np.vstack([d[0] for d in list(self._history_data)[-100:]])
        current_features = features
        
        hist_mean = np.mean(historical_features, axis=0)
        curr_mean = np.mean(current_features, axis=0)
        feature_shift = curr_mean - hist_mean
        
        hist_std = np.std(historical_features, axis=0) + 1e-8
        normalized_shift = np.abs(feature_shift) / hist_std
        drift_magnitude = float(np.mean(normalized_shift))
        
        from scipy import stats
        p_values = []
        for i in range(features.shape[1]):
            _, p_val = stats.ks_2samp(historical_features[:, i], current_features[:, i])
            p_values.append(p_val)
        
        min_p_value = float(np.min(p_values))
        
        has_drift = drift_magnitude > self._drift_threshold or min_p_value < 0.01
        
        return DriftDetectionResult(
            has_drift=has_drift,
            drift_magnitude=drift_magnitude,
            feature_shift=feature_shift,
            p_value=min_p_value
        )
    
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
    
    def _transfer_learning_predict(self, features: np.ndarray) -> Tuple[np.ndarray, float]:
        if not HAS_SKLEARN:
            return np.array([[2.5, 90.0]]), 0.0
        
        if not self._source_trained or self._source_model is None:
            return np.array([[2.5, 90.0]]), 0.0
        
        source_pred = self._source_model.predict(features)
        
        best_formula, similarity = self._find_best_matching_formula(features)
        
        if self._target_trained and self._target_model is not None and similarity > 0.3:
            target_pred = self._target_model.predict(features)
            
            alpha = self._transfer_alpha * min(1.0, similarity * 2)
            combined_pred = alpha * target_pred + (1 - alpha) * source_pred
            
            adaptation_level = alpha
        else:
            combined_pred = source_pred
            adaptation_level = 0.0
        
        return combined_pred, adaptation_level
    
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
    
    def _calculate_confidence(self, prediction: np.ndarray, features: np.ndarray,
                             adaptation_level: float) -> Tuple[float, float]:
        base_confidence = 0.85
        
        feature_std = np.std(features, axis=0) if features.shape[0] > 1 else np.zeros(features.shape[1])
        noise_factor = np.mean(feature_std) if len(feature_std) > 0 else 1.0
        noise_penalty = min(0.15, noise_factor * 0.02)
        
        sample_size_factor = min(1.0, len(self._labeled_data) / 100)
        
        adaptation_bonus = adaptation_level * 0.1
        
        moisture_conf = base_confidence - noise_penalty
        moisture_conf = moisture_conf * sample_size_factor + adaptation_bonus
        moisture_conf = max(0.5, min(0.99, moisture_conf))
        
        reconstitution_conf = base_confidence - noise_penalty * 1.2
        reconstitution_conf = reconstitution_conf * sample_size_factor + adaptation_bonus
        reconstitution_conf = max(0.5, min(0.99, reconstitution_conf))
        
        return moisture_conf, reconstitution_conf
    
    def predict(self, 
                temp_history: List[List[float]],
                vacuum_history: List[List[float]],
                power_history: List[List[float]],
                cold_trap_history: List[float]) -> PredictionResult:
        
        features = self._extract_features(temp_history, vacuum_history, power_history, cold_trap_history)
        
        drift_result = self._detect_concept_drift(features)
        
        if drift_result.has_drift:
            print(f"[PLS] 检测到概念漂移, 幅度: {drift_result.drift_magnitude:.4f}")
            self._adaptation_rate = min(0.3, self._adaptation_rate + 0.02)
        
        self._history_data.append((features, None))
        self._recent_data.append((features, None))
        
        formula_id = self._current_formula.formula_id if self._current_formula else None
        
        if HAS_SKLEARN and (self._source_trained or self._target_trained):
            try:
                predictions, adaptation_level = self._transfer_learning_predict(features)
                moisture = float(predictions[0, 0])
                reconstitution = float(predictions[0, 1])
            except Exception as e:
                print(f"[PLS] 模型预测失败, 使用模拟: {e}")
                moisture, reconstitution = self._simulate_prediction(features)
                adaptation_level = 0.0
        else:
            moisture, reconstitution = self._simulate_prediction(features)
            adaptation_level = 0.0
        
        moisture_conf, reconstitution_conf = self._calculate_confidence(
            predictions if 'predictions' in locals() else np.array([[moisture, reconstitution]]),
            features,
            adaptation_level
        )
        
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
            reconstitution_threshold=self.reconstitution_threshold,
            formula_id=formula_id,
            model_version=self._model_version,
            adaptation_level=round(adaptation_level, 3),
            drift_detected=drift_result.has_drift
        )
    
    def add_labeled_data(self, 
                         temp_history: List[List[float]],
                         vacuum_history: List[List[float]],
                         power_history: List[List[float]],
                         cold_trap_history: List[float],
                         actual_moisture: float,
                         actual_reconstitution: float,
                         formula_id: Optional[str] = None):
        features = self._extract_features(temp_history, vacuum_history, power_history, cold_trap_history)
        targets = np.array([[actual_moisture, actual_reconstitution]])
        
        self._labeled_data.append((features, targets, formula_id))
        
        if formula_id and formula_id in self._formula_library:
            formula = self._formula_library[formula_id]
            formula.sample_count += 1
            formula.feature_centroid = (
                formula.feature_centroid * (formula.sample_count - 1) + features.flatten()
            ) / formula.sample_count
        
        if len(self._labeled_data) >= 30:
            self._adaptive_update()
    
    def _adaptive_update(self):
        if not HAS_SKLEARN or len(self._labeled_data) < 30:
            return
        
        try:
            recent_data = list(self._labeled_data)[-100:]
            
            X = np.vstack([d[0] for d in recent_data])
            y = np.vstack([d[1] for d in recent_data])
            
            n_samples = X.shape[0]
            n_components = min(self.n_components, n_samples - 1, X.shape[1])
            
            if n_components < 2:
                return
            
            if not self._source_trained:
                self._source_model.set_params(pls__n_components=n_components)
                self._source_model.fit(X, y)
                self._source_trained = True
                print(f"[PLS] 源模型训练完成, 样本数: {n_samples}")
            else:
                old_coefs = self._source_model.named_steps['pls'].coef_
                
                self._target_model.set_params(pls__n_components=n_components)
                self._target_model.fit(X, y)
                self._target_trained = True
                
                new_coefs = self._target_model.named_steps['pls'].coef_
                blended_coefs = (1 - self._adaptation_rate) * old_coefs + self._adaptation_rate * new_coefs
                self._source_model.named_steps['pls'].coef_ = blended_coefs
                
                self._adaptation_rate = max(0.05, self._adaptation_rate * 0.95)
                
                print(f"[PLS] 模型自适应更新完成, 适应率: {self._adaptation_rate:.3f}")
            
            self._update_performance_metrics(X, y)
            
        except Exception as e:
            print(f"[PLS] 自适应更新失败: {e}")
    
    def _update_performance_metrics(self, X: np.ndarray, y: np.ndarray):
        if not HAS_SKLEARN or not self._source_model:
            return
        
        try:
            y_pred = self._source_model.predict(X)
            
            mae_moisture = mean_absolute_error(y[:, 0], y_pred[:, 0])
            mae_reconstitution = mean_absolute_error(y[:, 1], y_pred[:, 1])
            r2_moisture = r2_score(y[:, 0], y_pred[:, 0])
            r2_reconstitution = r2_score(y[:, 1], y_pred[:, 1])
            
            performance = ModelPerformance(
                mae_moisture=float(mae_moisture),
                mae_reconstitution=float(mae_reconstitution),
                r2_moisture=float(r2_moisture),
                r2_reconstitution=float(r2_reconstitution),
                sample_count=len(y),
                last_update=datetime.now().timestamp()
            )
            
            self._last_performance = performance
            self._performance_history.append(performance)
            
        except Exception as e:
            print(f"[PLS] 性能评估失败: {e}")
    
    def train_source_model(self, X: np.ndarray, y: np.ndarray):
        if not HAS_SKLEARN:
            return False
        
        try:
            n_samples = X.shape[0]
            n_components = min(self.n_components, n_samples - 1, X.shape[1])
            
            if n_components < 2:
                print(f"[PLS] 样本不足, 无法训练源模型")
                return False
            
            self._source_model.set_params(pls__n_components=n_components)
            self._source_model.fit(X, y)
            self._source_trained = True
            
            print(f"[PLS] 源模型训练完成, 样本数: {n_samples}, 主成分数: {n_components}")
            return True
        except Exception as e:
            print(f"[PLS] 源模型训练失败: {e}")
            return False
    
    def transfer_to_new_formula(self, source_formula_id: str, target_formula_id: str,
                                target_data: Tuple[np.ndarray, np.ndarray]) -> bool:
        if not HAS_SKLEARN or not self._source_trained:
            return False
        
        if source_formula_id not in self._formula_library:
            return False
        
        try:
            X_target, y_target = target_data
            
            if len(y_target) < 10:
                print(f"[PLS] 目标域样本不足, 至少需要10个样本")
                return False
            
            self._target_model = clone(self._source_model)
            
            n_components = min(self.n_components, len(y_target) - 1, X_target.shape[1])
            self._target_model.set_params(pls__n_components=n_components)
            
            source_coefs = self._source_model.named_steps['pls'].coef_
            
            self._target_model.fit(X_target, y_target)
            
            target_coefs = self._target_model.named_steps['pls'].coef_
            blended_coefs = self._transfer_alpha * target_coefs + (1 - self._transfer_alpha) * source_coefs
            self._target_model.named_steps['pls'].coef_ = blended_coefs
            
            self._target_trained = True
            
            print(f"[PLS] 迁移学习完成: {source_formula_id} -> {target_formula_id}")
            return True
            
        except Exception as e:
            print(f"[PLS] 迁移学习失败: {e}")
            return False
    
    def set_thresholds(self, moisture: float, reconstitution: float):
        self.moisture_threshold = moisture
        self.reconstitution_threshold = reconstitution
    
    def get_model_info(self) -> Dict:
        info = {
            "has_sklearn": HAS_SKLEARN,
            "source_trained": self._source_trained,
            "target_trained": self._target_trained,
            "n_components": self.n_components,
            "labeled_samples": len(self._labeled_data),
            "history_samples": len(self._history_data),
            "adaptation_rate": self._adaptation_rate,
            "transfer_alpha": self._transfer_alpha,
            "drift_threshold": self._drift_threshold,
            "feature_names": self._feature_names,
            "target_names": self._target_names,
            "moisture_threshold": self.moisture_threshold,
            "reconstitution_threshold": self.reconstitution_threshold,
            "model_version": self._model_version,
            "registered_formulas": list(self._formula_library.keys()),
            "current_formula": self._current_formula.formula_id if self._current_formula else None
        }
        
        if self._last_performance:
            info["performance"] = {
                "mae_moisture": round(self._last_performance.mae_moisture, 4),
                "mae_reconstitution": round(self._last_performance.mae_reconstitution, 4),
                "r2_moisture": round(self._last_performance.r2_moisture, 4),
                "r2_reconstitution": round(self._last_performance.r2_reconstitution, 4),
                "sample_count": self._last_performance.sample_count
            }
        
        return info


class QualityPredictionService:
    def __init__(self, n_devices: int = 10):
        self.predictors: Dict[int, AdaptivePLSPredictor] = {
            i: AdaptivePLSPredictor() for i in range(1, n_devices + 1)
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
        
        self._default_formulas = [
            ("FORMULA-001", "标准单抗", "mAb", 3.0, 120.0,
             {"freezing_rate": -1.0, "primary_temp": -40.0, "secondary_temp": 25.0}),
            ("FORMULA-002", "重组蛋白", "Protein", 2.5, 100.0,
             {"freezing_rate": -0.8, "primary_temp": -45.0, "secondary_temp": 30.0}),
            ("FORMULA-003", "疫苗制剂", "Vaccine", 3.5, 150.0,
             {"freezing_rate": -1.5, "primary_temp": -35.0, "secondary_temp": 20.0}),
        ]
        
        for device_id in range(1, n_devices + 1):
            for fid, fname, ptype, tmoist, trecon, fcurve in self._default_formulas:
                self.predictors[device_id].register_formula(
                    fid, fname, ptype, tmoist, trecon, fcurve
                )

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
                reconstitution_threshold=120.0,
                model_version="v2.0-transfer"
            )
        
        return self.predictors[device_id].predict(
            all_temps, all_vacuums, all_powers, cold_traps
        )

    def get_predictor(self, device_id: int) -> AdaptivePLSPredictor:
        return self.predictors[device_id]

    def set_thresholds(self, moisture: float, reconstitution: float):
        for predictor in self.predictors.values():
            predictor.set_thresholds(moisture, reconstitution)
    
    def set_formula(self, device_id: int, formula_id: str) -> bool:
        if device_id in self.predictors:
            return self.predictors[device_id].set_current_formula(formula_id)
        return False
    
    def add_labeled_data(self, device_id: int, 
                         temp_history: List[List[float]],
                         vacuum_history: List[List[float]],
                         power_history: List[List[float]],
                         cold_trap_history: List[float],
                         actual_moisture: float,
                         actual_reconstitution: float,
                         formula_id: Optional[str] = None):
        if device_id in self.predictors:
            self.predictors[device_id].add_labeled_data(
                temp_history, vacuum_history, power_history, cold_trap_history,
                actual_moisture, actual_reconstitution, formula_id
            )
