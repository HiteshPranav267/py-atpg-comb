import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { CircuitMetadata, FeatureSet, RankingResult, EvaluationResult, AtpgReport } from '@/types';

/**
 * A backup LLM credential. Same fields as the main key; blank model/base URL
 * mean the provider's defaults. `id` only keeps React rows stable.
 */
export interface LlmKeySlot {
  id: string;
  provider: string;
  apiKey: string | null;
  model: string | null;
  baseUrl: string | null;
}

/** Main key plus this many backups: five keys in all. */
export const MAX_LLM_BACKUP_KEYS = 4;

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
  /** Admin token for the server's /api/admin endpoints (this browser only). */
  adminToken: string | null;
  setAdminToken: (token: string | null) => void;
  /**
   * Which vendor the key belongs to. 'auto' lets the backend sniff it from
   * the key's prefix (AIza -> Gemini, sk-ant- -> Anthropic, nvapi- -> NVIDIA,
   * sk- -> OpenAI); anything else is a provider id from GET /api/llm/providers.
   */
  llmProvider: string;
  setLlmProvider: (provider: string) => void;
  /** Model id override; null means the provider's default. */
  llmModel: string | null;
  setLlmModel: (model: string | null) => void;
  /** Endpoint override for OpenAI-compatible providers (Ollama, Groq, ...). */
  llmBaseUrl: string | null;
  setLlmBaseUrl: (url: string | null) => void;
  /**
   * Backup keys, tried in order by the backend when the main key (or the
   * previous backup) hits its rate/quota limit or is rejected. Stored in
   * localStorage alongside the main key.
   */
  llmBackupKeys: LlmKeySlot[];
  addLlmBackupKey: () => void;
  updateLlmBackupKey: (id: string, patch: Partial<Omit<LlmKeySlot, 'id'>>) => void;
  removeLlmBackupKey: (id: string) => void;
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
      adminToken: null,
      setAdminToken: (token) => set({ adminToken: token }),
      llmProvider: 'auto',
      setLlmProvider: (provider) => set({ llmProvider: provider }),
      llmModel: null,
      setLlmModel: (model) => set({ llmModel: model }),
      llmBaseUrl: null,
      setLlmBaseUrl: (url) => set({ llmBaseUrl: url }),
      llmBackupKeys: [],
      addLlmBackupKey: () => set((state) => (
        state.llmBackupKeys.length >= MAX_LLM_BACKUP_KEYS ? {} : {
          llmBackupKeys: [
            ...state.llmBackupKeys,
            {
              id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
              // Most people add another key for the same vendor, so start there.
              provider: state.llmProvider,
              apiKey: null,
              model: null,
              baseUrl: null,
            },
          ],
        }
      )),
      updateLlmBackupKey: (id, patch) => set((state) => ({
        llmBackupKeys: state.llmBackupKeys.map((k) => (k.id === id ? { ...k, ...patch } : k)),
      })),
      removeLlmBackupKey: (id) => set((state) => ({
        llmBackupKeys: state.llmBackupKeys.filter((k) => k.id !== id),
      })),
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
        adminToken: state.adminToken,
        llmProvider: state.llmProvider,
        llmModel: state.llmModel,
        llmBaseUrl: state.llmBaseUrl,
        llmBackupKeys: state.llmBackupKeys,
      }),
    }
  )
);
