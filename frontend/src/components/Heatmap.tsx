import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import type { RealtimeData } from '@/types';

interface HeatmapProps {
  data: RealtimeData | null;
  tempDiffThreshold?: number;
  width?: number;
  height?: number;
}

interface SensorPosition {
  x: number;
  y: number;
  temp: number;
  index: number;
}

interface CacheKey {
  width: number;
  height: number;
  minTemp: number;
  maxTemp: number;
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
  
  const lastDataRef = useRef<RealtimeData | null>(null);
  const lastCacheKeyRef = useRef<CacheKey | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const pendingRedrawRef = useRef(false);

  const getTemperatureColor = useCallback((temp: number, minTemp: number, maxTemp: number) => {
    if (maxTemp === minTemp) {
      return 'rgb(100, 200, 255)';
    }

    const normalized = (temp - minTemp) / (maxTemp - minTemp);
    
    const colors = [
      { pos: 0, r: 0, g: 100, b: 200 },
      { pos: 0.25, r: 0, g: 180, b: 220 },
      { pos: 0.5, r: 100, g: 220, b: 180 },
      { pos: 0.75, r: 255, g: 200, b: 100 },
      { pos: 1, r: 255, g: 80, b: 80 },
    ];

    for (let i = 0; i < colors.length - 1; i++) {
      if (normalized <= colors[i + 1].pos) {
        const t = (normalized - colors[i].pos) / (colors[i + 1].pos - colors[i].pos);
        const r = Math.round(colors[i].r + t * (colors[i + 1].r - colors[i].r));
        const g = Math.round(colors[i].g + t * (colors[i + 1].g - colors[i].g));
        const b = Math.round(colors[i].b + t * (colors[i + 1].b - colors[i].b));
        return `rgb(${r}, ${g}, ${b})`;
      }
    }
    return 'rgb(255, 80, 80)';
  }, []);

  const sensorPositions = useMemo(() => {
    const positions: SensorPosition[] = [];
    const cols = 4;
    const rows = 2;
    const padding = 40;
    const sensorSize = Math.min((width - padding * 2) / cols, (height - padding * 2) / rows) * 0.8;
    const spacingX = (width - padding * 2 - sensorSize * cols) / (cols - 1);
    const spacingY = (height - padding * 2 - sensorSize * rows) / (rows - 1);

    for (let i = 0; i < 8; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      positions.push({
        x: padding + col * (sensorSize + spacingX),
        y: padding + row * (sensorSize + spacingY),
        temp: data?.temperatures[i] || 0,
        index: i,
      });
    }
    return { positions, sensorSize };
  }, [data, width, height]);

  const detectAbnormalRegions = useCallback((positions: SensorPosition[]) => {
    if (!data) return [];

    const regions: { x: number; y: number; w: number; h: number }[] = [];
    const temps = data.temperatures;
    const avgTemp = temps.reduce((a, b) => a + b, 0) / temps.length;

    for (let i = 0; i < temps.length; i++) {
      if (Math.abs(temps[i] - avgTemp) > tempDiffThreshold) {
        const pos = positions[i];
        regions.push({
          x: pos.x - 5,
          y: pos.y - 5,
          w: 50,
          h: 50,
        });
      }
    }
    return regions;
  }, [data, tempDiffThreshold]);

  const hasSignificantChange = useCallback((newData: RealtimeData | null) => {
    if (!lastDataRef.current && !newData) return false;
    if (!lastDataRef.current || !newData) return true;
    
    const oldTemps = lastDataRef.current.temperatures;
    const newTemps = newData.temperatures;
    
    for (let i = 0; i < 8; i++) {
      if (Math.abs(oldTemps[i] - newTemps[i]) > 0.05) {
        return true;
      }
    }
    
    if (Math.abs(lastDataRef.current.temperature_diff - newData.temperature_diff) > 0.05) {
      return true;
    }
    
    return false;
  }, []);

  const drawStaticLayer = useCallback((ctx: CanvasRenderingContext2D, cacheKey: CacheKey) => {
    const { width, height, minTemp, maxTemp } = cacheKey;

    ctx.clearRect(0, 0, width, height);

    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, '#0F172A');
    gradient.addColorStop(1, '#1E293B');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 1;
    for (let i = 0; i <= width; i += 20) {
      ctx.beginPath();
      ctx.moveTo(i, 0);
      ctx.lineTo(i, height);
      ctx.stroke();
    }
    for (let i = 0; i <= height; i += 20) {
      ctx.beginPath();
      ctx.moveTo(0, i);
      ctx.lineTo(width, i);
      ctx.stroke();
    }

    const legendWidth = 150;
    const legendHeight = 15;
    const legendX = width - legendWidth - 15;
    const legendY = height - 35;

    const legendGradient = ctx.createLinearGradient(legendX, legendY, legendX + legendWidth, legendY);
    legendGradient.addColorStop(0, 'rgb(0, 100, 200)');
    legendGradient.addColorStop(0.5, 'rgb(100, 220, 180)');
    legendGradient.addColorStop(1, 'rgb(255, 80, 80)');
    ctx.fillStyle = legendGradient;
    ctx.fillRect(legendX, legendY, legendWidth, legendHeight);

    ctx.fillStyle = '#94A3B8';
    ctx.font = '10px Inter';
    ctx.textAlign = 'left';
    ctx.fillText(`${minTemp.toFixed(1)}℃`, legendX, legendY + legendHeight + 12);
    ctx.textAlign = 'right';
    ctx.fillText(`${maxTemp.toFixed(1)}℃`, legendX + legendWidth, legendY + legendHeight + 12);
  }, []);

  const drawDynamicLayer = useCallback((ctx: CanvasRenderingContext2D) => {
    if (!data) {
      ctx.fillStyle = '#64748B';
      ctx.font = '14px Inter';
      ctx.textAlign = 'center';
      ctx.fillText('等待数据...', width / 2, height / 2);
      return;
    }

    const { positions, sensorSize } = sensorPositions;
    const temps = data.temperatures;
    const minTemp = Math.min(...temps);
    const maxTemp = Math.max(...temps);

    positions.forEach((pos, i) => {
      const color = getTemperatureColor(pos.temp, minTemp, maxTemp);
      
      ctx.shadowColor = color;
      ctx.shadowBlur = 10;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.roundRect(pos.x, pos.y, sensorSize, sensorSize, 4);
      ctx.fill();
      ctx.shadowBlur = 0;

      ctx.fillStyle = '#FFFFFF';
      ctx.font = 'bold 12px JetBrains Mono';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(pos.temp.toFixed(1), pos.x + sensorSize / 2, pos.y + sensorSize / 2);

      ctx.fillStyle = '#94A3B8';
      ctx.font = '10px Inter';
      ctx.fillText(`S${i + 1}`, pos.x + sensorSize / 2, pos.y + sensorSize + 12);
    });

    const abnormalRegions = detectAbnormalRegions(positions);
    setAbnormalRegions(abnormalRegions);

    abnormalRegions.forEach(region => {
      ctx.strokeStyle = '#EF4444';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 3]);
      ctx.strokeRect(region.x, region.y, region.w, region.h);
      ctx.setLineDash([]);

      ctx.fillStyle = 'rgba(239, 68, 68, 0.1)';
      ctx.fillRect(region.x, region.y, region.w, region.h);
    });

    ctx.textAlign = 'left';
    ctx.fillStyle = '#06B6D4';
    ctx.font = '11px Inter';
    ctx.fillText(`温差: ${data.temperature_diff.toFixed(2)}℃`, 15, height - 20);
    
    if (data.temperature_diff > tempDiffThreshold) {
      ctx.fillStyle = '#EF4444';
      ctx.fillText('⚠ 不均匀', 120, height - 20);
    } else {
      ctx.fillStyle = '#10B981';
      ctx.fillText('✓ 均匀', 120, height - 20);
    }
  }, [data, sensorPositions, getTemperatureColor, detectAbnormalRegions, width, height, tempDiffThreshold]);

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const temps = data?.temperatures || [];
    const minTemp = temps.length > 0 ? Math.min(...temps) : -50;
    const maxTemp = temps.length > 0 ? Math.max(...temps) : -40;

    const currentCacheKey: CacheKey = { width, height, minTemp, maxTemp };

    let needsStaticRedraw = false;
    if (!lastCacheKeyRef.current ||
        lastCacheKeyRef.current.width !== currentCacheKey.width ||
        lastCacheKeyRef.current.height !== currentCacheKey.height ||
        lastCacheKeyRef.current.minTemp !== currentCacheKey.minTemp ||
        lastCacheKeyRef.current.maxTemp !== currentCacheKey.maxTemp) {
      needsStaticRedraw = true;
    }

    if (!offscreenCanvasRef.current ||
        offscreenCanvasRef.current.width !== width ||
        offscreenCanvasRef.current.height !== height) {
      offscreenCanvasRef.current = document.createElement('canvas');
      offscreenCanvasRef.current.width = width;
      offscreenCanvasRef.current.height = height;
      needsStaticRedraw = true;
    }

    const offscreenCtx = offscreenCanvasRef.current.getContext('2d');
    if (offscreenCtx && needsStaticRedraw) {
      drawStaticLayer(offscreenCtx, currentCacheKey);
      lastCacheKeyRef.current = currentCacheKey;
    }

    ctx.clearRect(0, 0, width, height);
    
    if (offscreenCtx) {
      ctx.drawImage(offscreenCanvasRef.current, 0, 0);
    }

    drawDynamicLayer(ctx);

    lastDataRef.current = data;
    pendingRedrawRef.current = false;
  }, [data, width, height, drawStaticLayer, drawDynamicLayer]);

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
    if (!hasSignificantChange(data)) {
      return;
    }

    scheduleRedraw();
  }, [data, hasSignificantChange, scheduleRedraw]);

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

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const { positions, sensorSize } = sensorPositions;
    const hovered = positions.find(pos =>
      x >= pos.x && x <= pos.x + sensorSize &&
      y >= pos.y && y <= pos.y + sensorSize
    );

    setHoveredSensor(hovered || null);
  }, [sensorPositions]);

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
