# Run database

Every request the backend handles is recorded as one row in `runs`, linked to
the circuit it ran on and to per-engine result tables. Schema:
[`db/schema.sql`](../db/schema.sql) (PostgreSQL 14+).

```
circuits 1──* runs 1──* comb_atpg_runs 1──* fault_results
                 │ 1──1 gate_selections
                 │ 1──1 comb_comparisons
                 │ 1──* seq_atpg_runs  1──* fault_results
                 │ 1──1 seq_comparisons
                 │ 1──* llm_usage
                 │ 1──1 trojan_verdicts 1──* suspect_gates
                 │ 1──1 trojan_generations | ranking_runs | benchmark_jobs | feature_extractions
                 └ 1──* artifacts
```

Never stored: API keys, admin tokens, the uploaded source (only its hash and
size), raw Atalanta logs (they stay in the job's artifacts).

## How runs are classified

| Axis | Column | Values |
|---|---|---|
| What was run | `runs.run_type` | atpg, circuit_parse, feature_extract, ranking, trojan_generate, benchmark |
| How it ended | `runs.outcome` | ok, error, cancelled, rejected, timeout |
| ATPG mode | `runs.mode` | targeted, non_targeted, compare |
| Engine sub-run | `*_atpg_runs.kind` | targeted, non_targeted (combinational); deterministic, llm_assisted (sequential) |
| Circuit type | `circuits.circuit_kind` | combinational, sequential |
| Abstraction | `circuits.design_level` | gate, rtl |
| File format | `circuits.input_format` | bench, verilog_gate, verilog_rtl, vhdl, bundle |
| Benchmark suite | `circuits.source_suite` | iscas85, iscas89, itc99, trust_hub_chip, trust_hub_board, generated_trojan, custom, unknown |
| Trojan ground truth | `circuits.trojan_label` | trojan_free, trojan_inserted, unknown |
| LLM | `runs.llm_provider`, `runs.llm_model`, `runs.llm_key_source` | groq / gemini / …; request, server_env, none |

`source_suite`, `benchmark_family` and `trojan_label` are guessed from names:

- `c\d+` → iscas85
- `s\d+` (and `_scan`) → iscas89
- `b\d\d` → itc99
- `<NAME>-T\d+` → trust_hub_chip, with trojan_label=trojan_inserted
- a `TjFree` folder → trojan_free
- the tool's own generator output → generated_trojan

An admin can correct any of these afterwards.

## Columns

### circuits — one per distinct design (deduplicated by content hash)

| Column | Type | Meaning / source |
|---|---|---|
| id | bigint PK | |
| content_sha256 | char(64) unique | hash of the uploaded bytes (packed text for bundles) |
| name | text | top module / bench name from the parser |
| display_name | text | human name, e.g. `AES-T900` |
| first_seen_at | timestamptz | |
| source_suite | enum | see classification |
| benchmark_family | text | `AES`, `RS232`, `s38584`, `c6288` |
| trojan_label | enum | trojan_free / trojan_inserted / unknown |
| trojan_id | text | `AES-T900`, generated variant name |
| trojan_trigger_type | text | Trust-Hub taxonomy or generator metadata |
| trojan_payload_type | text | 〃 |
| trojan_gates | jsonb | ground-truth Trojan gate ids, when known (generated variants) |
| design_level | enum | gate / rtl |
| input_format | enum | bench / verilog_gate / verilog_rtl / vhdl / bundle |
| circuit_kind | enum | combinational / sequential |
| has_scan | bool | full-scan netlist (`_scan` variants) |
| file_count | int | files in the upload (bundles > 1) |
| upload_bytes | bigint | |
| primary_inputs | int | after synthesis/flattening |
| primary_outputs | int | 〃 |
| gates | int | 〃 |
| flip_flops | int | 〃 |
| logic_depth | int | |
| loop_cuts | int | combinational loops broken with pseudo flip-flops |
| dead_nets_removed | int | undriven/unused logic dropped by `remove_dead_logic` |
| synthesized | bool | went through Yosys |
| synth_cells | int | cells Yosys produced |
| synth_seconds | real | |
| synth_top | text | top module chosen |
| synth_notes | jsonb | `core.rtl` work notes (files dropped, TjIn chosen, stubs…) |

### runs — one per request

| Column | Type | Meaning / source |
|---|---|---|
| id | uuid PK | |
| progress_id | text | frontend progress id |
| job_id | text | workspace id used for artifact downloads |
| run_type | enum | see classification |
| endpoint | text | `/api/atpg/run` |
| label | text | `run_tracked` label |
| received_at | timestamptz | request arrived |
| started_at | timestamptz | got an analysis slot |
| finished_at | timestamptz | |
| queue_wait_s | real | received → started |
| duration_s | real | started → finished |
| outcome | enum | ok / error / cancelled / rejected / timeout |
| http_status | smallint | |
| error_stage | text | last `progress.report()` stage before failing |
| error_class | text | e.g. `AtalantaExecutionError`, `DesignTooLargeError` |
| error_message | text | first 2000 chars |
| cancelled_by | text | client_disconnect / admin |
| circuit_id | bigint FK | → circuits |
| upload_filename | text | |
| circuit_kind | enum | copy of circuits.circuit_kind, for filtering |
| mode | enum | targeted / non_targeted / compare |
| requested_patterns | int | `patterns` form field |
| backtrack_limit | int | |
| full_scan | bool | |
| use_llm | bool | |
| llm_provider | text | resolved provider id |
| llm_model | text | resolved model |
| llm_base_url_host | text | host only, for custom endpoints |
| llm_key_source | enum | request / server_env / none |
| llm_fallback_keys | smallint | backup keys sent |
| fault_coverage | real | headline: non-targeted (comb) or LLM-assisted (seq) coverage |
| patterns | int | headline: test patterns / sequences |
| llm_api_calls | int | total over stages |
| llm_total_tokens | int | 〃 |
| trojan_verdict | text | headline verdict |
| backend_commit | text | `RENDER_GIT_COMMIT` / git HEAD |
| hosted | bool | running on Render |
| peak_rss_mb | int | process memory at the end of the run |
| settings_snapshot | jsonb | live settings (timeouts, limits) in force |
| client_hash | char(16) | salted hash of client IP: counts distinct users, identifies no one |

### comb_atpg_runs — one per Atalanta run (targeted / non-targeted, per escalation attempt)

| Column | Type | Meaning / source |
|---|---|---|
| id | bigint PK | |
| run_id | uuid FK | |
| kind | enum | targeted / non_targeted |
| attempt, attempt_tag | smallint, text | backtrack-escalation attempt |
| backtrack_limit, backtrack_limit_phase2 | int | Atalanta `-b`, `-B` |
| static_learning | bool | `-L` |
| random_pattern_limit, random_seed, compaction_mode | | `AtpgParameters` |
| targeted_gate_count, targeted_fault_count | int | size of the `-f` list |
| collapsed_faults, detected_faults, redundant_faults, aborted_faults | int | Atalanta summary |
| fault_coverage, fault_efficiency | real | % |
| patterns_before_compact, patterns_after_compact | int | |
| backtrackings | int | |
| sim_fault_count, sim_detected, sim_coverage | | whole-circuit fault simulation of this run's patterns (same denominator for every run) |
| cpu_init_s, cpu_fault_sim_s, cpu_fan_s, cpu_total_s | real | Atalanta `TimingBreakdown` |
| wall_s | real | |
| memory_mb | real | |
| exit_code | int | |

### gate_selections — how targeted gates were chosen

| Column | Meaning |
|---|---|
| run_id | PK/FK |
| strategy | llm / structural / combined |
| used_llm | the LLM was actually consulted |
| cached | answer came from the LLM cache |
| gate_count | gates selected |
| structural_suspect_count | rare-node / structural suspects |
| toggle_patterns, toggle_seed | toggle-profiling settings |
| extraction_s, llm_s | time spent |
| gates, notes | jsonb |

### comb_comparisons — compare mode, combinational

Columns:

- `targeted_coverage`, `non_targeted_coverage`, `coverage_delta`, `targeted_circuit_wide_coverage`
- `targeted_patterns`, `non_targeted_patterns`, `pattern_reduction_pct`
- `targeted_runtime_s`, `non_targeted_runtime_s`
- `targeted_faults`, `non_targeted_faults`, `targeted_fault_share_pct`
- `targeted_sim_coverage`, `non_targeted_sim_coverage`, `sim_coverage_delta`, `sim_fault_count`

They are `RunComparison` one-to-one.

### seq_atpg_runs — one per sequential engine run

| Column | Meaning |
|---|---|
| id, run_id | |
| kind | deterministic / llm_assisted |
| time_budget_s, max_frames, random_sequences | engine parameters |
| parameters | jsonb, full parameter set |
| total_faults, detected_faults, undetected_faults, aborted_faults | `SequentialResults` |
| podem_exhausted_faults, not_confirmed_faults, not_attempted_faults | why undetected faults stayed undetected |
| fault_coverage | % |
| sequence_count, total_clock_cycles, max_sequence_length | test sequence cost |
| stopped_by | complete / time_budget / cancelled |
| phases | jsonb, per-phase faults and time |
| extraction_s, llm_s, atpg_s, total_s | timing |

### seq_comparisons — compare mode, sequential

| Column | Meaning |
|---|---|
| run_id | |
| baseline_coverage, llm_coverage, coverage_delta | |
| additional_faults_detected | faults only the LLM-assisted run found |
| additional_sequences, additional_clock_cycles | extra test cost |
| ran_in_parallel | baseline ran in a separate process |

### fault_results — per fault (optional for large designs)

| Column | Meaning |
|---|---|
| comb_run_id or seq_run_id | exactly one is set |
| gate, stuck_at | the fault |
| status | detected, redundant, aborted, undetected, podem_exhausted, not_confirmed, not_attempted |
| detected_by | pattern / sequence index |

For designs over about 20k gates, store only non-detected faults. AES-T900 alone has about 400k faults.

### llm_usage — one per run per pipeline stage

| Column | Meaning |
|---|---|
| run_id, stage | stage: gate_targeting, circuit_analysis, vector_generation, … |
| provider, model | |
| api_calls, cached_hits, failed_calls | |
| fallback_key_used | a backup key answered |
| prompt_tokens, response_tokens, total_tokens | |
| tokens_exact | vendor-reported (true) vs estimated |
| duration_s | |
| last_error | e.g. rate limit message |

### trojan_verdicts / suspect_gates

trojan_verdicts columns:

| Column | Meaning |
|---|---|
| run_id | |
| verdict | `TrojanVerdict.verdict` |
| confidence | |
| suspect_count | |
| summary | |
| caveats | jsonb |
| ground_truth_label | copied from the circuit |
| verdict_correct | |
| true_positive_gates | |
| suspect_precision, suspect_recall | |

suspect_gates columns:

| Column | Meaning |
|---|---|
| run_id, rank | |
| gate | |
| confidence | |
| evidence | structural, llm, atpg, combined |
| atpg_status | |
| reasons | jsonb |
| activating_vectors | count |
| is_trojan_gate | NULL when ground truth is unknown |

### Other run types

| Table | Columns |
|---|---|
| trojan_generations | variants_requested, variants_generated, strategy, seed, trigger_inputs, trigger_type, payload_type, verify_activation, activation_verified |
| ranking_runs | ranker_name, k, gate_count, execution_s, top_gates |
| benchmark_jobs | dataset_dir, ranker_name, k, status, circuits_evaluated, metrics |
| feature_extractions | gate_count, feature_count, toggle_patterns, extraction_s |
| artifacts | engine_kind, artifact_key, filename, size_bytes, deleted_at |

## Views

| View | Answers |
|---|---|
| `v_run_overview` | every run with its circuit classification: the admin table |
| `v_llm_effect` | baseline vs LLM coverage per run, combinational and sequential together |
| `v_trojan_accuracy` | verdict accuracy and suspect precision/recall per suite × model, where ground truth exists |
