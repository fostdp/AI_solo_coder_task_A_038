export interface TelemetryData {
  device_id: number;
  shelf_id: number;
  timestamp: string;
  temperatures: number[];
  vacuum_levels: number[];
  cold_trap_temp: number;
  heating_powers: number[];
}

export interface RealtimeData {
  device_id: number;
  shelf_id: number;
  timestamp: string;
  temperatures: number[];
  temperature_diff: number;
  avg_temperature: number;
  vacuum_levels: number[];
  avg_vacuum: number;
  cold_trap_temp: number;
  heating_powers: number[];
  has_alarm: boolean;
}

export interface DeviceInfo {
  id: number;
  name: string;
  location: string;
  status: string;
}

export interface ShelfInfo {
  id: number;
  device_id: number;
  shelf_number: number;
  temp_sensor_count: number;
  vacuum_sensor_count: number;
}

export interface AlarmData {
  id: string;
  timestamp: string;
  device_id: number;
  shelf_id?: number;
  alarm_type: 'temperature_diff' | 'vacuum_abnormal' | 'cold_trap_high' | 'quality_prediction';
  severity: 'warning' | 'critical';
  message: string;
  acknowledged: boolean;
  acknowledged_by?: string;
  acknowledged_at?: string;
}

export interface PredictionResultData {
  device_id: number;
  batch_id?: string;
  moisture_content: {
    predicted: number;
    confidence: number;
    threshold: number;
    is_qualified: boolean;
  };
  reconstitution_time: {
    predicted: number;
    confidence: number;
    threshold: number;
    is_qualified: boolean;
  };
  drying_rate: number;
  is_qualified: boolean;
  timestamp?: string;
}

export interface ControlCommand {
  device_id: number;
  shelf_id: number;
  timestamp?: string;
  power_adjustments: number[];
  auto_mode: boolean;
}

export interface DeviceStats {
  shelf_id: number;
  sample_count: number;
  avg_temp: number;
  max_temp: number;
  min_temp: number;
  temp_diff: number;
  avg_vacuum: number;
  avg_cold_trap: number;
}

export interface VacuumDataPoint {
  timestamp: string;
  shelf_id: number;
  value: number;
}
