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

// ---------------------------------------------------------------------------
// ATPG
//
// Every numeric field is nullable. A null means the ATPG tool did not report
// that value, and must render as "Not reported by the ATPG tool" -- never as 0.
// ---------------------------------------------------------------------------

export type AnalysisMode = 'targeted' | 'non_targeted' | 'compare';

export interface AnalysisModeOption {
  value: AnalysisMode;
  label: string;
  description: string;
}

export interface AtpgCircuitStructure {
  name: string | null;
  primary_inputs: number | null;
  primary_outputs: number | null;
  gates: number | null;
  logic_depth: number | null;
}

export interface AtpgParameters {
  generation_mode: string | null;
  random_pattern_limit: number | null;
  backtrack_limit: number | null;
  backtrack_limit_phase1: number | null;
  backtrack_limit_phase2: number | null;
  random_seed: number | null;
  compaction_mode: string | null;
  shuffling_compaction_limit: number | null;
  shuffles: number | null;
}

export interface AtpgResults {
  patterns_before_compaction: number | null;
  patterns_after_compaction: number | null;
  fault_coverage: number | null;
  collapsed_faults: number | null;
  redundant_faults: number | null;
  aborted_faults: number | null;
  backtrackings: number | null;
  detected_faults: number | null;
}

export interface TimingBreakdown {
  initialization: number | null;
  fault_simulation: number | null;
  fan: number | null;
  total: number | null;
}

export interface FaultEntry {
  fault_name: string;
  gate: string;
  fault_type: string;
  status: string;
}

export interface TestVector {
  index: number;
  inputs: string;
  outputs: string;
}

export interface TestVectorSet {
  primary_inputs: string[];
  primary_outputs: string[];
  vectors: TestVector[];
  count: number;
}

export interface AtpgRun {
  label: string;
  targeted: boolean;
  tool: string;
  version: string | null;
  command: string[];
  exit_code: number | null;
  circuit: AtpgCircuitStructure;
  parameters: AtpgParameters;
  results: AtpgResults;
  timing: TimingBreakdown;
  memory_mb: number | null;
  faults: FaultEntry[];
  test_vectors: TestVectorSet;
  targeted_gates: string[];
  raw_log: string;
  raw_stderr: string;
  artifacts: Record<string, string>;
}

export interface RunComparison {
  targeted_coverage: number | null;
  non_targeted_coverage: number | null;
  coverage_delta: number | null;
  targeted_circuit_wide_coverage: number | null;
  targeted_patterns: number | null;
  non_targeted_patterns: number | null;
  pattern_reduction_pct: number | null;
  targeted_runtime: number | null;
  non_targeted_runtime: number | null;
  notes: string[];
}

export interface AnalysisObservation {
  title: string;
  detail: string;
  gates: string[];
  confidence: string | null;
}

export interface SuspiciousRegion {
  name: string;
  gates: string[];
  rationale: string;
  confidence: string | null;
}

export interface CircuitAnalysis {
  source: 'llm' | 'structural' | 'unavailable';
  model: string | null;
  architecture_summary: string;
  observations: AnalysisObservation[];
  suspicious_regions: SuspiciousRegion[];
  reasoning: string[];
  unavailable_reason: string | null;
}

export interface CorrelatedGate {
  gate: string;
  analysis_context: string[];
  fault_names: string[];
  atpg_status: string;
  statement: string;
}

export interface CorrelationReport {
  correlated: CorrelatedGate[];
  analysis_gates_examined: number;
  undetected_gates_examined: number;
  summary: string;
  caveats: string[];
}

export interface TargetingInfo {
  strategy: string;
  used_llm: boolean;
  gates: string[];
  gate_count: number;
  notes: string[];
}

export interface AtpgCircuitOverview {
  name: string;
  filename: string;
  format: string;
  primary_inputs: number | null;
  primary_outputs: number | null;
  gates: number | null;
  logic_depth: number | null;
  total_nodes: number | null;
  total_edges: number | null;
  gate_types: Record<string, number>;
  max_fan_in: number | null;
  max_fan_out: number | null;
  avg_fan_in: number | null;
  avg_fan_out: number | null;
  flip_flops: number | null;
  is_dag: boolean | null;
}

export interface AtpgReport {
  job_id: string;
  mode: AnalysisMode;
  mode_label: string;
  circuit: AtpgCircuitOverview;
  runs: Partial<Record<'targeted' | 'non_targeted', AtpgRun>>;
  comparison: RunComparison | null;
  targeting: TargetingInfo | Record<string, never>;
  circuit_analysis: CircuitAnalysis | null;
  correlation: CorrelationReport | null;
  notes: string[];
}
