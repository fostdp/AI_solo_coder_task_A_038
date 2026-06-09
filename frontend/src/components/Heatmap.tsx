import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import type { RealtimeData } from '@/types';
import {
  TemperatureData,
  SensorPosition,
  HeatmapConfig,
  CacheKey,
  DEFAULT_CONFIG,
  calculateSensorPositions,
  hasSignificantChange,
  cacheKeyEquals,
  drawStaticLayer,
  drawDynamicLayer,
  findHoveredSensor,
  createOffscreenCanvas,
} from './shelf_thermal';

interface HeatmapProps {
  data: RealtimeData | null;
  tempDiffThreshold?: number;
  width?: number;
  height?: number;
}

const Heatmap = ({
  data,
  tempDiffThreshold = 1.0,
  width = 400,
  height = 200,
}: HeatmapProps) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const offscreenCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hoveredSensor, setHoveredSensor] = useState<SensorPosition | null>(null);
  const [abnormalRegions, setAbnormalRegions] = useState<{ x: number; y: number; w: number; h: number }[]>([]);

  const lastDataRef = useRef<TemperatureData | null>(null);
  const lastCacheKeyRef = useRef<CacheKey | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const pendingRedrawRef = useRef(false);

  const config: HeatmapConfig = useMemo(
    () => ({
      ...DEFAULT_CONFIG,
      width,
      height,
      tempDiffThreshold,
    }),
    [width, height, tempDiffThreshold]
  );

  const sensorPositions = useMemo(() => {
    return calculateSensorPositions(data?.temperatures || [0, 0, 0, 0, 0, 0, 0, 0], config);
  }, [data, config]);

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const temps = data?.temperatures || [];
    const minTemp = temps.length > 0 ? Math.min(...temps) : -50;
    const maxTemp = temps.length > 0 ? Math.max(...temps) : -40;

    const currentCacheKey: CacheKey = { width, height, minTemp, maxTemp };

    const needsStaticRedraw = !cacheKeyEquals(lastCacheKeyRef.current, currentCacheKey);

    if (!offscreenCanvasRef.current ||
        offscreenCanvasRef.current.width !== width ||
        offscreenCanvasRef.current.height !== height) {
      offscreenCanvasRef.current = createOffscreenCanvas(width, height);
      lastCacheKeyRef.current = null;
    }

    const offscreenCtx = offscreenCanvasRef.current?.getContext('2d');
    if (offscreenCtx && needsStaticRedraw) {
      drawStaticLayer(offscreenCtx, currentCacheKey, config);
      lastCacheKeyRef.current = currentCacheKey;
    }

    ctx.clearRect(0, 0, width, height);

    if (offscreenCtx && offscreenCanvasRef.current) {
      ctx.drawImage(offscreenCanvasRef.current, 0, 0);
    }

    const tempData: TemperatureData | null = data
      ? {
          temperatures: data.temperatures,
          temperature_diff: data.temperature_diff,
          avg_temperature: data.avg_temperature,
          timestamp: data.timestamp,
        }
      : null;

    const { abnormalRegions: regions } = drawDynamicLayer(
      ctx,
      tempData,
      sensorPositions.positions,
      sensorPositions.sensorSize,
      config
    );

    setAbnormalRegions(regions);

    lastDataRef.current = tempData;
    pendingRedrawRef.current = false;
  }, [data, width, height, config, sensorPositions]);

  const scheduleRedraw = useCallback(() => {
    if (pendingRedrawRef.current) return;

    pendingRedrawRef.current = true;

    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
    }

    animationFrameRef.current = requestAnimationFrame(() => {
      render();
    });
  }, [render]);

  useEffect(() => {
    const tempData: TemperatureData | null = data
      ? {
          temperatures: data.temperatures,
          temperature_diff: data.temperature_diff,
          avg_temperature: data.avg_temperature,
          timestamp: data.timestamp,
        }
      : null;

    if (!hasSignificantChange(lastDataRef.current, tempData)) {
      return;
    }

    scheduleRedraw();
  }, [data, scheduleRedraw]);

  useEffect(() => {
    scheduleRedraw();
  }, [width, height, scheduleRedraw]);

  useEffect(() => {
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      const hovered = findHoveredSensor(
        x,
        y,
        sensorPositions.positions,
        sensorPositions.sensorSize
      );

      setHoveredSensor(hovered);
    },
    [sensorPositions]
  );

  return (
    <div className="relative inline-block">
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="rounded-lg border border-slate-700 cursor-crosshair"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredSensor(null)}
      />
      {hoveredSensor && (
        <div
          className="absolute z-10 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg shadow-xl pointer-events-none"
          style={{
            left: hoveredSensor.x + 60,
            top: hoveredSensor.y - 10,
          }}
        >
          <div className="text-xs text-slate-400">传感器 {hoveredSensor.index + 1}</div>
          <div className="text-lg font-bold font-mono text-cyan-400">
            {hoveredSensor.temp.toFixed(2)}℃
          </div>
        </div>
      )}
    </div>
  );
};

export default Heatmap;
