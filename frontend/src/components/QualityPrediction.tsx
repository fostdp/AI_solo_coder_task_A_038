import { useEffect, useState } from 'react';
import { TrendingUp, Droplets, Clock, AlertTriangle, CheckCircle, RefreshCw } from 'lucide-react';
import type { PredictionResultData } from '@/types';
import { predictionApi } from '@/services/api';

interface QualityPredictionProps {
  deviceId: number;
  onPredict?: (result: PredictionResultData) => void;
}

const QualityPrediction = ({ deviceId, onPredict }: QualityPredictionProps) => {
  const [latestResult, setLatestResult] = useState<PredictionResultData | null>(null);
  const [history, setHistory] = useState<PredictionResultData[]>([]);
  const [isPredicting, setIsPredicting] = useState(false);
  const [moistureMax, setMoistureMax] = useState(3.0);
  const [reconstitutionMax, setReconstitutionMax] = useState(5.0);

  useEffect(() => {
    fetchHistory();
  }, [deviceId]);

  const fetchHistory = async () => {
    try {
      const result = await predictionApi.getResults(deviceId, 5);
      setHistory(result);
      if (result.length > 0) {
        setLatestResult(result[0]);
      }
    } catch (error) {
      console.error('获取预测历史失败:', error);
    }
  };

  const handlePredict = async () => {
    setIsPredicting(true);
    try {
      const result = await predictionApi.predictQuality(deviceId, `BATCH-${Date.now()}`);
      setLatestResult(result);
      setHistory((prev) => [result, ...prev].slice(0, 5));
      onPredict?.(result);
    } catch (error) {
      console.error('质量预测失败:', error);
    } finally {
      setIsPredicting(false);
    }
  };

  const handleSaveThresholds = async () => {
    try {
      await predictionApi.setThresholds(moistureMax, reconstitutionMax);
    } catch (error) {
      console.error('保存阈值失败:', error);
    }
  };

  const GaugeChart = ({
    value,
    max,
    label,
    unit,
    color,
  }: {
    value: number;
    max: number;
    label: string;
    unit: string;
    color: string;
  }) => {
    const percentage = Math.min((value / max) * 100, 100);
    const isWarning = value > max * 0.8;
    const isDanger = value > max;
    const strokeColor = isDanger ? '#EF4444' : isWarning ? '#F59E0B' : color;

    return (
      <div className="text-center">
        <div className="relative w-32 h-16 mx-auto mb-2">
          <svg className="w-full h-full" viewBox="0 0 100 50">
            <path
              d="M 10 45 A 40 40 0 0 1 90 45"
              fill="none"
              stroke="#1E293B"
              strokeWidth="8"
              strokeLinecap="round"
            />
            <path
              d="M 10 45 A 40 40 0 0 1 90 45"
              fill="none"
              stroke={strokeColor}
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={`${percentage * 1.26} 200`}
              style={{ transition: 'stroke-dasharray 0.5s ease-out' }}
            />
            <circle cx="50" cy="45" r="5" fill={strokeColor} />
            <text
              x="50"
              y="35"
              textAnchor="middle"
              className="text-xl font-bold font-mono"
              fill={isDanger ? '#EF4444' : '#F1F5F9'}
            >
              {value.toFixed(2)}
            </text>
          </svg>
        </div>
        <div className="text-sm font-medium text-slate-300">{label}</div>
        <div className="text-xs text-slate-500">
          {unit} · 阈值: {max}{unit}
        </div>
        {isDanger && (
          <div className="mt-1 text-xs text-red-400 flex items-center justify-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            超标
          </div>
        )}
        {!isDanger && isWarning && (
          <div className="mt-1 text-xs text-yellow-400 flex items-center justify-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            接近阈值
          </div>
        )}
        {!isDanger && !isWarning && (
          <div className="mt-1 text-xs text-green-400 flex items-center justify-center gap-1">
            <CheckCircle className="w-3 h-3" />
            合格
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="bg-slate-900/50 rounded-xl border border-slate-700 overflow-hidden">
      <div className="p-4 border-b border-slate-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-500/20 flex items-center justify-center">
              <TrendingUp className="w-5 h-5 text-purple-400" />
            </div>
            <div>
              <h3 className="font-semibold text-slate-100">
                产品质量预测
              </h3>
              <p className="text-xs text-slate-400">
                基于PLS回归模型
              </p>
            </div>
          </div>
          <button
            onClick={handlePredict}
            disabled={isPredicting}
            className="flex items-center gap-2 px-4 py-2 bg-purple-500 hover:bg-purple-600 disabled:bg-slate-600 text-white rounded-lg text-sm font-medium transition-colors"
          >
            <RefreshCw
              className={`w-4 h-4 ${isPredicting ? 'animate-spin' : ''}`}
            />
            {isPredicting ? '预测中...' : '执行预测'}
          </button>
        </div>
      </div>

      <div className="p-4">
        {!latestResult ? (
          <div className="p-8 text-center text-slate-500">
            <TrendingUp className="w-12 h-12 mx-auto mb-2 text-purple-500/50" />
            <p>暂无预测数据，点击"执行预测"开始</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <GaugeChart
                value={latestResult.moisture_content.predicted}
                max={latestResult.moisture_content.threshold}
                label="水分含量"
                unit="%"
                color="#06B6D4"
              />
              <GaugeChart
                value={latestResult.reconstitution_time.predicted}
                max={latestResult.reconstitution_time.threshold}
                label="复溶时间"
                unit="min"
                color="#10B981"
              />
              <div className="flex flex-col items-center justify-center p-4 bg-slate-800/50 rounded-lg border border-slate-700">
                <div className="text-xs text-slate-400 mb-2">干燥速率</div>
                <div className="text-2xl font-mono font-bold text-orange-400">
                  {latestResult.drying_rate.toFixed(4)}
                </div>
                <div className="text-xs text-slate-500">g/h·m²</div>
                <div
                  className={`mt-2 px-3 py-1 rounded-full text-sm font-medium ${
                    latestResult.is_qualified
                      ? 'bg-green-500/20 text-green-400'
                      : 'bg-red-500/20 text-red-400'
                  }`}
                >
                  {latestResult.is_qualified ? '✓ 合格' : '✗ 不合格'}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="p-3 bg-slate-800/50 rounded-lg border border-slate-700">
                <div className="flex items-center gap-2 text-slate-400 text-sm mb-2">
                  <Droplets className="w-4 h-4" />
                  水分含量置信度
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-lg font-mono text-cyan-400">
                    {(latestResult.moisture_content.confidence * 100).toFixed(1)}%
                  </span>
                  <span className="text-xs text-slate-500">
                    阈值: {latestResult.moisture_content.threshold}%
                  </span>
                </div>
                <div className="mt-2 w-full bg-slate-700 rounded-full h-2">
                  <div
                    className="bg-cyan-500 h-2 rounded-full transition-all duration-500"
                    style={{
                      width: `${latestResult.moisture_content.confidence * 100}%`,
                    }}
                  />
                </div>
              </div>

              <div className="p-3 bg-slate-800/50 rounded-lg border border-slate-700">
                <div className="flex items-center gap-2 text-slate-400 text-sm mb-2">
                  <Clock className="w-4 h-4" />
                  复溶时间置信度
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-lg font-mono text-green-400">
                    {(latestResult.reconstitution_time.confidence * 100).toFixed(1)}%
                  </span>
                  <span className="text-xs text-slate-500">
                    阈值: {latestResult.reconstitution_time.threshold}min
                  </span>
                </div>
                <div className="mt-2 w-full bg-slate-700 rounded-full h-2">
                  <div
                    className="bg-green-500 h-2 rounded-full transition-all duration-500"
                    style={{
                      width: `${latestResult.reconstitution_time.confidence * 100}%`,
                    }}
                  />
                </div>
              </div>
            </div>

            {history.length > 0 && (
              <div className="pt-4 border-t border-slate-700">
                <h4 className="text-sm font-medium text-slate-300 mb-3">
                  历史预测
                </h4>
                <div className="space-y-2">
                  {history.map((item, index) => (
                    <div
                      key={index}
                      className="flex items-center justify-between p-2 bg-slate-800/30 rounded-lg text-sm"
                    >
                      <div className="flex items-center gap-3">
                        <span
                          className={`w-2 h-2 rounded-full ${
                            item.is_qualified
                              ? 'bg-green-500'
                              : 'bg-red-500'
                          }`}
                        />
                        <span className="text-slate-400">
                          {item.timestamp &&
                            new Date(item.timestamp).toLocaleString('zh-CN')}
                        </span>
                      </div>
                      <div className="flex gap-4 text-xs">
                        <span className="text-cyan-400 font-mono">
                          {item.moisture_content.predicted.toFixed(2)}%
                        </span>
                        <span className="text-green-400 font-mono">
                          {item.reconstitution_time.predicted.toFixed(2)}min
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="pt-4 border-t border-slate-700">
              <h4 className="text-sm font-medium text-slate-300 mb-3">
                阈值设置
              </h4>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    水分含量最大阈值
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      min="0.1"
                      max="10"
                      step="0.1"
                      value={moistureMax}
                      onChange={(e) => setMoistureMax(parseFloat(e.target.value))}
                      className="flex-1 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-100 font-mono focus:outline-none focus:border-purple-500"
                    />
                    <span className="text-slate-400 self-center">%</span>
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-400 block mb-1">
                    复溶时间最大阈值
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      min="1"
                      max="30"
                      step="0.5"
                      value={reconstitutionMax}
                      onChange={(e) =>
                        setReconstitutionMax(parseFloat(e.target.value))
                      }
                      className="flex-1 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-100 font-mono focus:outline-none focus:border-purple-500"
                    />
                    <span className="text-slate-400 self-center">min</span>
                  </div>
                </div>
              </div>
              <button
                onClick={handleSaveThresholds}
                className="mt-3 w-full py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition-colors"
              >
                保存阈值
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default QualityPrediction;
