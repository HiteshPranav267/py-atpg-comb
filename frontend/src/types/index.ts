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

/** A named endpoint offered as a dropdown choice for a custom base URL. */
export interface LlmBaseUrlPreset {
  label: string;
  url: string;
}

/** One entry from GET /api/llm/providers. */
export interface LlmProviderSpec {
  id: string;
  label: string;
  env_vars: string[];
  default_model: string;
  key_prefix: string;
  openai_compatible: boolean;
  base_url: string | null;
  hint: string;
  /** Curated model ids for the dropdown, default first. Never exhaustive. */
  models: string[];
  /** Named endpoints for the base-URL dropdown (only used when openai_compatible). */
  base_url_presets: LlmBaseUrlPreset[];
}

export interface LlmModelInfo {
  id: string;
  /** Vendor's display name, or the id again when it has none. */
  label: string;
}

export interface LlmModelsResponse {
  provider: string;
  default_model: string;
  /** Text-generation models the credential can use, exact ids. */
  models: LlmModelInfo[];
}

export interface LlmProvidersResponse {
  auto: string;
  default_provider: string;
  providers: LlmProviderSpec[];
  /** What the server falls back to when a request carries no key; null if nothing is configured. */
  server_default: { provider: string; model: string } | null;
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
  triggerInputs: number;
  triggerType: string;
  payloadType: string;
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
  // (detected + redundant) / collapsed, percent.
  fault_efficiency?: number | null;
  fault_list?: string;
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
  // Every ATPG-tool attempt made for this run when the service re-ran the
  // same fault list at a higher search budget. Empty for a single attempt.
  attempts?: RunAttempt[];
  // This run's patterns fault-simulated over the whole circuit, on the same
  // fault list for every run, so pattern sets can be compared directly.
  pattern_coverage?: PatternCoverage | null;
}

export interface RunAttempt {
  label: string;
  backtrack_limit: number | null;
  backtrack_limit_phase2: number | null;
  static_learning: boolean;
  fault_coverage: number | null;
  aborted_faults: number | null;
  redundant_faults: number | null;
  patterns: number | null;
  cpu_seconds: number | null;
  // True on the attempt whose results the run reports (fewest aborted faults).
  reported?: boolean;
}

export interface PatternCoverage {
  total_faults: number;
  detected_faults: number;
  patterns: number;
  coverage: number | null;
  unavailable_reason: string | null;
  notes: string[];
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
  // Denominators behind the coverage figures. A targeted run that selects
  // two faults and detects both also reports 100%, so a coverage number is
  // only meaningful next to the size of the list it was measured over.
  targeted_faults: number | null;
  non_targeted_faults: number | null;
  targeted_fault_share_pct: number | null;
  // Both pattern sets fault-simulated over the same whole-circuit fault list:
  // the one pair of numbers here that shares a denominator.
  targeted_simulated_coverage?: number | null;
  non_targeted_simulated_coverage?: number | null;
  simulated_coverage_delta?: number | null;
  simulated_fault_count?: number | null;
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
  // Gates the deterministic trojan-shape detector flagged. Always a subset
  // of `gates` -- they are force-included so a model miss cannot drop them.
  structural_suspects?: string[];
  suspect_reasons?: string[];
  // Stimulus the toggle-rate ranking was measured with, so a selection can
  // be reproduced exactly.
  toggle_seed?: number | null;
  toggle_patterns?: number;
  // True when the model's answer was reused from an earlier identical run.
  cached?: boolean;
  // What the model was shown, when one was consulted.
  context?: {
    gates_sent?: number;
    gates_total?: number;
    gate_limit?: number;
    notes?: string[];
  };
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

// ---------------------------------------------------------------------------
// Sequential ATPG (pyatpg.seq) -- returned instead of runs/comparison/
// targeting/circuit_analysis/correlation when the circuit has flip-flops.
// ---------------------------------------------------------------------------

export interface SequentialTestSequence {
  vectors: Record<string, number>[];
  cycles: number;
  origin: string;
}

export interface SequentialResults {
  total_faults: number | null;
  detected_faults: number | null;
  undetected_faults: number | null;
  aborted_faults: number | null;
  fault_coverage: number | null;
  sequence_count: number | null;
  total_clock_cycles: number | null;
  max_sequence_length: number | null;
  // Breakdown of undetected faults by why the engine gave up on them.
  podem_exhausted_faults?: number | null;
  not_confirmed_faults?: number | null;
  not_attempted_faults?: number | null;
}

export interface SequentialFaultDetail {
  fault: string;
  node: string;
  node_type: string;
  stuck_at: number;
  status: string;
}

export interface SequentialPhase {
  phase: string;
  candidates: number;
  accepted: number;
  faults_detected: number;
  seconds: number;
}

export interface SequentialAnalysisData {
  circuit_name: string;
  results: SequentialResults;
  sequences: SequentialTestSequence[];
  detected: string[];
  undetected: string[];
  newly_detected_by_sequence: string[][];
  llm: Record<string, unknown> | null;
  notes: string[];
  fault_details?: SequentialFaultDetail[];
  outcome_descriptions?: Record<string, string>;
  phases?: SequentialPhase[];
  parameters?: Record<string, string | number | boolean | null>;
  timing?: { search_s?: number; final_fault_sim_s?: number; total_s?: number; llm_s?: number };
  stopped_by?: string | null;
}

export interface LlmUsage {
  enabled: boolean;
  provider?: string;
  model?: string;
  api_calls?: number;
  cached_hits?: number;
  prompt_tokens_est?: number;
  response_tokens_est?: number;
  total_tokens_est?: number;
  // True when every counted call carried the provider's own usage report,
  // so the numbers are exact rather than a chars/4 estimate.
  tokens_exact?: boolean;
  by_stage?: Record<
    string,
    {
      calls: number;
      cached_hits?: number;
      prompt_tokens_est?: number;
      response_tokens_est?: number;
      tokens_est: number;
    }
  >;
  circuit_summary?: string;
}

export interface SequentialComparison {
  baseline: SequentialAnalysisData;
  llm_assisted: SequentialAnalysisData;
  coverage_delta: number | null;
  additional_faults_detected: string[];
  additional_sequences: number | null;
  additional_clock_cycles: number | null;
  notes: string[];
}

export interface SequentialSummary {
  circuit: AtpgCircuitOverview;
  analysis: SequentialAnalysisData;
  llm_usage: LlmUsage;
  //: Populated instead of relying on `analysis` alone when Compare mode ran
  //: a deterministic-only pass and an LLM-assisted pass back to back.
  comparison?: SequentialComparison | null;
  // Downloadable files in the job workspace: key -> filename.
  artifacts?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// In-app product-usage analytics (not visitor/page-view analytics -- that's
// Vercel Web Analytics, tracked separately on the frontend and not
// queryable from this API).
// ---------------------------------------------------------------------------

export interface AnalyticsCoveragePoint {
  ts: number;
  coverage: number | null;
  circuit_kind: 'combinational' | 'sequential' | null;
  circuit_name: string | null;
}

export interface AnalyticsActivityEvent {
  ts: number;
  type: string;
  [key: string]: unknown;
}

export interface AnalyticsSummary {
  total_events: number;
  circuits_parsed: number;
  atpg_runs: {
    total: number;
    combinational: number;
    sequential: number;
    avg_coverage_combinational: number | null;
    avg_coverage_sequential: number | null;
  };
  llm: {
    runs_with_llm: number;
    total_api_calls: number;
    total_tokens_est: number;
  };
  ranking_runs: number;
  trojan_generations: number;
  benchmark_runs: number;
  coverage_over_time: AnalyticsCoveragePoint[];
  recent_activity: AnalyticsActivityEvent[];
}

export interface CpuTimeByRun {
  atpg_s: number | null;
  extraction_s: number;
  llm_s: number;
  total_s: number;
  note: string;
}

export interface CpuTimeBreakdown {
  extraction_s: number;
  llm_s: number;
  by_run: Partial<Record<'targeted' | 'non_targeted' | 'deterministic' | 'llm_assisted', CpuTimeByRun>>;
}

export interface SystemInfo {
  cpu_model: string;
  physical_cores: number | null;
  logical_cores: number | null;
  os: string;
  python_version: string;
}

/** One analysis holding or waiting for a server slot (GET /api/admin/runs). */
export interface AdminRun {
  id: string;
  state: 'running' | 'queued';
  stage: string;
  elapsed_s: number;
  stage_elapsed_s: number;
  cancelled: boolean;
  client_connected: boolean;
}

export interface AdminRunList {
  slots: number;
  running: AdminRun[];
  queued: AdminRun[];
}

/** Live state of an in-flight ATPG run (GET /api/atpg/progress/{id}). */
export interface AtpgProgress {
  stage: string;
  elapsed_s: number;
  stage_elapsed_s: number;
  done: boolean;
  history: { stage: string; at_s: number }[];
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
  verdict: TrojanVerdict | null;
  notes: string[];
  sequential: SequentialSummary | null;
  // Model calls and token counts for the combinational path (the
  // sequential path carries its own under `sequential.llm_usage`).
  llm_usage?: LlmUsage;
  // Feature-extraction and per-run CPU time, split so a non-targeted run's
  // total never includes work it didn't actually do (see the backend's
  // _cpu_time_breakdown). On the sequential path keyed deterministic / llm_assisted.
  cpu_time?: CpuTimeBreakdown;
  // The machine that actually produced every timing number in this report.
  system_info?: SystemInfo;
}

export interface VerdictSuspect {
  gate: string;
  confidence: number;
  evidence: string;
  reasons: string[];
  atpg_status: string;
  activating_vectors: string[];
  statement: string;
}

export interface TrojanVerdict {
  verdict: 'trojan_suspected' | 'inconclusive' | 'no_trojan_indicated';
  confidence: number;
  suspects: VerdictSuspect[];
  summary: string;
  caveats: string[];
}
