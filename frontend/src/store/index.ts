import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { CircuitMetadata, FeatureSet, RankingResult, EvaluationResult, AtpgReport } from '@/types';

export interface BenchmarkHistoryEntry extends EvaluationResult {
  job_id: string;
  completed_at: string; // ISO timestamp
}

interface AppState {
  // Global State
  activeCircuit: CircuitMetadata | null;
  // The raw uploaded Verilog file, kept so every other tool (features,
  // trojan generation, ATPG) can reuse the same circuit without re-uploading.
  activeCircuitFile: File | null;
  activeFeatureSet: FeatureSet | null;
  activeRanking: RankingResult | null;
  activeAtpgReport: AtpgReport | null;
  selectedGateId: string | null;

  // Actions
  setActiveCircuit: (circuit: CircuitMetadata | null, file?: File | null) => void;
  setActiveFeatureSet: (featureSet: FeatureSet | null) => void;
  setActiveRanking: (ranking: RankingResult | null) => void;
  setActiveAtpgReport: (report: AtpgReport | null) => void;
  setSelectedGateId: (gateId: string | null) => void;

  // Benchmark history (backend is stateless / in-memory only, so we
  // persist completed runs on the client to give the dashboard real data)
  benchmarkHistory: BenchmarkHistoryEntry[];
  addBenchmarkResults: (job_id: string, results: EvaluationResult[]) => void;

  // Status Bar State
  backendStatus: 'CONNECTED' | 'DISCONNECTED' | 'BUSY';
  setBackendStatus: (status: 'CONNECTED' | 'DISCONNECTED' | 'BUSY') => void;

  // Settings (persisted)
  theme: 'light' | 'dark';
  toggleTheme: () => void;
  defaultPatterns: number;
  setDefaultPatterns: (n: number) => void;
  defaultUseLlm: boolean;
  setDefaultUseLlm: (v: boolean) => void;
  apiBaseUrlOverride: string | null;
  setApiBaseUrlOverride: (url: string | null) => void;
  /**
   * Optional LLM credential sent with each request, so a key can be changed
   * without restarting the backend. Stored in this browser's localStorage;
   * when null the backend falls back to its own environment variable.
   */
  llmApiKey: string | null;
  setLlmApiKey: (key: string | null) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      activeCircuit: null,
      activeCircuitFile: null,
      activeFeatureSet: null,
      activeRanking: null,
      activeAtpgReport: null,
      selectedGateId: null,

      setActiveCircuit: (circuit, file) => set((state) => {
        const isNewUpload = file !== undefined;
        return {
          activeCircuit: circuit,
          activeCircuitFile: isNewUpload ? file : state.activeCircuitFile,
          // A new/cleared circuit invalidates anything derived from the old one
          activeFeatureSet: (circuit === null || isNewUpload) ? null : state.activeFeatureSet,
          activeRanking: (circuit === null || isNewUpload) ? null : state.activeRanking,
          activeAtpgReport: (circuit === null || isNewUpload) ? null : state.activeAtpgReport,
        };
      }),
      setActiveFeatureSet: (featureSet) => set({ activeFeatureSet: featureSet }),
      setActiveRanking: (ranking) => set({ activeRanking: ranking }),
      setActiveAtpgReport: (report) => set({ activeAtpgReport: report }),
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

      theme: 'dark',
      toggleTheme: () => set((state) => ({ theme: state.theme === 'dark' ? 'light' : 'dark' })),
      defaultPatterns: 3000,
      setDefaultPatterns: (n) => set({ defaultPatterns: n }),
      defaultUseLlm: true,
      setDefaultUseLlm: (v) => set({ defaultUseLlm: v }),
      apiBaseUrlOverride: null,
      setApiBaseUrlOverride: (url) => set({ apiBaseUrlOverride: url }),
      llmApiKey: null,
      setLlmApiKey: (key) => set({ llmApiKey: key }),
    }),
    {
      name: 'pyatpg-app-store',
      partialize: (state) => ({
        benchmarkHistory: state.benchmarkHistory,
        theme: state.theme,
        defaultPatterns: state.defaultPatterns,
        defaultUseLlm: state.defaultUseLlm,
        apiBaseUrlOverride: state.apiBaseUrlOverride,
        llmApiKey: state.llmApiKey,
      }),
    }
  )
);
