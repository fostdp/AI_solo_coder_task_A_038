/**
 * 质量预测仪表盘模块 - quality_dashboard.js
 * 独立封装质量预测数据展示和交互逻辑
 */

export interface PredictionResult {
  device_id: number;
  timestamp: string;
  moisture_content: number;
  moisture_confidence: number;
  reconstitution_time: number;
  reconstitution_confidence: number;
  drying_rate: number;
  is_qualified: boolean;
  moisture_threshold: number;
  reconstitution_threshold: number;
  formula_id?: string;
  batch_id?: string;
  drift_detected: boolean;
  adaptation_level: number;
  model_version: string;
}

export interface GaugeConfig {
  width: number;
  height: number;
  arcWidth: number;
  label: string;
  unit: string;
  min: number;
  max: number;
  threshold: number;
  warningThreshold: number;
  colors: {
    normal: string;
    warning: string;
    danger: string;
    background: string;
    text: string;
    subtext: string;
  };
}

export interface PredictionHistoryItem {
  timestamp: string;
  moisture_content: number;
  reconstitution_time: number;
  is_qualified: boolean;
}

export const DEFAULT_GAUGE_CONFIG: Record<string, GaugeConfig> = {
  moisture: {
    width: 200,
    height: 160,
    arcWidth: 16,
    label: '水分含量',
    unit: '%',
    min: 0,
    max: 5,
    threshold: 3.0,
    warningThreshold: 2.5,
    colors: {
      normal: '#10B981',
      warning: '#F59E0B',
      danger: '#EF4444',
      background: '#334155',
      text: '#F1F5F9',
      subtext: '#94A3B8',
    },
  },
  reconstitution: {
    width: 200,
    height: 160,
    arcWidth: 16,
    label: '复溶时间',
    unit: 's',
    min: 0,
    max: 200,
    threshold: 120,
    warningThreshold: 100,
    colors: {
      normal: '#10B981',
      warning: '#F59E0B',
      danger: '#EF4444',
      background: '#334155',
      text: '#F1F5F9',
      subtext: '#94A3B8',
    },
  },
};

export function getValueStatus(
  value: number,
  threshold: number,
  warningThreshold: number
): 'normal' | 'warning' | 'danger' {
  if (value >= threshold) return 'danger';
  if (value >= warningThreshold) return 'warning';
  return 'normal';
}

export function getStrokeColor(
  status: 'normal' | 'warning' | 'danger',
  config: GaugeConfig
): string {
  switch (status) {
    case 'normal':
      return config.colors.normal;
    case 'warning':
      return config.colors.warning;
    case 'danger':
      return config.colors.danger;
  }
}

export function calculateGaugePath(
  cx: number,
  cy: number,
  radius: number,
  startAngle: number,
  endAngle: number
): string {
  const startRad = (startAngle * Math.PI) / 180;
  const endRad = (endAngle * Math.PI) / 180;

  const x1 = cx + radius * Math.cos(startRad);
  const y1 = cy + radius * Math.sin(startRad);
  const x2 = cx + radius * Math.cos(endRad);
  const y2 = cy + radius * Math.sin(endRad);

  const largeArc = endAngle - startAngle > 180 ? 1 : 0;

  return `M ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`;
}

export function drawGauge(
  ctx: CanvasRenderingContext2D,
  value: number,
  config: GaugeConfig
): void {
  const { width, height, arcWidth, unit, min, max, threshold, warningThreshold, colors } =
    config;

  const cx = width / 2;
  const cy = height / 2 + 10;
  const radius = Math.min(width, height) / 2 - arcWidth - 10;

  ctx.clearRect(0, 0, width, height);

  const startAngle = -135;
  const endAngle = 135;
  const angleRange = endAngle - startAngle;

  const normalizedValue = Math.max(min, Math.min(max, value));
  const valueAngle =
    startAngle + ((normalizedValue - min) / (max - min)) * angleRange;

  const status = getValueStatus(value, threshold, warningThreshold);
  const strokeColor = getStrokeColor(status, config);

  ctx.beginPath();
  ctx.strokeStyle = colors.background;
  ctx.lineWidth = arcWidth;
  ctx.lineCap = 'round';
  ctx.arc(cx, cy, radius, (startAngle * Math.PI) / 180, (endAngle * Math.PI) / 180);
  ctx.stroke();

  const warningStartAngle =
    startAngle + ((warningThreshold - min) / (max - min)) * angleRange;
  const dangerStartAngle =
    startAngle + ((threshold - min) / (max - min)) * angleRange;

  if (warningStartAngle < endAngle) {
    ctx.beginPath();
    ctx.strokeStyle = colors.warning;
    ctx.lineWidth = arcWidth;
    ctx.lineCap = 'butt';
    ctx.arc(
      cx,
      cy,
      radius,
      (warningStartAngle * Math.PI) / 180,
      Math.min(dangerStartAngle, endAngle) * (Math.PI / 180)
    );
    ctx.stroke();
  }

  if (dangerStartAngle < endAngle) {
    ctx.beginPath();
    ctx.strokeStyle = colors.danger;
    ctx.lineWidth = arcWidth;
    ctx.lineCap = 'round';
    ctx.arc(
      cx,
      cy,
      radius,
      (dangerStartAngle * Math.PI) / 180,
      (endAngle * Math.PI) / 180
    );
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = arcWidth + 2;
  ctx.lineCap = 'round';
  ctx.shadowColor = strokeColor;
  ctx.shadowBlur = 15;
  ctx.arc(
    cx,
    cy,
    radius,
    (startAngle * Math.PI) / 180,
    (valueAngle * Math.PI) / 180
  );
  ctx.stroke();
  ctx.shadowBlur = 0;

  ctx.fillStyle = colors.text;
  ctx.font = 'bold 32px JetBrains Mono';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(value.toFixed(2), cx, cy - 5);

  ctx.fillStyle = colors.subtext;
  ctx.font = '14px Inter';
  ctx.fillText(unit, cx, cy + 25);

  ctx.fillStyle = colors.subtext;
  ctx.font = '12px Inter';
  ctx.fillText(config.label, cx, height - 15);

  ctx.beginPath();
  ctx.arc(cx + radius * Math.cos((valueAngle * Math.PI) / 180), 
          cy + radius * Math.sin((valueAngle * Math.PI) / 180), 
          6, 0, Math.PI * 2);
  ctx.fillStyle = strokeColor;
  ctx.shadowColor = strokeColor;
  ctx.shadowBlur = 10;
  ctx.fill();
  ctx.shadowBlur = 0;
}

export function drawConfidenceBar(
  ctx: CanvasRenderingContext2D,
  confidence: number,
  width: number,
  height: number
): void {
  ctx.clearRect(0, 0, width, height);

  const barHeight = 8;
  const barY = height / 2 - barHeight / 2;

  ctx.fillStyle = '#334155';
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(0, barY, width, barHeight, 4);
  } else {
    ctx.rect(0, barY, width, barHeight);
  }
  ctx.fill();

  const fillWidth = width * confidence;
  const color =
    confidence >= 0.85 ? '#10B981' : confidence >= 0.7 ? '#F59E0B' : '#EF4444';

  ctx.fillStyle = color;
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(0, barY, fillWidth, barHeight, 4);
  } else {
    ctx.rect(0, barY, fillWidth, barHeight);
  }
  ctx.fill();

  ctx.fillStyle = '#94A3B8';
  ctx.font = '11px Inter';
  ctx.textAlign = 'left';
  ctx.fillText('置信度', 0, barY - 5);

  ctx.fillStyle = color;
  ctx.font = 'bold 11px JetBrains Mono';
  ctx.textAlign = 'right';
  ctx.fillText(`${(confidence * 100).toFixed(1)}%`, width, barY - 5);
}

export function formatPredictionMessage(
  result: PredictionResult
): { title: string; content: string; severity: 'success' | 'warning' | 'danger' } {
  const issues: string[] = [];

  if (result.moisture_content > result.moisture_threshold) {
    issues.push(
      `水分含量 ${result.moisture_content.toFixed(2)}% > ${result.moisture_threshold}%`
    );
  }

  if (result.reconstitution_time > result.reconstitution_threshold) {
    issues.push(
      `复溶时间 ${result.reconstitution_time.toFixed(0)}s > ${result.reconstitution_threshold}s`
    );
  }

  if (result.drift_detected) {
    issues.push('检测到概念漂移');
  }

  if (result.is_qualified) {
    return {
      title: '质量预测合格',
      content: `水分: ${result.moisture_content.toFixed(2)}% | 复溶: ${result.reconstitution_time.toFixed(0)}s`,
      severity: 'success',
    };
  } else {
    return {
      title: '质量预测不合格',
      content: issues.join('; '),
      severity: 'danger',
    };
  }
}

export function getTrendIcon(
  current: number,
  previous: number | null,
  threshold: number
): 'up' | 'down' | 'stable' {
  if (previous === null) return 'stable';

  const diff = current - previous;
  if (Math.abs(diff) < threshold * 0.05) return 'stable';
  return diff > 0 ? 'up' : 'down';
}

export function calculateQualityScore(result: PredictionResult): number {
  const moistureScore = Math.max(
    0,
    100 - (result.moisture_content / result.moisture_threshold) * 50
  );
  const reconstitutionScore = Math.max(
    0,
    100 - (result.reconstitution_time / result.reconstitution_threshold) * 50
  );
  const confidenceScore = result.moisture_confidence * result.reconstitution_confidence * 100;

  return Math.round((moistureScore + reconstitutionScore + confidenceScore) / 3);
}

export function formatHistoryItem(
  item: PredictionHistoryItem
): { time: string; moisture: string; reconstitution: string; status: string } {
  const date = new Date(item.timestamp);
  return {
    time: date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    moisture: item.moisture_content.toFixed(2) + '%',
    reconstitution: item.reconstitution_time.toFixed(0) + 's',
    status: item.is_qualified ? '合格' : '不合格',
  };
}

export function getStatusBadgeStyle(
  status: 'success' | 'warning' | 'danger'
): string {
  switch (status) {
    case 'success':
      return 'bg-green-500/20 text-green-400 border-green-500/30';
    case 'warning':
      return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
    case 'danger':
      return 'bg-red-500/20 text-red-400 border-red-500/30';
  }
}
