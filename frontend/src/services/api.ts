import axios from 'axios';
import type {
  CircuitMetadata,
  FeatureSet,
  RankingRequest,
  RankingResult,
  BenchmarkStartRequest,
  BenchmarkStatusResponse,
  BenchmarkResultResponse,
  AtpgReport,
  AnalysisMode,
  AnalysisModeOption,
  TrojanGenerateResult,
  AnalyticsSummary,
  LlmProvidersResponse,
  LlmModelsResponse,
} from '@/types';
import { useAppStore } from '@/store';

// Axios instance pointing to the FastAPI backend
// VITE_API_URL should be the Render root URL e.g. https://py-atpg-backend.onrender.com
// All business routes live under /api, so we append it here.
// A user-set override (Settings page) takes priority over the build-time value,
// so the same build can be pointed at a local or deployed backend without rebuilding.
function resolveBaseRoot(): string {
  const root = useAppStore.getState().apiBaseUrlOverride || import.meta.env.VITE_API_URL || '';
  return root.replace(/\/+$/, '');
}

/**
 * The LLM key saved in this browser, if any. Returning undefined lets the
 * backend fall back to its own environment variable rather than being sent
 * an empty string.
 */
function llmApiKey(): string | undefined {
  return useAppStore.getState().llmApiKey?.trim() || undefined;
}

/**
 * Provider/model/endpoint the user picked in Settings, as the backend's
 * field names. Blank values are omitted so the backend applies its own
 * defaults (and 'auto' is omitted too — it is the backend's default).
 */
function llmSelection(): { llm_provider?: string; llm_model?: string; llm_base_url?: string } {
  const { llmProvider, llmModel, llmBaseUrl } = useAppStore.getState();
  const out: { llm_provider?: string; llm_model?: string; llm_base_url?: string } = {};
  const provider = llmProvider?.trim();
  if (provider && provider !== 'auto') out.llm_provider = provider;
  const model = llmModel?.trim();
  if (model) out.llm_model = model;
  const baseUrl = llmBaseUrl?.trim();
  if (baseUrl) out.llm_base_url = baseUrl;
  return out;
}

/**
 * Backup keys saved in Settings, in the backend's field names. Slots with no
 * key are left out, so an added-but-unfilled row changes nothing.
 */
function llmFallbacks(): { api_key: string; provider?: string; model?: string; base_url?: string }[] {
  return useAppStore.getState().llmBackupKeys
    .filter((k) => k.apiKey?.trim())
    .map((k) => {
      const out: { api_key: string; provider?: string; model?: string; base_url?: string } = {
        api_key: k.apiKey!.trim(),
      };
      if (k.provider && k.provider !== 'auto') out.provider = k.provider;
      if (k.model?.trim()) out.model = k.model.trim();
      if (k.baseUrl?.trim()) out.base_url = k.baseUrl.trim();
      return out;
    });
}

const apiClient = axios.create({
  headers: {
    'Content-Type': 'application/json',
  },
});
apiClient.interceptors.request.use((config) => {
  const root = resolveBaseRoot();
  config.baseURL = root ? `${root}/api` : '/api';
  return config;
});

// Separate client for the /health endpoint (no /api prefix)
const healthClient = axios.create();
healthClient.interceptors.request.use((config) => {
  config.baseURL = resolveBaseRoot();
  return config;
});

export const api = {
  // Health
  healthCheck: async (): Promise<{ status: string }> => {
    const res = await healthClient.get('/health');
    return res.data;
  },
  // Circuit
  uploadCircuit: async (file: File): Promise<CircuitMetadata> => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await apiClient.post('/circuit/parse', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    return res.data.circuit;
  },

  // Features
  extractFeatures: async (file: File): Promise<FeatureSet> => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await apiClient.post('/features/extract', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    });
    return res.data;
  },

  // Ranking
  getRankers: async (): Promise<string[]> => {
    const res = await apiClient.get('/ranking/rankers');
    return res.data;
  },

  runRanking: async (req: RankingRequest): Promise<RankingResult> => {
    const fallbacks = llmFallbacks();
    const res = await apiClient.post('/ranking/run', {
      ...req,
      api_key: llmApiKey(),
      ...llmSelection(),
      ...(fallbacks.length ? { llm_fallbacks: fallbacks } : {}),
    });
    return res.data;
  },

  // LLM providers the backend supports, for the Settings picker.
  getLlmProviders: async (): Promise<LlmProvidersResponse> => {
    const res = await apiClient.get('/llm/providers');
    return res.data;
  },

  // Models the given credential can actually use, for the Settings picker.
  listLlmModels: async (params: {
    provider?: string;
    api_key?: string;
    base_url?: string;
  }): Promise<LlmModelsResponse> => {
    const res = await apiClient.post('/llm/models', params);
    return res.data;
  },

  // Trojan
  generateTrojanDataset: async (params: {
    file: File;
    variants?: number;
    strategy?: string;
    seed?: number;
    verifyActivation?: boolean;
    triggerInputs?: number;
    triggerType?: string;
    payloadType?: string;
  }): Promise<TrojanGenerateResult> => {
    const formData = new FormData();
    formData.append('file', params.file);
    formData.append('variants', String(params.variants ?? 10));
    formData.append('strategy', params.strategy ?? 'random');
    if (params.seed !== undefined && params.seed !== null) {
      formData.append('seed', String(params.seed));
    }
    formData.append('verify_activation', String(params.verifyActivation ?? false));
    formData.append('num_trigger_inputs', String(params.triggerInputs ?? 2));
    formData.append('trigger_type', params.triggerType ?? 'AND');
    formData.append('payload_type', params.payloadType ?? 'XOR');
    const res = await apiClient.post('/trojan/generate', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      responseType: 'blob',
    });

    const disposition: string = res.headers['content-disposition'] || '';
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : 'trojan_dataset.zip';

    return {
      blob: res.data as Blob,
      filename,
      circuit: res.headers['x-circuit-name'] || params.file.name,
      variantsGenerated: parseInt(res.headers['x-variants-generated'] || '0', 10),
      strategy: res.headers['x-strategy'] || params.strategy || 'random',
      triggerInputs: parseInt(res.headers['x-trigger-inputs'] || String(params.triggerInputs ?? 2), 10),
      triggerType: res.headers['x-trigger-type'] || params.triggerType || 'AND',
      payloadType: res.headers['x-payload-type'] || params.payloadType || 'XOR',
    };
  },

  // Benchmark
  startBenchmark: async (req: BenchmarkStartRequest): Promise<{ job_id: string }> => {
    const res = await apiClient.post('/benchmark/start', req);
    return res.data;
  },

  getBenchmarkStatus: async (jobId: string): Promise<BenchmarkStatusResponse> => {
    const res = await apiClient.get(`/benchmark/status/${jobId}`);
    return res.data;
  },

  getBenchmarkResult: async (jobId: string): Promise<BenchmarkResultResponse> => {
    const res = await apiClient.get(`/benchmark/result/${jobId}`);
    return res.data;
  },

  // ATPG
  getAnalysisModes: async (): Promise<AnalysisModeOption[]> => {
    const res = await apiClient.get('/atpg/modes');
    return res.data;
  },

  runAtpg: async (params: {
    file: File;
    mode?: AnalysisMode;
    patterns?: number;
    use_llm?: boolean;
    full_scan?: boolean;
  }): Promise<AtpgReport> => {
    const formData = new FormData();
    formData.append('file', params.file);
    formData.append('mode', params.mode ?? 'compare');
    formData.append('patterns', String(params.patterns ?? 3000));
    formData.append('use_llm', String(params.use_llm ?? true));
    formData.append('full_scan', String(params.full_scan ?? false));
    const key = llmApiKey();
    if (key) formData.append('api_key', key);
    for (const [field, value] of Object.entries(llmSelection())) {
      if (value) formData.append(field, value);
    }
    const fallbacks = llmFallbacks();
    if (fallbacks.length) formData.append('llm_fallbacks', JSON.stringify(fallbacks));
    const res = await apiClient.post('/atpg/run', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Two ATPG runs plus toggle simulation on a large circuit -- and, for
      // RTL uploads such as Trust-Hub AES (~190k gates once synthesized),
      // Yosys synthesis first.
      timeout: 3600000,
    });
    return res.data;
  },

  /** Absolute URL for a raw artifact, used directly as an anchor href. */
  atpgArtifactUrl: (jobId: string, filename: string): string => {
    const root = resolveBaseRoot();
    const base = root ? `${root}/api` : '/api';
    return `${base}/atpg/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(filename)}`;
  },

  // Analytics
  getAnalyticsSummary: async (): Promise<AnalyticsSummary> => {
    const res = await apiClient.get('/analytics/summary');
    return res.data;
  }
};
