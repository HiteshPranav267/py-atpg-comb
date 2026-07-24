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
