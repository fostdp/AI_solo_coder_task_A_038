import { create } from 'zustand';
import type { DeviceInfo, RealtimeData, AlarmData, PredictionResultData } from '@/types';

interface AppState {
  selectedDevice: number;
  devices: DeviceInfo[];
  realtimeData: Record<number, RealtimeData[]>;
  currentAlarms: AlarmData[];
  alarmHistory: AlarmData[];
  predictionResults: Record<number, PredictionResultData[]>;
  autoMode: Record<number, boolean>;
  lastUpdate: string;
  isLoading: boolean;
  error: string | null;
  
  setSelectedDevice: (id: number) => void;
  setDevices: (devices: DeviceInfo[]) => void;
  setRealtimeData: (deviceId: number, data: RealtimeData[]) => void;
  setCurrentAlarms: (alarms: AlarmData[]) => void;
  setAlarmHistory: (alarms: AlarmData[]) => void;
  setPredictionResults: (deviceId: number, results: PredictionResultData[]) => void;
  setAutoMode: (deviceId: number, enabled: boolean) => void;
  setLastUpdate: (time: string) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  acknowledgeAlarm: (alarmId: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedDevice: 1,
  devices: [],
  realtimeData: {},
  currentAlarms: [],
  alarmHistory: [],
  predictionResults: {},
  autoMode: {},
  lastUpdate: '',
  isLoading: false,
  error: null,

  setSelectedDevice: (id) => set({ selectedDevice: id }),
  setDevices: (devices) => set({ devices }),
  setRealtimeData: (deviceId, data) => 
    set((state) => ({
      realtimeData: { ...state.realtimeData, [deviceId]: data },
      lastUpdate: new Date().toISOString(),
    })),
  setCurrentAlarms: (alarms) => set({ currentAlarms: alarms }),
  setAlarmHistory: (alarms) => set({ alarmHistory: alarms }),
  setPredictionResults: (deviceId, results) => 
    set((state) => ({
      predictionResults: { ...state.predictionResults, [deviceId]: results },
    })),
  setAutoMode: (deviceId, enabled) => 
    set((state) => ({
      autoMode: { ...state.autoMode, [deviceId]: enabled },
    })),
  setLastUpdate: (time) => set({ lastUpdate: time }),
  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error }),
  acknowledgeAlarm: (alarmId) => 
    set((state) => ({
      currentAlarms: state.currentAlarms.map(a => 
        a.id === alarmId ? { ...a, acknowledged: true } : a
      ),
    })),
}));
