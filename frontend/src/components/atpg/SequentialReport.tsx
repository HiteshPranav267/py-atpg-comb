import { useState } from 'react';
import { Cpu, Download, GitCompareArrows } from 'lucide-react';
import type {
  CpuTimeBreakdown,
  SequentialAnalysisData,
  SequentialComparison,
  SequentialSummary,
  SystemInfo,
} from '@/types';
import { api } from '@/services/api';
import { KeyValue, Paginator, Pill, Section, Stat, metric } from './primitives';

const ROWS_PER_PAGE = 25;

type SeqRunKey = 'deterministic' | 'llm_assisted';

const RUN_LABELS: Record<SeqRunKey, string> = {
  deterministic: 'Deterministic only',
  llm_assisted: 'LLM-assisted',
};

const PHASE_LABELS: Record<string, string> = {
  random_fill: 'Random fill',
  podem: 'PODEM (per fault, time-frame expansion)',
  extended_search: 'Extended search (natural fault order)',
  llm_guided: 'LLM-guided search (LLM fault order)',
};

const STATUS_LABELS: Record<string, string> = {
  podem_exhausted: 'PODEM exhausted',
  aborted: 'Aborted',
  test_not_confirmed: 'Test not confirmed',
  not_attempted: 'Not attempted',
};

const STOP_LABELS: Record<string, string> = {
  target_coverage: 'Target coverage reached',
  time_budget: 'Time budget reached',
  max_sequences: 'Sequence budget reached',
  search_exhausted: 'Every phase ran to completion',
};

const PARAMETER_LABELS: Record<string, string> = {
  target_coverage: 'Target coverage (%)',
  max_sequences: 'Max test sequences',
  base_cycles: 'Random sequence length (cycles)',
  max_cycles: 'Max sequence length (cycles)',
  random_pool_size: 'Random candidates',
  podem_max_frames: 'PODEM max unrolled frames',
  podem_max_backtracks: 'PODEM backtrack limit',
  llm_activation_threshold: 'LLM activation threshold (faults)',
  rng_seed: 'Random seed',
  time_budget_s: 'Search time budget (s)',
  deterministic_share: 'Time share for deterministic phases',
  extended_search: 'Extended search without a ranker',
};

/** Renders one test cycle's vector as "A=1 B=0 ...". */
function formatVector(vector: Record<string, number>): string {
  return Object.entries(vector)
    .map(([pi, v]) => `${pi}=${v}`)
    .join(' ');
}

export function SequentialReport({
  sequential,
  jobId,
  cpuTime,
  systemInfo,
}: {
  sequential: SequentialSummary;
  jobId: string;
  cpuTime?: CpuTimeBreakdown;
  systemInfo?: SystemInfo;
}) {
  const comparison = sequential.comparison ?? null;
  const runs: Partial<Record<SeqRunKey, SequentialAnalysisData>> = comparison
    ? { deterministic: comparison.baseline, llm_assisted: comparison.llm_assisted }
    : { deterministic: sequential.analysis };
  const runKeys = Object.keys(runs) as SeqRunKey[];
  const [activeRun, setActiveRun] = useState<SeqRunKey>(runKeys[runKeys.length - 1]);
  const current = runs[activeRun] ?? runs[runKeys[0]]!;

  const artifacts = Object.entries(sequential.artifacts ?? {}).filter(([key]) =>
    key.startsWith(`${activeRun}_`)
  );

  return (
    <div className="flex flex-col gap-6">
      {/* When a comparison ran, it's the headline result. */}
      {comparison && <SequentialComparisonPanel comparison={comparison} />}

      {runKeys.length > 1 && (
        <div className="flex gap-2 border-b border-border">
          {runKeys.map((key) => {
            const active = key === activeRun;
            return (
              <button
                key={key}
                onClick={() => setActiveRun(key)}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  active
                    ? 'border-primary text-primary'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              >
                {RUN_LABELS[key]}
              </button>
            );
          })}
        </div>
      )}

      <RunSummary
        run={current}
        runKey={activeRun}
        cpuTime={cpuTime?.by_run[activeRun]}
        systemInfo={systemInfo}
      />
      <SearchPhases run={current} />
      <EngineParameters run={current} />
      {activeRun === 'llm_assisted' && sequential.llm_usage?.enabled && (
        <LlmUsagePanel llm_usage={sequential.llm_usage} />
      )}
      <FaultAnalysis run={current} />
      <SequenceTable run={current} />
      <Artifacts entries={artifacts} jobId={jobId} />

      {current.notes.length > 0 && (
        <Section title="Engine notes">
          <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1">
            {current.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function RunSummary({
  run,
  runKey,
  cpuTime,
  systemInfo,
}: {
  run: SequentialAnalysisData;
  runKey: SeqRunKey;
  cpuTime?: { atpg_s: number | null; extraction_s: number; llm_s: number; total_s: number; note: string };
  systemInfo?: SystemInfo;
}) {
  const r = run.results;
  const t = run.timing ?? {};
  return (
    <Section
      title="Sequential ATPG summary"
      subtitle="No-scan model: flip-flops reset to 0, stimulus only at primary inputs, responses observed only at primary outputs. Every number is from the engine's own deterministic fault simulation of the final test set."
      actions={
        <div className="flex items-center gap-2">
          {run.stopped_by && (
            <Pill tone={run.stopped_by === 'time_budget' ? 'warn' : 'neutral'}>
              {STOP_LABELS[run.stopped_by] ?? run.stopped_by}
            </Pill>
          )}
          <Pill tone="info">{RUN_LABELS[runKey]}</Pill>
        </div>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat
          label="Fault coverage"
          value={metric(r.fault_coverage, { suffix: ' %' })}
          hint="of the full checkpoint fault list"
          emphasis
        />
        <Stat label="Total faults" value={r.total_faults} hint="stuck-at-0/1 on every gate output, used input and flip-flop Q" />
        <Stat label="Detected faults" value={r.detected_faults} />
        <Stat label="Undetected faults" value={r.undetected_faults} />
        <Stat label="PODEM exhausted" value={r.podem_exhausted_faults} hint="no test found; not a redundancy proof" />
        <Stat label="Aborted faults" value={r.aborted_faults} hint="backtrack limit hit" />
        <Stat label="Test not confirmed" value={r.not_confirmed_faults} hint="PODEM test failed from reset" />
        <Stat label="Not attempted" value={r.not_attempted_faults} hint="budget ran out first" />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
        <Stat label="Test sequences" value={r.sequence_count} />
        <Stat label="Total clock cycles" value={r.total_clock_cycles} />
        <Stat label="Longest sequence (cycles)" value={r.max_sequence_length} />
        <Stat label="Search time" value={metric(t.search_s, { suffix: ' s' })} />
      </div>

      {cpuTime && (
        <div className="mt-4 border-t border-border pt-4">
          <div className="flex items-center gap-2 text-sm font-medium mb-3">
            <Cpu className="w-4 h-4 text-primary" /> Reported CPU time
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Parse + profiling" value={metric(cpuTime.extraction_s, { suffix: ' s' })} />
            <Stat label="LLM" value={metric(cpuTime.llm_s, { suffix: ' s' })} hint={runKey === 'llm_assisted' ? 'circuit summary + fault ranking calls' : 'no LLM consulted'} />
            <Stat label="ATPG engine" value={metric(cpuTime.atpg_s, { suffix: ' s' })} hint="search + final fault simulation" />
            <Stat label="Total" value={metric(cpuTime.total_s, { suffix: ' s' })} emphasis />
          </div>
          <p className="text-xs text-muted-foreground mt-2">{cpuTime.note}</p>
        </div>
      )}

      {systemInfo && (
        <p className="text-xs text-muted-foreground mt-3 border-t border-border pt-3 font-mono">
          Measured on: {systemInfo.cpu_model} · {systemInfo.logical_cores ?? '?'} logical core
          {systemInfo.logical_cores === 1 ? '' : 's'} · {systemInfo.os} · Python {systemInfo.python_version}
        </p>
      )}
    </Section>
  );
}

function SearchPhases({ run }: { run: SequentialAnalysisData }) {
  const phases = run.phases ?? [];
  if (phases.length === 0) return null;
  return (
    <Section
      title="Search phases"
      subtitle="Candidates each phase proposed, how many the fault simulator accepted (a sequence is kept only if it detects at least one new fault), and what they contributed."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground border-b border-border">
              <th className="py-2 pr-4 font-medium">Phase</th>
              <th className="py-2 pr-4 font-medium">Candidates</th>
              <th className="py-2 pr-4 font-medium">Accepted</th>
              <th className="py-2 pr-4 font-medium">Faults detected</th>
              <th className="py-2 pr-4 font-medium">Time</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {phases.map((p) => (
              <tr key={p.phase} className="border-b border-border/50">
                <td className="py-2 pr-4">{PHASE_LABELS[p.phase] ?? p.phase}</td>
                <td className="py-2 pr-4">{p.candidates}</td>
                <td className="py-2 pr-4">{p.accepted}</td>
                <td className="py-2 pr-4 text-primary">{p.faults_detected}</td>
                <td className="py-2 pr-4">{metric(p.seconds, { suffix: ' s' })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function EngineParameters({ run }: { run: SequentialAnalysisData }) {
  const params = run.parameters ?? {};
  const rows = Object.entries(params).map(
    ([key, value]) =>
      [PARAMETER_LABELS[key] ?? key, value === null ? 'unlimited' : String(value)] as [string, string]
  );
  if (rows.length === 0) return null;
  return (
    <Section title="ATPG parameters" subtitle="The engine settings this run actually used.">
      <KeyValue rows={rows} />
    </Section>
  );
}

function FaultAnalysis({ run }: { run: SequentialAnalysisData }) {
  const [page, setPage] = useState(0);
  const details = run.fault_details ?? run.undetected.map((f) => ({
    fault: f, node: f.split('/')[0], node_type: '-', stuck_at: Number(f.split('/')[1]), status: '-',
  }));
  const pageCount = Math.ceil(details.length / ROWS_PER_PAGE);
  const rows = details.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);
  const descriptions = run.outcome_descriptions ?? {};

  return (
    <Section
      title={`Fault analysis (${details.length.toLocaleString()} undetected)`}
      subtitle="Every fault the final test set does not detect, with why the engine gave up on it. A fault's absence means it was detected."
    >
      {Object.keys(descriptions).length > 0 && (
        <dl className="grid md:grid-cols-2 gap-x-6 gap-y-2 text-xs mb-4">
          {Object.entries(descriptions).map(([status, text]) => (
            <div key={status}>
              <dt className="font-medium text-yellow-500">{STATUS_LABELS[status] ?? status}</dt>
              <dd className="text-muted-foreground leading-relaxed">{text}</dd>
            </div>
          ))}
        </dl>
      )}
      {details.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Every fault in the checkpoint fault list was detected.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground border-b border-border">
                  <th className="py-2 pr-4 font-medium">Fault</th>
                  <th className="py-2 pr-4 font-medium">Net</th>
                  <th className="py-2 pr-4 font-medium">Driver</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {rows.map((f) => (
                  <tr key={f.fault} className="border-b border-border/50">
                    <td className="py-1.5 pr-4">{f.fault}</td>
                    <td className="py-1.5 pr-4 text-primary">{f.node}</td>
                    <td className="py-1.5 pr-4">{f.node_type}</td>
                    <td className="py-1.5 pr-4">stuck-at-{f.stuck_at}</td>
                    <td className="py-1.5">
                      <span className="text-yellow-500">{STATUS_LABELS[f.status] ?? f.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator page={page} pageCount={pageCount} total={details.length} onPage={setPage} />
        </>
      )}
    </Section>
  );
}

function SequentialComparisonPanel({ comparison }: { comparison: SequentialComparison }) {
  const { baseline, llm_assisted } = comparison;
  const deltaPositive = (comparison.coverage_delta ?? 0) > 0;

  return (
    <Section
      title="Deterministic-only vs LLM-assisted"
      subtitle="Same random seed and same search effort: both runs do random fill and PODEM identically, then an extra deep-search phase with the same budget -- in natural fault order for the deterministic run, in the LLM's order for the other. The difference below is what the LLM's ordering contributed."
      actions={<GitCompareArrows className="w-4 h-4 text-muted-foreground" />}
    >
      <div className="grid grid-cols-2 gap-4">
        <div className="border border-border rounded-lg p-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Deterministic only
          </span>
          <div className="text-2xl font-bold tabular-nums mt-1">
            {metric(baseline.results.fault_coverage, { suffix: ' %' })}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {baseline.results.detected_faults} / {baseline.results.total_faults} faults ·{' '}
            {baseline.results.sequence_count} sequence(s) · {baseline.results.total_clock_cycles} cycles
          </div>
        </div>
        <div className="border border-primary/40 bg-primary/5 rounded-lg p-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-primary">
            LLM-assisted
          </span>
          <div className="text-2xl font-bold tabular-nums mt-1 text-primary">
            {metric(llm_assisted.results.fault_coverage, { suffix: ' %' })}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {llm_assisted.results.detected_faults} / {llm_assisted.results.total_faults} faults ·{' '}
            {llm_assisted.results.sequence_count} sequence(s) · {llm_assisted.results.total_clock_cycles} cycles
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
        <Stat
          label="Coverage delta"
          value={
            comparison.coverage_delta !== null
              ? `${comparison.coverage_delta > 0 ? '+' : ''}${comparison.coverage_delta} %`
              : null
          }
          emphasis={deltaPositive}
        />
        <Stat label="Additional faults detected" value={comparison.additional_faults_detected.length} emphasis={deltaPositive} />
        <Stat label="Additional sequences" value={comparison.additional_sequences} />
        <Stat label="Additional clock cycles" value={comparison.additional_clock_cycles} />
      </div>

      {comparison.additional_faults_detected.length > 0 && (
        <div className="mt-4 pt-4 border-t border-border">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            Faults only the LLM-assisted run detected
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {comparison.additional_faults_detected.map((fault) => (
              <span key={fault} className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-secondary text-primary">
                {fault}
              </span>
            ))}
          </div>
        </div>
      )}

      {comparison.notes.length > 0 && (
        <ul className="list-disc list-inside text-xs text-muted-foreground space-y-1 mt-4 pt-4 border-t border-border">
          {comparison.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function LlmUsagePanel({ llm_usage }: { llm_usage: SequentialSummary['llm_usage'] }) {
  return (
    <Section
      title="LLM-assisted fault prioritization"
      subtitle="LLM-1 (once, cached) summarised the circuit; LLM-2 ordered the faults the deterministic phases left undetected, with a compact per-call context -- never the full circuit or fault list."
      actions={<Cpu className="w-4 h-4 text-muted-foreground" />}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="API calls" value={llm_usage.api_calls ?? 0} />
        <Stat label="Cached hits" value={llm_usage.cached_hits ?? 0} />
        <Stat label="Prompt tokens (est.)" value={llm_usage.prompt_tokens_est ?? 0} />
        <Stat label="Response tokens (est.)" value={llm_usage.response_tokens_est ?? 0} />
      </div>
      {llm_usage.circuit_summary && (
        <p className="text-sm text-muted-foreground mt-4 leading-relaxed border-t border-border pt-4">
          {llm_usage.circuit_summary}
        </p>
      )}
    </Section>
  );
}

function SequenceTable({ run }: { run: SequentialAnalysisData }) {
  const sequences = run.sequences;
  const newlyDetected = run.newly_detected_by_sequence;
  const [page, setPage] = useState(0);
  const pageCount = Math.ceil(sequences.length / ROWS_PER_PAGE);
  const rows = sequences
    .map((seq, i) => ({ seq, i }))
    .slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);

  return (
    <Section
      title={`Test sequences (${sequences.length.toLocaleString()})`}
      subtitle="Each row is one independent multi-cycle sequence, applied from reset. Every sequence shown detected at least one new fault -- this is the test set the engine kept, not every candidate it tried."
    >
      {sequences.length === 0 ? (
        <p className="text-sm text-muted-foreground">No test sequences were generated for this run.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground border-b border-border">
                  <th className="py-2 pr-4 font-medium w-12">#</th>
                  <th className="py-2 pr-4 font-medium">Origin</th>
                  <th className="py-2 pr-4 font-medium w-20">Cycles</th>
                  <th className="py-2 pr-4 font-medium">Vectors (one per cycle)</th>
                  <th className="py-2 font-medium">Newly detected faults</th>
                </tr>
              </thead>
              <tbody className="font-mono text-xs align-top">
                {rows.map(({ seq, i }) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className="py-1.5 pr-4 text-muted-foreground">{i}</td>
                    <td className="py-1.5 pr-4 text-primary">{seq.origin}</td>
                    <td className="py-1.5 pr-4">{seq.cycles}</td>
                    <td className="py-1.5 pr-4 break-all">
                      {seq.vectors.map((v, cycle) => (
                        <div key={cycle}>{formatVector(v)}</div>
                      ))}
                    </td>
                    <td className="py-1.5 break-all text-muted-foreground">
                      {(newlyDetected[i] ?? []).join(', ') || '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator page={page} pageCount={pageCount} total={sequences.length} onPage={setPage} />
        </>
      )}
    </Section>
  );
}

const ARTIFACT_LABELS: Record<string, string> = {
  sequences: 'Test sequences',
  undetected: 'Undetected faults (CSV)',
  report: 'Full run report (JSON)',
};

function Artifacts({ entries, jobId }: { entries: Array<[string, string]>; jobId: string }) {
  return (
    <Section title="Artifacts" subtitle="Files written for this run, downloadable as-is.">
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No artifacts were retained for this run.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {entries.map(([key, filename]) => (
            <a
              key={key}
              href={api.atpgArtifactUrl(jobId, filename)}
              download={filename}
              className="flex items-center gap-2 text-sm px-3 py-1.5 border border-border rounded-md hover:bg-secondary transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              {ARTIFACT_LABELS[key.split('_').slice(-1)[0]] ?? key}
              <span className="text-[11px] text-muted-foreground font-mono">{filename}</span>
            </a>
          ))}
        </div>
      )}
    </Section>
  );
}
