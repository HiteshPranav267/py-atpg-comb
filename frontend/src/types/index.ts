export interface GateFeature {
  gate_id: string;
  gate_type: string;
  role: string;
  level: number;
  fanin: number;
  fanout: number;
  pi_distance: number;
  po_distance: number;
  cc0: number;
  cc1: number;
  co: number;
  toggle_rate: number;
  detectable: boolean;
  undetected: boolean;
  redundant: boolean;
  fault_count: number;
  is_trojan_gate: boolean;
  is_trigger_gate: boolean;
  is_payload_gate: boolean;
  trojan_id?: number;
  extra_features: Record<string, any>;
}

export interface FeatureSet {
  circuit_name: string;
  gate_features: GateFeature[];
  atpg_coverage?: number;
  extraction_time_ms: number;
}

export interface RankedGate {
  gate_id: string;
  score: number;
  rank: number;
  reason?: string;
}

export interface RankingResult {
  circuit_name: string;
  ranked_gates: RankedGate[];
  ranker_name: string;
  execution_time: number;
}

export interface RankingRequest {
  feature_set: FeatureSet;
  ranker_name: string;
}

export interface EvaluationResult {
  ranker_name: string;
  circuit_name: string;
  top1_accuracy: number;
  top5_accuracy: number;
  top10_accuracy: number;
  recall_at_k: number;
  precision_at_k: number;
  mean_reciprocal_rank: number;
  search_space_reduction: number;
  k: number;
}

export interface BenchmarkStartRequest {
  dataset_dir: string;
  ranker_name: string;
  k?: number;
}

export interface BenchmarkStatusResponse {
  job_id: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED';
  progress: number;
  message: string;
}

export interface BenchmarkResultResponse {
  job_id: string;
  status: string;
  results?: EvaluationResult[];
  error?: string;
}

export interface CircuitMetadata {
  name: string;
  total_nodes: number;
  gates: number;
  primary_inputs: number;
  primary_outputs: number;
  levels: number;
}

export interface TrojanGenerateResult {
  blob: Blob;
  filename: string;
  circuit: string;
  variantsGenerated: number;
  strategy: string;
}

export interface CoverageResult {
  coverage_pct: number;
  test_patterns: number;
  error?: string;
}

export interface CoverageComparison {
  targeted_coverage: number;
  baseline_coverage: number;
  coverage_diff: number;
  targeted_patterns: number;
  baseline_patterns: number;
  pattern_reduction_pct: number;
  speedup_factor: number;
}

export interface VerdictGate {
  gate_id: string;
  verdict: string;
  reason: string;
}

export interface HeatmapEntry {
  verdict: string;
  color: string;
  severity: string;
  toggle_rate?: number | null;
  co?: number | null;
  centrality?: number | null;
}

export interface AtpgReport {
  targeted_coverage: CoverageResult | null;
  baseline_coverage: CoverageResult | null;
  comparison: CoverageComparison | null;
  trojan_candidates: VerdictGate[];
  cleared_gates: VerdictGate[];
  unknown_gates: VerdictGate[];
  heatmap: Record<string, HeatmapEntry>;
  circuit_stats: Record<string, any>;
  circuit_name: string;
  ai_suspects: string[];
  used_llm: boolean;
  note?: string;
}
