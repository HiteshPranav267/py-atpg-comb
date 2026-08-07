import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { CircuitMetadata, FeatureSet, RankingResult, EvaluationResult } from '@/types';

export interface BenchmarkHistoryEntry extends EvaluationResult {
  job_id: string;
  completed_at: string; // ISO timestamp
}

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

  // Benchmark history (backend is stateless / in-memory only, so we
  // persist completed runs on the client to give the dashboard real data)
  benchmarkHistory: BenchmarkHistoryEntry[];
  addBenchmarkResults: (job_id: string, results: EvaluationResult[]) => void;

  // Status Bar State
  backendStatus: 'CONNECTED' | 'DISCONNECTED' | 'BUSY';
  setBackendStatus: (status: 'CONNECTED' | 'DISCONNECTED' | 'BUSY') => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      activeCircuit: null,
      activeFeatureSet: null,
      activeRanking: null,
      selectedGateId: null,

      setActiveCircuit: (circuit) => set({ activeCircuit: circuit }),
      setActiveFeatureSet: (featureSet) => set({ activeFeatureSet: featureSet }),
      setActiveRanking: (ranking) => set({ activeRanking: ranking }),
      setSelectedGateId: (gateId) => set({ selectedGateId: gateId }),

      benchmarkHistory: [],
      addBenchmarkResults: (job_id, results) => set((state) => ({
        benchmarkHistory: [
          ...results.map((r) => ({ ...r, job_id, completed_at: new Date().toISOString() })),
          ...state.benchmarkHistory,
        ].slice(0, 50), // cap history so localStorage doesn't grow unbounded
      })),

      backendStatus: 'DISCONNECTED',
      setBackendStatus: (status) => set({ backendStatus: status }),
    }),
    {
      name: 'pyatpg-app-store',
      partialize: (state) => ({ benchmarkHistory: state.benchmarkHistory }),
    }
  )
);
