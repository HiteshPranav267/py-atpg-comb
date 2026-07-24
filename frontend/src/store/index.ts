import { create } from 'zustand';
import type { CircuitMetadata, FeatureSet, RankingResult } from '@/types';

interface AppState {
  // Global State
  activeCircuit: CircuitMetadata | null;
  activeFeatureSet: FeatureSet | null;
  activeRanking: RankingResult | null;
  selectedGateId: string | null;

  // Actions
  setActiveCircuit: (circuit: CircuitMetadata | null) => void;
  setActiveFeatureSet: (featureSet: FeatureSet | null) => void;
  setActiveRanking: (ranking: RankingResult | null) => void;
  setSelectedGateId: (gateId: string | null) => void;
  
  // Status Bar State
  backendStatus: 'CONNECTED' | 'DISCONNECTED' | 'BUSY';
  setBackendStatus: (status: 'CONNECTED' | 'DISCONNECTED' | 'BUSY') => void;
}

export const useAppStore = create<AppState>((set) => ({
  activeCircuit: null,
  activeFeatureSet: null,
  activeRanking: null,
  selectedGateId: null,
  
  setActiveCircuit: (circuit) => set({ activeCircuit: circuit }),
  setActiveFeatureSet: (featureSet) => set({ activeFeatureSet: featureSet }),
  setActiveRanking: (ranking) => set({ activeRanking: ranking }),
  setSelectedGateId: (gateId) => set({ selectedGateId: gateId }),
  
  backendStatus: 'DISCONNECTED',
  setBackendStatus: (status) => set({ backendStatus: status }),
}));
