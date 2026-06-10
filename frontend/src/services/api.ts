import axios from 'axios';
import type { 
  DeviceInfo, 
  ShelfInfo, 
  RealtimeData, 
  TelemetryData,
  AlarmData, 
  PredictionResultData,
  DeviceStats 
} from '@/types';

const API_BASE_URL = '__API_BASE_URL__';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const deviceApi = {
  getDevices: (): Promise<DeviceInfo[]> => 
    api.get('/api/devices').then(res => res.data),
  
  getDevice: (id: number): Promise<DeviceInfo> => 
    api.get(`/api/devices/${id}`).then(res => res.data),
  
  getShelves: (deviceId: number): Promise<ShelfInfo[]> => 
    api.get(`/api/devices/${deviceId}/shelves`).then(res => res.data),
};

export const dataApi = {
  sendTelemetry: (data: TelemetryData) => 
    api.post('/api/data/telemetry', data),
  
  getRealtimeData: (deviceId: number): Promise<RealtimeData[]> => 
    api.get(`/api/data/realtime/${deviceId}`).then(res => res.data),
  
  getHistory: (params: {
    device_id: number;
    shelf_id?: number;
    start_time?: string;
    end_time?: string;
    limit?: number;
  }) => api.get('/api/data/history', { params }).then(res => res.data),
  
  getDeviceStats: (deviceId: number, hours: number = 1): Promise<{
    device_id: number;
    time_window_hours: number;
    stats: DeviceStats[];
  }> => api.get(`/api/data/stats/${deviceId}`, { params: { hours } }).then(res => res.data),
};

export const controlApi = {
  sendCommand: (command: {
    device_id: number;
    shelf_id: number;
    power_adjustments: number[];
    auto_mode: boolean;
  }) => api.post('/api/control/power', command).then(res => res.data),
  
  setMode: (deviceId: number, autoMode: boolean) => 
    api.put('/api/control/mode', { device_id: deviceId, auto_mode: autoMode }).then(res => res.data),
  
  getLatestCommand: (deviceId: number, shelfId?: number) => 
    api.get(`/api/control/latest/${deviceId}`, { params: { shelf_id: shelfId } }).then(res => res.data),
  
  calculateAdjustment: (deviceId: number, shelfId: number) => 
    api.get(`/api/control/calculate/${deviceId}/${shelfId}`).then(res => res.data),
  
  getThreshold: () => api.get('/api/control/threshold').then(res => res.data),
  
  setThreshold: (threshold: number) => 
    api.put('/api/control/threshold', null, { params: { threshold } }).then(res => res.data),
  
  getStatus: (deviceId: number) => 
    api.get(`/api/control/status/${deviceId}`).then(res => res.data),
};

export const predictionApi = {
  predictQuality: (deviceId: number, batchId?: string) => 
    api.post('/api/prediction/quality', null, { params: { device_id: deviceId, batch_id: batchId } }).then(res => res.data),
  
  getResults: (deviceId: number, limit: number = 10) => 
    api.get(`/api/prediction/result/${deviceId}`, { params: { limit } }).then(res => res.data),
  
  getModelInfo: (deviceId: number) => 
    api.get(`/api/prediction/model/${deviceId}`).then(res => res.data),
  
  setThresholds: (moistureMax: number, reconstitutionMax: number) => 
    api.put('/api/prediction/thresholds', null, { 
      params: { moisture_max: moistureMax, reconstitution_max: reconstitutionMax } 
    }).then(res => res.data),
};

export const alarmApi = {
  getCurrentAlarms: (): Promise<{ count: number; alarms: AlarmData[] }> => 
    api.get('/api/alarm/current').then(res => res.data),
  
  getHistory: (params?: {
    device_id?: number;
    alarm_type?: string;
    start_time?: string;
    end_time?: string;
    limit?: number;
  }): Promise<{ count: number; alarms: AlarmData[] }> => 
    api.get('/api/alarm/history', { params }).then(res => res.data),
  
  acknowledge: (alarmId: string, acknowledgedBy: string) => 
    api.post('/api/alarm/acknowledge', { 
      alarm_id: alarmId, 
      acknowledged_by: acknowledgedBy 
    }).then(res => res.data),
  
  checkAlarms: (deviceId: number, shelfId: number) => 
    api.post('/api/alarm/check', null, { params: { device_id: deviceId, shelf_id: shelfId } }).then(res => res.data),
  
  getThresholds: () => api.get('/api/alarm/thresholds').then(res => res.data),
  
  setThresholds: (params: {
    temp_diff?: number;
    vacuum_min?: number;
    vacuum_max?: number;
    cold_trap_max?: number;
    moisture_max?: number;
    reconstitution_max?: number;
  }) => api.put('/api/alarm/thresholds', null, { params }).then(res => res.data),
  
  getMqttStatus: () => api.get('/api/alarm/mqtt/status').then(res => res.data),
};

export default api;
