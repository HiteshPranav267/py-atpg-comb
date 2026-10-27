-- py-atpg run database (PostgreSQL 14+).
--
-- One row in `runs` per request the backend handles (ATPG, parse, feature
-- extraction, ranking, Trojan generation, benchmark), classified by what was
-- run, on what circuit, with which LLM, and how it ended. Engine-level
-- results hang off it in per-kind tables. See db/README.md for what
-- every column means and where its value comes from.
--
-- Never stored: API keys, admin tokens, uploaded circuit source (only its
-- hash and size), raw Atalanta logs (those stay in job artifacts).

BEGIN;

-- ---------------------------------------------------------------------------
-- Classification vocabularies
-- ---------------------------------------------------------------------------

CREATE TYPE run_type AS ENUM (
    'atpg',              -- POST /api/atpg/run (combinational or sequential)
    'circuit_parse',     -- POST /api/circuit/parse
    'feature_extract',   -- POST /api/feature/...
    'ranking',           -- POST /api/ranking/...
    'trojan_generate',   -- POST /api/trojan/generate
    'benchmark'          -- POST /api/benchmark/run
);

CREATE TYPE run_outcome AS ENUM (
    'ok',
    'error',             -- 4xx/5xx raised by the analysis
    'cancelled',         -- browser left, or admin cancelled (HTTP 499)
    'rejected',          -- maintenance mode / size guard before any work
    'timeout'            -- Atalanta / Yosys / LLM time limit hit
);

CREATE TYPE circuit_kind AS ENUM ('combinational', 'sequential');

CREATE TYPE design_level AS ENUM ('gate', 'rtl');

CREATE TYPE input_format AS ENUM (
    'bench',             -- ISCAS .bench
    'verilog_gate',      -- structural Verilog netlist
    'verilog_rtl',       -- behavioural Verilog / SystemVerilog, synthesized by Yosys
    'vhdl',              -- converted via GHDL, then Yosys
    'bundle'             -- multi-file upload (zip / folder) packed by core.rtl
);

CREATE TYPE source_suite AS ENUM (
    'iscas85',           -- c17, c432 ... c7552
    'iscas89',           -- s27 ... s38584 (incl. _scan variants)
    'itc99',             -- b01 ... b22
    'trust_hub_chip',    -- trust-hub.org chip-level Trojan benchmarks (AES-T900, ...)
    'trust_hub_board',   -- trust-hub.org board-level Trojan benchmarks
    'generated_trojan',  -- produced by this tool's Trojan generator
    'custom',
    'unknown'
);

CREATE TYPE trojan_label AS ENUM (
    'trojan_free',       -- golden / TjFree
    'trojan_inserted',   -- TjIn / generated variant
    'unknown'
);

CREATE TYPE atpg_mode AS ENUM ('targeted', 'non_targeted', 'compare');

CREATE TYPE engine_run_kind AS ENUM (
    'targeted',          -- combinational: Atalanta on LLM/structural-selected gates
    'non_targeted',      -- combinational: Atalanta on the full collapsed fault list
    'deterministic',     -- sequential: native engine, no LLM
    'llm_assisted'       -- sequential: native engine seeded by the LLM
);

CREATE TYPE llm_key_source AS ENUM ('request', 'server_env', 'none');

-- ---------------------------------------------------------------------------
-- Circuits: one row per distinct uploaded design (deduplicated by hash)
-- ---------------------------------------------------------------------------

CREATE TABLE circuits (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    content_sha256      CHAR(64)      NOT NULL UNIQUE,  -- of the uploaded bytes (bundle: packed text)
    name                TEXT          NOT NULL,         -- top module / bench name
    display_name        TEXT,                           -- e.g. 'AES-T900', 's38584 (scan)'
    first_seen_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),

    -- classification
    source_suite        source_suite  NOT NULL DEFAULT 'unknown',
    benchmark_family    TEXT,                           -- 'AES', 'RS232', 's38584', 'c6288'
    trojan_label        trojan_label  NOT NULL DEFAULT 'unknown',
    trojan_id           TEXT,                           -- 'AES-T900', 'variant_007'
    trojan_trigger_type TEXT,                           -- from Trust-Hub taxonomy / generator metadata
    trojan_payload_type TEXT,
    trojan_gates        JSONB,                          -- ground-truth Trojan gate ids, when known
    design_level        design_level  NOT NULL,
    input_format        input_format  NOT NULL,
    circuit_kind        circuit_kind  NOT NULL,
    has_scan            BOOLEAN       NOT NULL DEFAULT FALSE,

    -- size (after synthesis / flattening, i.e. what the engines saw)
    file_count          INTEGER       NOT NULL DEFAULT 1,
    upload_bytes        BIGINT,
    primary_inputs      INTEGER,
    primary_outputs     INTEGER,
    gates               INTEGER,
    flip_flops          INTEGER       NOT NULL DEFAULT 0,
    logic_depth         INTEGER,
    loop_cuts           INTEGER       NOT NULL DEFAULT 0, -- combinational loops broken with pseudo-FFs
    dead_nets_removed   INTEGER       NOT NULL DEFAULT 0,

    -- RTL front-end
    synthesized         BOOLEAN       NOT NULL DEFAULT FALSE,
    synth_cells         INTEGER,
    synth_seconds       REAL,
    synth_top           TEXT,
    synth_notes         JSONB                           -- core.rtl work_notes
);

CREATE INDEX circuits_suite_idx  ON circuits (source_suite, benchmark_family);
CREATE INDEX circuits_trojan_idx ON circuits (trojan_label);

-- ---------------------------------------------------------------------------
-- Runs: one row per request, the hub every other table hangs off
-- ---------------------------------------------------------------------------

CREATE TABLE runs (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    progress_id         TEXT,                           -- frontend progress id, if sent
    job_id              TEXT,                           -- workspace id (artifact downloads)
    run_type            run_type      NOT NULL,
    endpoint            TEXT          NOT NULL,         -- '/api/atpg/run'
    label               TEXT,                           -- concurrency.run_tracked label

    -- timing
    received_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,                    -- got an analysis slot
    finished_at         TIMESTAMPTZ,
    queue_wait_s        REAL,
    duration_s          REAL,                           -- started -> finished

    -- outcome
    outcome             run_outcome   NOT NULL,
    http_status         SMALLINT,
    error_stage         TEXT,                           -- last progress.report() stage before failure
    error_class         TEXT,                           -- 'AtalantaExecutionError', 'DesignTooLargeError'
    error_message       TEXT,                           -- truncated to 2000 chars
    cancelled_by        TEXT,                           -- 'client_disconnect' | 'admin'

    -- what was run
    circuit_id          BIGINT        REFERENCES circuits(id),
    upload_filename     TEXT,
    circuit_kind        circuit_kind,                   -- copied from circuits for fast filtering
    mode                atpg_mode,
    requested_patterns  INTEGER,                        -- 'patterns' form field
    backtrack_limit     INTEGER,
    full_scan           BOOLEAN,
    use_llm             BOOLEAN       NOT NULL DEFAULT FALSE,

    -- LLM configuration (never the key itself)
    llm_provider        TEXT,                           -- 'groq', 'gemini', ...
    llm_model           TEXT,
    llm_base_url_host   TEXT,                           -- host only, for custom endpoints
    llm_key_source      llm_key_source NOT NULL DEFAULT 'none',
    llm_fallback_keys   SMALLINT      NOT NULL DEFAULT 0,

    -- headline results (denormalized for dashboards; details in child tables)
    fault_coverage      REAL,                           -- primary run: non_targeted / llm_assisted
    patterns            INTEGER,                        -- test patterns or sequences
    llm_api_calls       INTEGER,
    llm_total_tokens    INTEGER,
    trojan_verdict      TEXT,

    -- environment
    backend_commit      TEXT,
    hosted              BOOLEAN       NOT NULL DEFAULT FALSE,
    peak_rss_mb         INTEGER,
    settings_snapshot   JSONB,                          -- core.settings.describe() values at run time
    client_hash         CHAR(16)                        -- salted hash of client IP: counts users, identifies no one
);

CREATE INDEX runs_received_idx ON runs (received_at DESC);
CREATE INDEX runs_type_idx     ON runs (run_type, outcome);
CREATE INDEX runs_circuit_idx  ON runs (circuit_id);
CREATE INDEX runs_llm_idx      ON runs (llm_provider, llm_model);

-- ---------------------------------------------------------------------------
-- Combinational ATPG: one row per Atalanta run (targeted / non-targeted, and
-- each escalation attempt)
-- ---------------------------------------------------------------------------

CREATE TABLE comb_atpg_runs (
    id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                   UUID     NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind                     engine_run_kind NOT NULL CHECK (kind IN ('targeted', 'non_targeted')),
    attempt                  SMALLINT NOT NULL DEFAULT 1,
    attempt_tag              TEXT,

    -- parameters
    backtrack_limit          INTEGER,
    backtrack_limit_phase2   INTEGER,
    static_learning          BOOLEAN  NOT NULL DEFAULT FALSE,
    random_pattern_limit     INTEGER,
    random_seed              BIGINT,
    compaction_mode          TEXT,
    targeted_gate_count      INTEGER,
    targeted_fault_count     INTEGER,

    -- Atalanta results (over its own collapsed fault list)
    collapsed_faults         INTEGER,
    detected_faults          INTEGER,
    redundant_faults         INTEGER,
    aborted_faults           INTEGER,
    fault_coverage           REAL,
    fault_efficiency         REAL,
    patterns_before_compact  INTEGER,
    patterns_after_compact   INTEGER,
    backtrackings            INTEGER,

    -- whole-circuit fault simulation of this run's patterns (same denominator for every run)
    sim_fault_count          INTEGER,
    sim_detected             INTEGER,
    sim_coverage             REAL,

    -- cost
    cpu_init_s               REAL,
    cpu_fault_sim_s          REAL,
    cpu_fan_s                REAL,
    cpu_total_s              REAL,
    wall_s                   REAL,
    memory_mb                REAL,
    exit_code                INTEGER,

    UNIQUE (run_id, kind, attempt)
);

-- How the targeted gates were picked (one per targeted ATPG request)
CREATE TABLE gate_selections (
    run_id                   UUID     PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    strategy                 TEXT     NOT NULL,         -- 'llm', 'structural', 'llm+structural'
    used_llm                 BOOLEAN  NOT NULL,
    cached                   BOOLEAN  NOT NULL DEFAULT FALSE,
    gate_count               INTEGER  NOT NULL,
    structural_suspect_count INTEGER,
    toggle_patterns          INTEGER,
    toggle_seed              BIGINT,
    extraction_s             REAL,
    llm_s                    REAL,
    gates                    JSONB,                     -- selected gate ids
    notes                    JSONB
);

CREATE TABLE comb_comparisons (
    run_id                          UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    targeted_coverage               REAL,
    non_targeted_coverage           REAL,
    coverage_delta                  REAL,
    targeted_circuit_wide_coverage  REAL,
    targeted_patterns               INTEGER,
    non_targeted_patterns           INTEGER,
    pattern_reduction_pct           REAL,
    targeted_runtime_s              REAL,
    non_targeted_runtime_s          REAL,
    targeted_faults                 INTEGER,
    non_targeted_faults             INTEGER,
    targeted_fault_share_pct        REAL,
    targeted_sim_coverage           REAL,
    non_targeted_sim_coverage       REAL,
    sim_coverage_delta              REAL,
    sim_fault_count                 INTEGER
);

-- ---------------------------------------------------------------------------
-- Sequential ATPG: one row per engine run (deterministic / llm_assisted)
-- ---------------------------------------------------------------------------

CREATE TABLE seq_atpg_runs (
    id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                 UUID     NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind                   engine_run_kind NOT NULL CHECK (kind IN ('deterministic', 'llm_assisted')),

    -- parameters
    time_budget_s          REAL,
    max_frames             INTEGER,
    random_sequences       INTEGER,
    parameters             JSONB,                       -- full SequentialAnalysis.parameters

    -- results
    total_faults           INTEGER,
    detected_faults        INTEGER,
    undetected_faults      INTEGER,
    aborted_faults         INTEGER,
    podem_exhausted_faults INTEGER,
    not_confirmed_faults   INTEGER,
    not_attempted_faults   INTEGER,
    fault_coverage         REAL,
    sequence_count         INTEGER,
    total_clock_cycles     INTEGER,
    max_sequence_length    INTEGER,
    stopped_by             TEXT,                        -- 'complete', 'time_budget', 'cancelled'
    phases                 JSONB,                       -- per-phase faults/time

    -- cost
    extraction_s           REAL,
    llm_s                  REAL,
    atpg_s                 REAL,
    total_s                REAL,

    UNIQUE (run_id, kind)
);

CREATE TABLE seq_comparisons (
    run_id                      UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    baseline_coverage           REAL,
    llm_coverage                REAL,
    coverage_delta              REAL,
    additional_faults_detected  INTEGER,
    additional_sequences        INTEGER,
    additional_clock_cycles     INTEGER,
    ran_in_parallel             BOOLEAN
);

-- ---------------------------------------------------------------------------
-- Per-fault outcomes (optional: large designs produce hundreds of thousands
-- of rows; the recorder may store only non-detected faults for them)
-- ---------------------------------------------------------------------------

CREATE TABLE fault_results (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    comb_run_id     BIGINT REFERENCES comb_atpg_runs(id) ON DELETE CASCADE,
    seq_run_id      BIGINT REFERENCES seq_atpg_runs(id)  ON DELETE CASCADE,
    gate            TEXT     NOT NULL,
    stuck_at        SMALLINT NOT NULL CHECK (stuck_at IN (0, 1)),
    status          TEXT     NOT NULL,  -- detected, redundant, aborted, undetected, podem_exhausted, not_confirmed, not_attempted
    detected_by     INTEGER,            -- pattern / sequence index
    CHECK ((comb_run_id IS NULL) <> (seq_run_id IS NULL))
);

CREATE INDEX fault_results_comb_idx ON fault_results (comb_run_id, status);
CREATE INDEX fault_results_seq_idx  ON fault_results (seq_run_id, status);

-- ---------------------------------------------------------------------------
-- LLM usage: one row per run per pipeline stage
-- ---------------------------------------------------------------------------

CREATE TABLE llm_usage (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              UUID     NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage               TEXT     NOT NULL,              -- 'gate_targeting', 'circuit_analysis', 'vector_generation', ...
    provider            TEXT,
    model               TEXT,
    api_calls           INTEGER  NOT NULL DEFAULT 0,
    cached_hits         INTEGER  NOT NULL DEFAULT 0,
    failed_calls        INTEGER  NOT NULL DEFAULT 0,
    fallback_key_used   BOOLEAN  NOT NULL DEFAULT FALSE,
    prompt_tokens       INTEGER,
    response_tokens     INTEGER,
    total_tokens        INTEGER,
    tokens_exact        BOOLEAN  NOT NULL DEFAULT FALSE, -- vendor-reported vs estimated
    duration_s          REAL,
    last_error          TEXT,
    UNIQUE (run_id, stage)
);

-- ---------------------------------------------------------------------------
-- Trojan detection verdicts
-- ---------------------------------------------------------------------------

CREATE TABLE trojan_verdicts (
    run_id              UUID     PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    verdict             TEXT     NOT NULL,              -- 'no_trojan_indicated', 'trojan_suspected', ...
    confidence          REAL     NOT NULL,
    suspect_count       INTEGER  NOT NULL DEFAULT 0,
    summary             TEXT,
    caveats             JSONB,
    -- scored against circuits.trojan_label / trojan_gates when ground truth exists
    ground_truth_label  trojan_label,
    verdict_correct     BOOLEAN,
    true_positive_gates INTEGER,
    suspect_precision   REAL,
    suspect_recall      REAL
);

CREATE TABLE suspect_gates (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              UUID     NOT NULL REFERENCES trojan_verdicts(run_id) ON DELETE CASCADE,
    rank                INTEGER  NOT NULL,
    gate                TEXT     NOT NULL,
    confidence          REAL     NOT NULL,
    evidence            TEXT     NOT NULL,              -- 'structural', 'llm', 'atpg', 'combined'
    atpg_status         TEXT,
    reasons             JSONB,
    activating_vectors  INTEGER,
    is_trojan_gate      BOOLEAN,                        -- NULL when ground truth unknown
    UNIQUE (run_id, rank)
);

-- ---------------------------------------------------------------------------
-- Other run types
-- ---------------------------------------------------------------------------

CREATE TABLE trojan_generations (
    run_id               UUID     PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    variants_requested   INTEGER  NOT NULL,
    variants_generated   INTEGER  NOT NULL,
    strategy             TEXT     NOT NULL,
    seed                 BIGINT,
    trigger_inputs       INTEGER,
    trigger_type         TEXT,
    payload_type         TEXT,
    verify_activation    BOOLEAN  NOT NULL DEFAULT FALSE,
    activation_verified  INTEGER                        -- variants whose trigger was shown to fire
);

CREATE TABLE ranking_runs (
    run_id               UUID     PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    ranker_name          TEXT     NOT NULL,             -- 'scoap', 'rare_node', 'llm', 'random'
    k                    INTEGER,
    gate_count           INTEGER  NOT NULL,
    execution_s          REAL,
    top_gates            JSONB                          -- first k {gate_id, score, rank}
);

CREATE TABLE benchmark_jobs (
    run_id               UUID     PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    dataset_dir          TEXT     NOT NULL,
    ranker_name          TEXT     NOT NULL,
    k                    INTEGER,
    status               TEXT     NOT NULL,             -- 'queued', 'running', 'done', 'failed'
    circuits_evaluated   INTEGER,
    metrics              JSONB                          -- aggregated precision/recall/hit@k
);

CREATE TABLE feature_extractions (
    run_id               UUID     PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    gate_count           INTEGER,
    feature_count        INTEGER,
    toggle_patterns      INTEGER,
    extraction_s         REAL
);

-- Downloadable files a run produced (the files themselves stay on disk)
CREATE TABLE artifacts (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id               UUID     NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    engine_kind          engine_run_kind,               -- which sub-run, if any
    artifact_key         TEXT     NOT NULL,             -- 'test_vectors', 'fault_log', 'bench', ...
    filename             TEXT     NOT NULL,
    size_bytes           BIGINT,
    deleted_at           TIMESTAMPTZ                    -- set when the workspace is pruned
);

-- ---------------------------------------------------------------------------
-- Views for the common questions
-- ---------------------------------------------------------------------------

CREATE VIEW v_run_overview AS
SELECT r.id, r.received_at, r.run_type, r.outcome, r.duration_s, r.queue_wait_s,
       c.display_name AS circuit, c.source_suite, c.benchmark_family,
       c.trojan_label, c.circuit_kind, c.design_level, c.gates, c.flip_flops,
       r.mode, r.use_llm, r.llm_provider, r.llm_model,
       r.fault_coverage, r.patterns, r.llm_api_calls, r.llm_total_tokens,
       r.trojan_verdict, r.hosted
FROM runs r LEFT JOIN circuits c ON c.id = r.circuit_id;

-- Does the LLM help? Combinational and sequential side by side.
CREATE VIEW v_llm_effect AS
SELECT r.id AS run_id, c.display_name AS circuit, c.circuit_kind, r.llm_provider, r.llm_model,
       cc.non_targeted_coverage AS baseline_coverage,
       cc.targeted_circuit_wide_coverage AS llm_coverage,
       cc.sim_coverage_delta AS coverage_delta,
       cc.pattern_reduction_pct
FROM runs r JOIN circuits c ON c.id = r.circuit_id
JOIN comb_comparisons cc ON cc.run_id = r.id
UNION ALL
SELECT r.id, c.display_name, c.circuit_kind, r.llm_provider, r.llm_model,
       sc.baseline_coverage, sc.llm_coverage, sc.coverage_delta, NULL
FROM runs r JOIN circuits c ON c.id = r.circuit_id
JOIN seq_comparisons sc ON sc.run_id = r.id;

-- Trojan detection accuracy where ground truth is known.
CREATE VIEW v_trojan_accuracy AS
SELECT c.source_suite, c.benchmark_family, r.llm_provider, r.llm_model,
       count(*)                                         AS runs,
       avg(CASE WHEN v.verdict_correct THEN 1 ELSE 0 END) AS accuracy,
       avg(v.suspect_precision)                         AS mean_precision,
       avg(v.suspect_recall)                            AS mean_recall
FROM trojan_verdicts v
JOIN runs r     ON r.id = v.run_id
JOIN circuits c ON c.id = r.circuit_id
WHERE v.ground_truth_label IS NOT NULL AND v.ground_truth_label <> 'unknown'
GROUP BY 1, 2, 3, 4;

COMMIT;
