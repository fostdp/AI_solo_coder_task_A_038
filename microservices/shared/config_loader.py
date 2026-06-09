"""
配置加载器
从YAML文件加载控制参数、模型参数、告警阈值
"""

import yaml
import os
from pathlib import Path
from typing import Dict, Any
from dataclasses import dataclass, field


DEFAULT_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


@dataclass
class ControlConfig:
    fuzzy_control: Dict[str, Any] = field(default_factory=dict)
    ilc_control: Dict[str, Any] = field(default_factory=dict)
    power_allocation: Dict[str, Any] = field(default_factory=dict)
    temperature_limits: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelConfig:
    pls_model: Dict[str, Any] = field(default_factory=dict)
    transfer_learning: Dict[str, Any] = field(default_factory=dict)
    adaptive_update: Dict[str, Any] = field(default_factory=dict)
    concept_drift: Dict[str, Any] = field(default_factory=dict)
    formulas: list = field(default_factory=list)


@dataclass
class AlarmConfig:
    global_config: Dict[str, Any] = field(default_factory=dict)
    temperature: Dict[str, Any] = field(default_factory=dict)
    vacuum: Dict[str, Any] = field(default_factory=dict)
    cold_trap: Dict[str, Any] = field(default_factory=dict)
    quality: Dict[str, Any] = field(default_factory=dict)
    severity_levels: Dict[str, Any] = field(default_factory=dict)
    mqtt_publisher: Dict[str, Any] = field(default_factory=dict)
    notification_channels: list = field(default_factory=list)
    auto_suppression: Dict[str, Any] = field(default_factory=dict)


class ConfigLoader:
    """配置加载器"""

    def __init__(self, config_dir: str = None):
        self.config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
        self._control_config: ControlConfig = None
        self._model_config: ModelConfig = None
        self._alarm_config: AlarmConfig = None

    def load_control_config(self) -> ControlConfig:
        """加载控制参数"""
        if self._control_config is None:
            file_path = self.config_dir / "control_params.yaml"
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            self._control_config = ControlConfig(
                fuzzy_control=data.get('fuzzy_control', {}),
                ilc_control=data.get('ilc_control', {}),
                power_allocation=data.get('power_allocation', {}),
                temperature_limits=data.get('temperature_limits', {})
            )
        return self._control_config

    def load_model_config(self) -> ModelConfig:
        """加载模型参数"""
        if self._model_config is None:
            file_path = self.config_dir / "model_params.yaml"
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            self._model_config = ModelConfig(
                pls_model=data.get('pls_model', {}),
                transfer_learning=data.get('transfer_learning', {}),
                adaptive_update=data.get('adaptive_update', {}),
                concept_drift=data.get('concept_drift', {}),
                formulas=data.get('formulas', [])
            )
        return self._model_config

    def load_alarm_config(self) -> AlarmConfig:
        """加载告警阈值"""
        if self._alarm_config is None:
            file_path = self.config_dir / "alarm_thresholds.yaml"
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            self._alarm_config = AlarmConfig(
                global_config=data.get('global', {}),
                temperature=data.get('temperature', {}),
                vacuum=data.get('vacuum', {}),
                cold_trap=data.get('cold_trap', {}),
                quality=data.get('quality', {}),
                severity_levels=data.get('severity_levels', {}),
                mqtt_publisher=data.get('mqtt_publisher', {}),
                notification_channels=data.get('notification_channels', []),
                auto_suppression=data.get('auto_suppression', {})
            )
        return self._alarm_config

    def reload_all(self):
        """重新加载所有配置"""
        self._control_config = None
        self._model_config = None
        self._alarm_config = None
        return self.load_control_config(), self.load_model_config(), self.load_alarm_config()

    def get(self, config_type: str, key_path: str, default: Any = None) -> Any:
        """获取配置值"""
        parts = key_path.split('.')
        config = None

        if config_type == 'control':
            config = self.load_control_config()
        elif config_type == 'model':
            config = self.load_model_config()
        elif config_type == 'alarm':
            config = self.load_alarm_config()
        else:
            return default

        value = config
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                return default
        return value


# 全局配置加载器实例
config_loader = ConfigLoader()
