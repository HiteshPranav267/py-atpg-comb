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
  TrojanGenerateResult
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
    const res = await apiClient.post('/ranking/run', { ...req, api_key: llmApiKey() });
    return res.data;
  },

  // Trojan
  generateTrojanDataset: async (params: { file: File; variants?: number; strategy?: string }): Promise<TrojanGenerateResult> => {
    const formData = new FormData();
    formData.append('file', params.file);
    formData.append('variants', String(params.variants ?? 10));
    formData.append('strategy', params.strategy ?? 'random');
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

  // ATPG (Atalanta)
  getAnalysisModes: async (): Promise<AnalysisModeOption[]> => {
    const res = await apiClient.get('/atpg/modes');
    return res.data;
  },

  runAtpg: async (params: {
    file: File;
    mode?: AnalysisMode;
    patterns?: number;
    use_llm?: boolean;
  }): Promise<AtpgReport> => {
    const formData = new FormData();
    formData.append('file', params.file);
    formData.append('mode', params.mode ?? 'compare');
    formData.append('patterns', String(params.patterns ?? 3000));
    formData.append('use_llm', String(params.use_llm ?? true));
    const key = llmApiKey();
    if (key) formData.append('api_key', key);
    const res = await apiClient.post('/atpg/run', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Two Atalanta runs plus toggle simulation on a large circuit.
      timeout: 300000,
    });
    return res.data;
  },

  /** Absolute URL for a raw artifact, used directly as an anchor href. */
  atpgArtifactUrl: (jobId: string, filename: string): string => {
    const root = resolveBaseRoot();
    const base = root ? `${root}/api` : '/api';
    return `${base}/atpg/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(filename)}`;
  }
};
