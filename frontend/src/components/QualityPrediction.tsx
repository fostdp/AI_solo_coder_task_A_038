import { useEffect, useState, useRef, useCallback } from 'react';
import { TrendingUp, Droplets, Clock, AlertTriangle, CheckCircle, RefreshCw } from 'lucide-react';
import type { PredictionResultData } from '@/types';
import { predictionApi } from '@/services/api';
import {
  PredictionResult,
  DEFAULT_GAUGE_CONFIG,
  drawGauge,
  drawConfidenceBar,
  formatPredictionMessage,
  getTrendIcon,
  calculateQualityScore,
  formatHistoryItem,
  getStatusBadgeStyle,
  GaugeConfig,
  PredictionHistoryItem,
} from './quality_dashboard';

interface QualityPredictionProps {
  deviceId: number;
  onPredict?: (result: PredictionResultData) => void;
}

const QualityPrediction = ({ deviceId, onPredict }: QualityPredictionProps) => {
  const [latestResult, setLatestResult] = useState<PredictionResultData | null>(null);
  const [history, setHistory] = useState<PredictionResultData[]>([]);
  const [isPredicting, setIsPredicting] = useState(false);
  const [moistureMax, setMoistureMax] = useState(3.0);
  const [reconstitutionMax, setReconstitutionMax] = useState(120);
  const [previousMoisture, setPreviousMoisture] = useState<number | null>(null);
  const [previousReconstitution, setPreviousReconstitution] = useState<number | null>(null);

  const moistureGaugeRef = useRef<HTMLCanvasElement>(null);
  const reconstitutionGaugeRef = useRef<HTMLCanvasElement>(null);
  const moistureConfidenceRef = useRef<HTMLCanvasElement>(null);
  const reconstitutionConfidenceRef = useRef<HTMLCanvasElement>(null);

  const drawGauges = useCallback(() => {
    if (!latestResult) return;

    const moistureConfig: GaugeConfig = {
      ...DEFAULT_GAUGE_CONFIG.moisture,
      threshold: latestResult.moisture_content.threshold,
      warningThreshold: latestResult.moisture_content.threshold * 0.8,
      max: Math.max(latestResult.moisture_content.threshold * 1.5, 5),
    };

    const reconstitutionConfig: GaugeConfig = {
      ...DEFAULT_GAUGE_CONFIG.reconstitution,
      threshold: latestResult.reconstitution_time.threshold,
      warningThreshold: latestResult.reconstitution_time.threshold * 0.8,
      max: Math.max(latestResult.reconstitution_time.threshold * 1.5, 200),
    };

    const moistureCtx = moistureGaugeRef.current?.getContext('2d');
    if (moistureCtx) {
      drawGauge(moistureCtx, latestResult.moisture_content.predicted, moistureConfig);
    }

    const reconstitutionCtx = reconstitutionGaugeRef.current?.getContext('2d');
    if (reconstitutionCtx) {
      drawGauge(reconstitutionCtx, latestResult.reconstitution_time.predicted, reconstitutionConfig);
    }

    const moistureConfCtx = moistureConfidenceRef.current?.getContext('2d');
    if (moistureConfCtx) {
      drawConfidenceBar(
        moistureConfCtx,
        latestResult.moisture_content.confidence,
        moistureConfidenceRef.current.width,
        moistureConfidenceRef.current.height
      );
    }

    const reconstitutionConfCtx = reconstitutionConfidenceRef.current?.getContext('2d');
    if (reconstitutionConfCtx) {
      drawConfidenceBar(
        reconstitutionConfCtx,
        latestResult.reconstitution_time.confidence,
        reconstitutionConfidenceRef.current.width,
        reconstitutionConfidenceRef.current.height
      );
    }
  }, [latestResult]);

  useEffect(() => {
    fetchHistory();
  }, [deviceId]);

  useEffect(() => {
    if (latestResult) {
      const timer = requestAnimationFrame(() => {
        drawGauges();
      });
      return () => cancelAnimationFrame(timer);
    }
  }, [latestResult, drawGauges]);

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

      if (latestResult) {
        setPreviousMoisture(latestResult.moisture_content.predicted);
        setPreviousReconstitution(latestResult.reconstitution_time.predicted);
      }

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

  const renderTrendIcon = (
    current: number,
    previous: number | null,
    threshold: number
  ) => {
    const trend = getTrendIcon(current, previous, threshold);
    if (trend === 'up') {
      return <span className="text-red-400">↑</span>;
    } else if (trend === 'down') {
      return <span className="text-green-400">↓</span>;
    }
    return <span className="text-slate-400">→</span>;
  };

  const predictionResult: PredictionResult | null = latestResult
    ? {
        device_id: deviceId,
        timestamp: latestResult.timestamp || new Date().toISOString(),
        moisture_content: latestResult.moisture_content.predicted,
        moisture_confidence: latestResult.moisture_content.confidence,
        reconstitution_time: latestResult.reconstitution_time.predicted,
        reconstitution_confidence: latestResult.reconstitution_time.confidence,
        drying_rate: latestResult.drying_rate,
        is_qualified: latestResult.is_qualified,
        moisture_threshold: latestResult.moisture_content.threshold,
        reconstitution_threshold: latestResult.reconstitution_time.threshold,
        drift_detected: false,
        adaptation_level: 1.0,
        model_version: 'PLS-v2.0',
      }
    : null;

  const message = predictionResult ? formatPredictionMessage(predictionResult) : null;
  const qualityScore = predictionResult ? calculateQualityScore(predictionResult) : null;

  const formattedHistory: PredictionHistoryItem[] = history.map((item) => ({
    timestamp: item.timestamp || new Date().toISOString(),
    moisture_content: item.moisture_content.predicted,
    reconstitution_time: item.reconstitution_time.predicted,
    is_qualified: item.is_qualified,
  }));

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
                基于PLS回归模型 · 自适应更新
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
            {message && qualityScore !== null && (
              <div className={`p-3 rounded-lg border ${getStatusBadgeStyle(message.severity)}`}>
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-semibold">{message.title}</div>
                    <div className="text-sm opacity-80">{message.content}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs text-slate-400">质量评分</div>
                    <div className="text-2xl font-bold font-mono">{qualityScore}</div>
                  </div>
                </div>
              </div>
            )}

            <div className="grid grid-cols-3 gap-4">
              <div className="text-center p-3 bg-slate-800/30 rounded-lg border border-slate-700">
                <canvas
                  ref={moistureGaugeRef}
                  width={200}
                  height={160}
                  className="w-full"
                />
                <div className="flex items-center justify-center gap-1 text-xs text-slate-400">
                  <Droplets className="w-3 h-3" />
                  水分含量
                  {renderTrendIcon(
                    latestResult.moisture_content.predicted,
                    previousMoisture,
                    latestResult.moisture_content.threshold
                  )}
                </div>
              </div>

              <div className="text-center p-3 bg-slate-800/30 rounded-lg border border-slate-700">
                <canvas
                  ref={reconstitutionGaugeRef}
                  width={200}
                  height={160}
                  className="w-full"
                />
                <div className="flex items-center justify-center gap-1 text-xs text-slate-400">
                  <Clock className="w-3 h-3" />
                  复溶时间
                  {renderTrendIcon(
                    latestResult.reconstitution_time.predicted,
                    previousReconstitution,
                    latestResult.reconstitution_time.threshold
                  )}
                </div>
              </div>

              <div className="flex flex-col items-center justify-center p-4 bg-slate-800/50 rounded-lg border border-slate-700">
                <div className="text-xs text-slate-400 mb-2">干燥速率</div>
                <div className="text-2xl font-mono font-bold text-orange-400">
                  {latestResult.drying_rate.toFixed(4)}
                </div>
                <div className="text-xs text-slate-500">g/h·m²</div>
                <div
                  className={`mt-2 px-3 py-1 rounded-full text-sm font-medium border ${
                    latestResult.is_qualified
                      ? 'bg-green-500/20 text-green-400 border-green-500/30'
                      : 'bg-red-500/20 text-red-400 border-red-500/30'
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
                <canvas
                  ref={moistureConfidenceRef}
                  width={300}
                  height={35}
                  className="w-full"
                />
              </div>

              <div className="p-3 bg-slate-800/50 rounded-lg border border-slate-700">
                <div className="flex items-center gap-2 text-slate-400 text-sm mb-2">
                  <Clock className="w-4 h-4" />
                  复溶时间置信度
                </div>
                <canvas
                  ref={reconstitutionConfidenceRef}
                  width={300}
                  height={35}
                  className="w-full"
                />
              </div>
            </div>

            {formattedHistory.length > 0 && (
              <div className="pt-4 border-t border-slate-700">
                <h4 className="text-sm font-medium text-slate-300 mb-3">
                  历史预测
                </h4>
                <div className="space-y-2">
                  {formattedHistory.map((item, index) => {
                    const formatted = formatHistoryItem(item);
                    return (
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
                          <span className="text-slate-400">{formatted.time}</span>
                        </div>
                        <div className="flex gap-4 text-xs">
                          <span className="text-cyan-400 font-mono">
                            {formatted.moisture}
                          </span>
                          <span className="text-green-400 font-mono">
                            {formatted.reconstitution}
                          </span>
                        </div>
                      </div>
                    );
                  })}
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
                      max="300"
                      step="1"
                      value={reconstitutionMax}
                      onChange={(e) =>
                        setReconstitutionMax(parseFloat(e.target.value))
                      }
                      className="flex-1 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-100 font-mono focus:outline-none focus:border-purple-500"
                    />
                    <span className="text-slate-400 self-center">s</span>
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
