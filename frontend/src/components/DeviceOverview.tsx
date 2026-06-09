import { Activity, Thermometer, Gauge, AlertTriangle } from 'lucide-react';
import type { RealtimeData } from '@/types';

interface DeviceOverviewProps {
  deviceId: number;
  shelfData: RealtimeData[];
  onShelfClick?: (shelfId: number) => void;
  selectedShelf?: number;
}

const DeviceOverview = ({
  deviceId,
  shelfData,
  onShelfClick,
  selectedShelf,
}: DeviceOverviewProps) => {
  const shelfColors = [
    { normal: 'bg-cyan-500/20 border-cyan-500/30 text-cyan-400' },
    { normal: 'bg-purple-500/20 border-purple-500/30 text-purple-400' },
    { normal: 'bg-green-500/20 border-green-500/30 text-green-400' },
    { normal: 'bg-orange-500/20 border-orange-500/30 text-orange-400' },
    { normal: 'bg-pink-500/20 border-pink-500/30 text-pink-400' },
  ];

  const getDeviceStatus = () => {
    const hasCriticalAlarm = shelfData.some(
      (d) => d.has_alarm && d.temperature_diff > 1.5
    );
    const hasWarning = shelfData.some((d) => d.has_alarm);

    if (hasCriticalAlarm) return { status: '严重', color: 'text-red-400', bg: 'bg-red-500/20' };
    if (hasWarning) return { status: '警告', color: 'text-yellow-400', bg: 'bg-yellow-500/20' };
    return { status: '正常', color: 'text-green-400', bg: 'bg-green-500/20' };
  };

  const getAvgTempDiff = () => {
    const diffs = shelfData.map((d) => d.temperature_diff);
    return diffs.reduce((a, b) => a + b, 0) / diffs.length;
  };

  const getAvgVacuum = () => {
    const vacuums = shelfData.map((d) => d.avg_vacuum);
    return vacuums.reduce((a, b) => a + b, 0) / vacuums.length;
  };

  const getColdTrapTemp = () => {
    return shelfData[0]?.cold_trap_temp || 0;
  };

  const deviceStatus = getDeviceStatus();
  const avgTempDiff = getAvgTempDiff();
  const avgVacuum = getAvgVacuum();
  const coldTrapTemp = getColdTrapTemp();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-cyan-500/20 flex items-center justify-center">
            <Activity className="w-6 h-6 text-cyan-400" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-100">
              冻干机 #{deviceId.toString().padStart(2, '0')}
            </h2>
            <p className="text-sm text-slate-400">
              5层搁板 · 40个温度传感器
            </p>
          </div>
        </div>
        <span
          className={`px-3 py-1 rounded-full text-sm font-medium ${deviceStatus.bg} ${deviceStatus.color}`}
        >
          {deviceStatus.status}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-1">
            <Thermometer className="w-4 h-4" />
            平均温差
          </div>
          <div
            className={`text-2xl font-mono font-bold ${
              avgTempDiff > 1 ? 'text-red-400' : 'text-green-400'
            }`}
          >
            {avgTempDiff.toFixed(2)}℃
          </div>
          <div className="text-xs text-slate-500">
            目标: &lt;1.0℃
          </div>
        </div>

        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-1">
            <Gauge className="w-4 h-4" />
            真空度
          </div>
          <div className="text-2xl font-mono font-bold text-cyan-400">
            {avgVacuum.toFixed(4)}
          </div>
          <div className="text-xs text-slate-500">Pa</div>
        </div>

        <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-1">
            <AlertTriangle className="w-4 h-4" />
            冷阱温度
          </div>
          <div
            className={`text-2xl font-mono font-bold ${
              coldTrapTemp > -50 ? 'text-yellow-400' : 'text-blue-400'
            }`}
          >
            {coldTrapTemp.toFixed(1)}℃
          </div>
          <div className="text-xs text-slate-500">
            阈值: -50℃
          </div>
        </div>
      </div>

      <div className="grid grid-cols-5 gap-2">
        {shelfData.map((shelf, index) => {
          const color = shelfColors[index];
          const isSelected = selectedShelf === shelf.shelf_id;
          const hasAlarm = shelf.has_alarm;

          return (
            <button
              key={shelf.shelf_id}
              onClick={() => onShelfClick?.(shelf.shelf_id)}
              className={`
                relative p-3 rounded-lg border transition-all duration-200
                ${
                  isSelected
                    ? 'border-cyan-500 bg-cyan-500/10 ring-2 ring-cyan-500/30'
                    : `${color.normal} hover:bg-slate-700/50`
                }
              `}
            >
              <div className="text-xs text-slate-400 mb-1">
                搁板 {shelf.shelf_id}
              </div>
              <div
                className={`text-lg font-mono font-bold ${
                  hasAlarm ? 'text-red-400' : ''
                }`}
              >
                {shelf.avg_temperature.toFixed(1)}℃
              </div>
              <div className="text-xs mt-1">
                <span
                  className={`${
                    shelf.temperature_diff > 1
                      ? 'text-red-400'
                      : 'text-slate-500'
                  }`}
                >
                  Δ{shelf.temperature_diff.toFixed(2)}℃
                </span>
              </div>
              {hasAlarm && (
                <div className="absolute -top-1 -right-1">
                  <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
};

export default DeviceOverview;
