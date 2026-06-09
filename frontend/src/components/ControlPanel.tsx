import { useState, useEffect } from 'react';
import { Settings, Zap, RefreshCw, ThermometerSun } from 'lucide-react';
import type { RealtimeData } from '@/types';
import { controlApi } from '@/services/api';
import { useAppStore } from '@/store';

interface ControlPanelProps {
  deviceId: number;
  shelfId: number;
  data: RealtimeData | null;
}

const ControlPanel = ({ deviceId, shelfId, data }: ControlPanelProps) => {
  const [autoMode, setAutoMode] = useState(true);
  const [powerAdjustments, setPowerAdjustments] = useState<number[]>([0, 0, 0, 0, 0, 0, 0, 0]);
  const [threshold, setThreshold] = useState(1.0);
  const [isApplying, setIsApplying] = useState(false);
  const { setAutoMode: setStoreAutoMode } = useAppStore();

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const status = await controlApi.getStatus(deviceId);
        setAutoMode(status.auto_mode);
        setStoreAutoMode(deviceId, status.auto_mode);
        const th = await controlApi.getThreshold();
        setThreshold(th.threshold);
      } catch (error) {
        console.error('获取控制状态失败:', error);
      }
    };
    fetchStatus();
  }, [deviceId, shelfId, setStoreAutoMode]);

  const handleAutoModeToggle = async () => {
    try {
      const newMode = !autoMode;
      await controlApi.setMode(deviceId, newMode);
      setAutoMode(newMode);
      setStoreAutoMode(deviceId, newMode);
    } catch (error) {
      console.error('切换模式失败:', error);
    }
  };

  const handlePowerSliderChange = (index: number, value: number) => {
    const newAdjustments = [...powerAdjustments];
    newAdjustments[index] = value;
    setPowerAdjustments(newAdjustments);
  };

  const handleApplyAdjustments = async () => {
    setIsApplying(true);
    try {
      await controlApi.sendCommand({
        device_id: deviceId,
        shelf_id: shelfId,
        power_adjustments: powerAdjustments,
        auto_mode: autoMode,
      });
    } catch (error) {
      console.error('应用功率调整失败:', error);
    } finally {
      setIsApplying(false);
    }
  };

  const handleCalculate = async () => {
    try {
      const result = await controlApi.calculateAdjustment(deviceId, shelfId);
      setPowerAdjustments(result.power_adjustments);
    } catch (error) {
      console.error('计算调整量失败:', error);
    }
  };

  const handleThresholdSave = async () => {
    try {
      await controlApi.setThreshold(threshold);
    } catch (error) {
      console.error('保存阈值失败:', error);
    }
  };

  if (!data) {
    return (
      <div className="bg-slate-900/50 rounded-xl border border-slate-700 p-6">
        <p className="text-slate-500 text-center">等待数据...</p>
      </div>
    );
  }

  const avgPower = data.heating_powers.reduce((a, b) => a + b, 0) / data.heating_powers.length;

  return (
    <div className="bg-slate-900/50 rounded-xl border border-slate-700 overflow-hidden">
      <div className="p-4 border-b border-slate-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Settings className="w-5 h-5 text-cyan-400" />
            <div>
              <h3 className="font-semibold text-slate-100">
                功率控制面板
              </h3>
              <p className="text-xs text-slate-400">
                设备 #{deviceId} · 搁板 {shelfId}
              </p>
            </div>
          </div>
          <button
            onClick={handleAutoModeToggle}
            className={`relative w-14 h-7 rounded-full transition-colors ${
              autoMode ? 'bg-cyan-500' : 'bg-slate-600'
            }`}
          >
            <span
              className={`absolute top-1 w-5 h-5 bg-white rounded-full transition-transform ${
                autoMode ? 'left-8' : 'left-1'
              }`}
            />
            <span className="sr-only">切换自动模式</span>
          </button>
          <span
            className={`text-xs ${
              autoMode ? 'text-cyan-400' : 'text-slate-400'
            }`}
          >
            {autoMode ? '自动' : '手动'}
          </span>
        </div>
      </div>

      <div className="p-4 space-y-4">
        <div className="grid grid-cols-4 gap-4 p-3 bg-slate-800/50 rounded-lg">
          <div>
            <div className="text-xs text-slate-400 mb-1">当前温差</div>
            <div
              className={`text-xl font-mono font-bold ${
                data.temperature_diff > threshold
                  ? 'text-red-400'
                  : 'text-green-400'
              }`}
            >
              {data.temperature_diff.toFixed(2)}℃
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">平均温度</div>
            <div className="text-xl font-mono font-bold text-cyan-400">
              {data.avg_temperature.toFixed(1)}℃
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">平均功率</div>
            <div className="text-xl font-mono font-bold text-orange-400">
              {avgPower.toFixed(1)}%
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">控制状态</div>
            <div
              className={`text-xl font-bold ${
                autoMode ? 'text-green-400' : 'text-yellow-400'
              }`}
            >
              {autoMode ? '运行中' : '手动'}
            </div>
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-slate-300">
              <Zap className="w-4 h-4 inline mr-1" />
              加热丝功率调整
            </h4>
            <button
              onClick={handleCalculate}
              disabled={!autoMode}
              className="flex items-center gap-1 px-3 py-1 text-xs bg-cyan-500/20 text-cyan-400 rounded hover:bg-cyan-500/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <RefreshCw className="w-3 h-3" />
              智能计算
            </button>
          </div>
          <div className="grid grid-cols-4 gap-3">
            {data.heating_powers.map((power, index) => (
              <div
                key={index}
                className="space-y-2 p-3 bg-slate-800 rounded-lg border border-slate-700"
              >
                <div className="flex justify-between text-xs">
                  <span className="text-slate-400">
                    <ThermometerSun className="w-3 h-3 inline mr-1" />
                    H{index + 1}
                  </span>
                  <span className="text-slate-500 font-mono">{power.toFixed(1)}%</span>
                </div>
                <input
                  type="range"
                  min="-10"
                  max="10"
                  step="0.5"
                  value={powerAdjustments[index]}
                  onChange={(e) =>
                    handlePowerSliderChange(index, parseFloat(e.target.value))
                  }
                  disabled={autoMode}
                  className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: 'linear-gradient(to right, #ef4444 0%, #f59e0b 50%, #10b981 100%)',
                  }}
                />
                <div className="text-center text-xs">
                  <span
                    className={`font-mono ${
                      powerAdjustments[index] > 0
                        ? 'text-red-400'
                        : powerAdjustments[index] < 0
                        ? 'text-blue-400'
                        : 'text-slate-500'
                    }`}
                  >
                    {powerAdjustments[index] > 0
                      ? `+${powerAdjustments[index].toFixed(1)}`
                      : powerAdjustments[index].toFixed(1)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={handleApplyAdjustments}
            disabled={autoMode || isApplying}
            className="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-600 disabled:cursor-not-allowed text-white rounded-lg font-medium transition-colors"
          >
            {isApplying ? '应用中...' : '应用调整'}
          </button>
        </div>

        <div className="pt-4 border-t border-slate-700">
          <h4 className="text-sm font-medium text-slate-300 mb-3">
            阈值设置
          </h4>
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <label className="text-xs text-slate-400 block mb-1">
                温差目标阈值
              </label>
              <div className="flex gap-2">
                <input
                  type="number"
                  min="0.1"
                  max="5"
                  step="0.1"
                  value={threshold}
                  onChange={(e) => setThreshold(parseFloat(e.target.value))}
                  className="flex-1 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-100 font-mono focus:outline-none focus:border-cyan-500"
                />
                <span className="text-slate-400 self-center">℃</span>
                <button
                  onClick={handleThresholdSave}
                  className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition-colors"
                >
                  保存
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ControlPanel;
