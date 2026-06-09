import { useState } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle, XCircle, Clock } from 'lucide-react';
import type { AlarmData } from '@/types';
import { alarmApi } from '@/services/api';
import { useAppStore } from '@/store';

interface AlarmPanelProps {
  alarms: AlarmData[];
  onAcknowledge?: (alarmId: string) => void;
}

const AlarmPanel = ({ alarms, onAcknowledge }: AlarmPanelProps) => {
  const [filter, setFilter] = useState<'all' | 'active' | 'acknowledged'>('all');
  const { acknowledgeAlarm } = useAppStore();

  const getAlarmTypeLabels: Record<string, { label: string; color: string }> = {
  temperature_diff: { label: '温差超限', color: 'text-red-400' },
  vacuum_abnormal: { label: '真空度异常', color: 'text-yellow-400' },
  cold_trap_high: { label: '冷阱温度过高', color: 'text-orange-400' },
  quality_prediction: { label: '质量预警', color: 'text-purple-400' },
};

  const getSeverityIcon = (severity: string) => {
    return severity === 'critical'
      ? <AlertCircle className="w-5 h-5 text-red-400" />
      : <AlertTriangle className="w-5 h-5 text-yellow-400" />;
  };

  const handleAcknowledge = async (alarmId: string) => {
    try {
      await alarmApi.acknowledge(alarmId, 'operator');
      acknowledgeAlarm(alarmId);
      onAcknowledge?.(alarmId);
    } catch (error) {
      console.error('确认告警失败:', error);
    }
  };

  const filteredAlarms = alarms.filter((alarm) => {
    if (filter === 'active') return !alarm.acknowledged;
    if (filter === 'acknowledged') return alarm.acknowledged;
    return true;
  });

  const activeCount = alarms.filter((a) => !a.acknowledged).length;
  const criticalCount = alarms.filter(
    (a) => a.severity === 'critical' && !a.acknowledged
  ).length;

  return (
    <div className="bg-slate-900/50 rounded-xl border border-slate-700 overflow-hidden">
      <div className="p-4 border-b border-slate-700">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="relative">
              <AlertTriangle className="w-5 h-5 text-red-400" />
              {activeCount > 0 && (
                <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full text-xs flex items-center justify-center text-white">
                  {activeCount}
                </span>
              )}
            </div>
            <div>
              <h3 className="font-semibold text-slate-100">实时告警</h3>
              {criticalCount > 0 && (
                <span className="text-xs text-red-400">
                  {criticalCount} 条严重告警
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-1">
            {(['all', 'active', 'acknowledged'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  filter === f
                    ? 'bg-cyan-500 text-white'
                    : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                }`}
              >
                {f === 'all' ? '全部' : f === 'active' ? '未处理' : '已确认'}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="max-h-96 overflow-y-auto">
        {filteredAlarms.length === 0 ? (
          <div className="p-8 text-center text-slate-500">
            <CheckCircle className="w-12 h-12 mx-auto mb-2 text-green-500/50" />
            <p>暂无告警信息</p>
          </div>
        ) : (
          filteredAlarms.map((alarm) => {
            const typeInfo = getAlarmTypeLabels[alarm.alarm_type] || {
              label: alarm.alarm_type,
              color: 'text-slate-400',
            };

            return (
              <div
                key={alarm.id}
                className={`p-4 border-b border-slate-700/50 hover:bg-slate-800/50 transition-colors ${
                  alarm.acknowledged ? 'opacity-60' : ''
                }`}
              >
                <div className="flex items-start gap-3">
                  {getSeverityIcon(alarm.severity)}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-1">
                      <span
                        className={`text-sm font-medium ${typeInfo.color}`}>
                        {typeInfo.label}
                      </span>
                      <span className="text-xs text-slate-500 font-mono">
                        #{alarm.device_id}
                        {alarm.shelf_id && ` · 搁板${alarm.shelf_id}`}
                      </span>
                    </div>
                    <p className="text-sm text-slate-300 mb-2">{alarm.message}</p>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1 text-xs text-slate-500">
                        <Clock className="w-3 h-3" />
                        {new Date(alarm.timestamp).toLocaleString('zh-CN')}
                      </div>
                      {!alarm.acknowledged ? (
                        <button
                          onClick={() => handleAcknowledge(alarm.id)}
                          className="flex items-center gap-1 px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 rounded transition-colors"
                        >
                          <CheckCircle className="w-3 h-3" />
                          确认
                        </button>
                      ) : (
                        <span className="flex items-center gap-1 text-xs text-green-400">
                          <CheckCircle className="w-3 h-3" />
                          {alarm.acknowledged_by} ·{' '}
                          {alarm.acknowledged_at &&
                            new Date(alarm.acknowledged_at).toLocaleTimeString(
                              'zh-CN'
                            )}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default AlarmPanel;
