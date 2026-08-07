import axios from 'axios';
import type {
  CircuitMetadata,
  FeatureSet,
  RankingRequest,
  RankingResult,
  BenchmarkStartRequest,
  BenchmarkStatusResponse,
  BenchmarkResultResponse
} from '@/types';

// Axios instance pointing to the FastAPI backend
// VITE_API_URL should be the Render root URL e.g. https://py-atpg-comb.onrender.com
// All business routes live under /api, so we append it here.
const BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api`
  : '/api';

const apiClient = axios.create({
  baseURL: BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Separate client for the /health endpoint (no /api prefix)
const healthClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
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
    const res = await apiClient.post('/ranking/run', req);
    return res.data;
  },

  // Trojan
  generateTrojanDataset: async (circuit_path: string, variants: number = 10, strategy: string = 'random'): Promise<any> => {
    const res = await apiClient.post('/trojan/generate', { circuit_path, variants, strategy });
    return res.data;
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
  }
};
