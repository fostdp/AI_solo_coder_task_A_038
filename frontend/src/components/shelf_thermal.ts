/**
 * 搁板热力图模块 - shelf_thermal.js
 * 独立封装温度热力图渲染和交互逻辑
 */

export interface TemperatureData {
  temperatures: number[];
  temperature_diff: number;
  avg_temperature: number;
  timestamp?: string;
}

export interface SensorPosition {
  x: number;
  y: number;
  temp: number;
  index: number;
}

export interface HeatmapConfig {
  width: number;
  height: number;
  padding: number;
  cols: number;
  rows: number;
  tempDiffThreshold: number;
  significantChange: number;
}

export interface CacheKey {
  width: number;
  height: number;
  minTemp: number;
  maxTemp: number;
}

export const DEFAULT_CONFIG: HeatmapConfig = {
  width: 400,
  height: 200,
  padding: 40,
  cols: 4,
  rows: 2,
  tempDiffThreshold: 1.0,
  significantChange: 0.05,
};

export const COLOR_STOPS = [
  { pos: 0, r: 0, g: 100, b: 200 },
  { pos: 0.25, r: 0, g: 180, b: 220 },
  { pos: 0.5, r: 100, g: 220, b: 180 },
  { pos: 0.75, r: 255, g: 200, b: 100 },
  { pos: 1, r: 255, g: 80, b: 80 },
];

export function getTemperatureColor(
  temp: number,
  minTemp: number,
  maxTemp: number,
  colorStops = COLOR_STOPS
): string {
  if (maxTemp === minTemp) {
    return 'rgb(100, 200, 255)';
  }

  const normalized = (temp - minTemp) / (maxTemp - minTemp);

  for (let i = 0; i < colorStops.length - 1; i++) {
    if (normalized <= colorStops[i + 1].pos) {
      const t =
        (normalized - colorStops[i].pos) /
        (colorStops[i + 1].pos - colorStops[i].pos);
      const r = Math.round(
        colorStops[i].r + t * (colorStops[i + 1].r - colorStops[i].r)
      );
      const g = Math.round(
        colorStops[i].g + t * (colorStops[i + 1].g - colorStops[i].g)
      );
      const b = Math.round(
        colorStops[i].b + t * (colorStops[i + 1].b - colorStops[i].b)
      );
      return `rgb(${r}, ${g}, ${b})`;
    }
  }
  return 'rgb(255, 80, 80)';
}

export function calculateSensorPositions(
  temperatures: number[],
  config: HeatmapConfig
): { positions: SensorPosition[]; sensorSize: number } {
  const { width, height, padding, cols, rows } = config;
  const positions: SensorPosition[] = [];

  const sensorSize =
    Math.min((width - padding * 2) / cols, (height - padding * 2) / rows) * 0.8;
  const spacingX = (width - padding * 2 - sensorSize * cols) / (cols - 1);
  const spacingY = (height - padding * 2 - sensorSize * rows) / (rows - 1);

  for (let i = 0; i < 8; i++) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    positions.push({
      x: padding + col * (sensorSize + spacingX),
      y: padding + row * (sensorSize + spacingY),
      temp: temperatures[i] || 0,
      index: i,
    });
  }

  return { positions, sensorSize };
}

export function detectAbnormalRegions(
  positions: SensorPosition[],
  temperatures: number[],
  threshold: number
): { x: number; y: number; w: number; h: number }[] {
  const regions: { x: number; y: number; w: number; h: number }[] = [];
  const avgTemp = temperatures.reduce((a, b) => a + b, 0) / temperatures.length;

  for (let i = 0; i < temperatures.length; i++) {
    if (Math.abs(temperatures[i] - avgTemp) > threshold) {
      const pos = positions[i];
      if (pos) {
        regions.push({
          x: pos.x - 5,
          y: pos.y - 5,
          w: 50,
          h: 50,
        });
      }
    }
  }
  return regions;
}

export function hasSignificantChange(
  oldData: TemperatureData | null,
  newData: TemperatureData | null,
  threshold: number = DEFAULT_CONFIG.significantChange
): boolean {
  if (!oldData && !newData) return false;
  if (!oldData || !newData) return true;

  const oldTemps = oldData.temperatures;
  const newTemps = newData.temperatures;

  for (let i = 0; i < 8; i++) {
    if (Math.abs(oldTemps[i] - newTemps[i]) > threshold) {
      return true;
    }
  }

  if (Math.abs(oldData.temperature_diff - newData.temperature_diff) > threshold) {
    return true;
  }

  return false;
}

export function cacheKeyEquals(a: CacheKey | null, b: CacheKey | null): boolean {
  if (!a || !b) return false;
  return (
    a.width === b.width &&
    a.height === b.height &&
    a.minTemp === b.minTemp &&
    a.maxTemp === b.maxTemp
  );
}

export function drawStaticLayer(
  ctx: CanvasRenderingContext2D,
  cacheKey: CacheKey,
  config: HeatmapConfig = DEFAULT_CONFIG
): void {
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

  const legendGradient = ctx.createLinearGradient(
    legendX,
    legendY,
    legendX + legendWidth,
    legendY
  );
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
  ctx.fillText(
    `${maxTemp.toFixed(1)}℃`,
    legendX + legendWidth,
    legendY + legendHeight + 12
  );
}

export function drawDynamicLayer(
  ctx: CanvasRenderingContext2D,
  data: TemperatureData | null,
  positions: SensorPosition[],
  sensorSize: number,
  config: HeatmapConfig = DEFAULT_CONFIG
): { abnormalRegions: { x: number; y: number; w: number; h: number }[] } {
  const { width, height, tempDiffThreshold } = config;

  if (!data) {
    ctx.fillStyle = '#64748B';
    ctx.font = '14px Inter';
    ctx.textAlign = 'center';
    ctx.fillText('等待数据...', width / 2, height / 2);
    return { abnormalRegions: [] };
  }

  const { temperatures } = data;
  const minTemp = Math.min(...temperatures);
  const maxTemp = Math.max(...temperatures);

  positions.forEach((pos, i) => {
    const color = getTemperatureColor(pos.temp, minTemp, maxTemp);

    ctx.shadowColor = color;
    ctx.shadowBlur = 10;
    ctx.fillStyle = color;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(pos.x, pos.y, sensorSize, sensorSize, 4);
    } else {
      ctx.rect(pos.x, pos.y, sensorSize, sensorSize);
    }
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 12px JetBrains Mono';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(
      pos.temp.toFixed(1),
      pos.x + sensorSize / 2,
      pos.y + sensorSize / 2
    );

    ctx.fillStyle = '#94A3B8';
    ctx.font = '10px Inter';
    ctx.fillText(`S${i + 1}`, pos.x + sensorSize / 2, pos.y + sensorSize + 12);
  });

  const abnormalRegions = detectAbnormalRegions(
    positions,
    temperatures,
    tempDiffThreshold
  );

  abnormalRegions.forEach((region) => {
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

  return { abnormalRegions };
}

export function findHoveredSensor(
  x: number,
  y: number,
  positions: SensorPosition[],
  sensorSize: number
): SensorPosition | null {
  return (
    positions.find(
      (pos) =>
        x >= pos.x &&
        x <= pos.x + sensorSize &&
        y >= pos.y &&
        y <= pos.y + sensorSize
    ) || null
  );
}

export function createOffscreenCanvas(
  width: number,
  height: number
): HTMLCanvasElement | null {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}
