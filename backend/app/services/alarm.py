"""
告警管理服务
检测搁板温差超限、真空度异常、冷阱温度过高、质量预测不合格
通过MQTT推送告警至MES系统
"""

import numpy as np
from typing import List, Dict, Optional, Callable
from datetime import datetime, timezone
from collections import deque
from dataclasses import dataclass
from uuid import UUID
import json
import asyncio


@dataclass
class AlarmThresholds:
    temp_diff: float = 1.0
    vacuum_min: float = 0.1
    vacuum_max: float = 100.0
    cold_trap_max: float = -50.0
    moisture_max: float = 3.0
    reconstitution_max: float = 120.0


@dataclass
class AlarmEvent:
    id: UUID
    timestamp: datetime
    device_id: int
    shelf_id: Optional[int]
    alarm_type: str
    severity: str
    message: str
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


class AlarmDetector:
    def __init__(self, thresholds: AlarmThresholds = None):
        self.thresholds = thresholds or AlarmThresholds()
        self.active_alarms: Dict[str, AlarmEvent] = {}
        self.alarm_history: deque = deque(maxlen=1000)
        self.cooldown_period: Dict[str, datetime] = {}
        self.cooldown_seconds = 30

    def _in_cooldown(self, alarm_key: str) -> bool:
        if alarm_key in self.cooldown_period:
            if datetime.now(timezone.utc) < self.cooldown_period[alarm_key]:
                return True
        return False

    def _set_cooldown(self, alarm_key: str):
        self.cooldown_period[alarm_key] = datetime.now(timezone.utc) + \
            timedelta(seconds=self.cooldown_seconds)

    def check_temperature_diff(self, device_id: int, shelf_id: int,
                               temperatures: List[float]) -> Optional[AlarmEvent]:
        temp_diff = max(temperatures) - min(temperatures)
        alarm_key = f"temp_diff_{device_id}_{shelf_id}"
        
        if temp_diff > self.thresholds.temp_diff and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                id=UUID(int=abs(hash(alarm_key + str(datetime.now().timestamp()))), version=4),
                timestamp=datetime.now(timezone.utc),
                device_id=device_id,
                shelf_id=shelf_id,
                alarm_type="temperature_diff",
                severity="warning" if temp_diff < self.thresholds.temp_diff * 1.5 else "critical",
                message=f"设备{device_id}搁板{shelf_id}温度温差超限: {temp_diff:.2f}℃ > {self.thresholds.temp_diff}℃"
            )
        return None

    def check_vacuum(self, device_id: int, shelf_id: int,
                     vacuum_levels: List[float]) -> Optional[AlarmEvent]:
        avg_vacuum = np.mean(vacuum_levels)
        alarm_key = f"vacuum_{device_id}_{shelf_id}"
        
        if avg_vacuum < self.thresholds.vacuum_min and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                id=UUID(int=abs(hash(alarm_key + str(datetime.now().timestamp()))), version=4),
                timestamp=datetime.now(timezone.utc),
                device_id=device_id,
                shelf_id=shelf_id,
                alarm_type="vacuum_abnormal",
                severity="critical",
                message=f"设备{device_id}搁板{shelf_id}真空度过低: {avg_vacuum:.4f}Pa < {self.thresholds.vacuum_min}Pa"
            )
        elif avg_vacuum > self.thresholds.vacuum_max and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                id=UUID(int=abs(hash(alarm_key + str(datetime.now().timestamp()))), version=4),
                timestamp=datetime.now(timezone.utc),
                device_id=device_id,
                shelf_id=shelf_id,
                alarm_type="vacuum_abnormal",
                severity="warning",
                message=f"设备{device_id}搁板{shelf_id}真空度过高: {avg_vacuum:.4f}Pa > {self.thresholds.vacuum_max}Pa"
            )
        return None

    def check_cold_trap(self, device_id: int, shelf_id: int,
                        cold_trap_temp: float) -> Optional[AlarmEvent]:
        alarm_key = f"cold_trap_{device_id}"
        
        if cold_trap_temp > self.thresholds.cold_trap_max and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            return AlarmEvent(
                id=UUID(int=abs(hash(alarm_key + str(datetime.now().timestamp()))), version=4),
                timestamp=datetime.now(timezone.utc),
                device_id=device_id,
                shelf_id=shelf_id,
                alarm_type="cold_trap_high",
                severity="critical",
                message=f"设备{device_id}冷阱温度过高: {cold_trap_temp:.2f}℃ > {self.thresholds.cold_trap_max}℃"
            )
        return None

    def check_quality_prediction(self, device_id: int, 
                                 is_qualified: bool,
                                 moisture: float,
                                 reconstitution: float) -> Optional[AlarmEvent]:
        alarm_key = f"quality_{device_id}"
        
        if not is_qualified and not self._in_cooldown(alarm_key):
            self._set_cooldown(alarm_key)
            issues = []
            if moisture > self.thresholds.moisture_max:
                issues.append(f"水分含量{moisture:.2f}% > {self.thresholds.moisture_max}%")
            if reconstitution > self.thresholds.reconstitution_max:
                issues.append(f"复溶时间{reconstitution:.1f}s > {self.thresholds.reconstitution_max}s")
            
            return AlarmEvent(
                id=UUID(int=abs(hash(alarm_key + str(datetime.now().timestamp()))), version=4),
                timestamp=datetime.now(timezone.utc),
                device_id=device_id,
                shelf_id=None,
                alarm_type="quality_prediction",
                severity="critical",
                message=f"设备{device_id}产品质量预测不合格: {'; '.join(issues)}"
            )
        return None

    def process_telemetry(self, device_id: int, shelf_id: int,
                          temperatures: List[float],
                          vacuum_levels: List[float],
                          cold_trap_temp: float) -> List[AlarmEvent]:
        alarms = []
        
        alarm = self.check_temperature_diff(device_id, shelf_id, temperatures)
        if alarm:
            alarms.append(alarm)
            
        alarm = self.check_vacuum(device_id, shelf_id, vacuum_levels)
        if alarm:
            alarms.append(alarm)
            
        alarm = self.check_cold_trap(device_id, shelf_id, cold_trap_temp)
        if alarm:
            alarms.append(alarm)
        
        for alarm in alarms:
            key = f"{alarm.alarm_type}_{alarm.device_id}_{alarm.shelf_id or 'all'}"
            self.active_alarms[key] = alarm
            self.alarm_history.append(alarm)
        
        return alarms

    def acknowledge_alarm(self, alarm_id: UUID, acknowledged_by: str) -> bool:
        for key, alarm in self.active_alarms.items():
            if alarm.id == alarm_id:
                alarm.acknowledged = True
                alarm.acknowledged_by = acknowledged_by
                alarm.acknowledged_at = datetime.now(timezone.utc)
                del self.active_alarms[key]
                return True
        return False

    def get_active_alarms(self) -> List[AlarmEvent]:
        return [a for a in self.active_alarms.values() if not a.acknowledged]

    def get_alarm_history(self, limit: int = 100) -> List[AlarmEvent]:
        return list(self.alarm_history)[-limit:]

    def update_thresholds(self, thresholds: AlarmThresholds):
        self.thresholds = thresholds


from datetime import timedelta
