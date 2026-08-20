import { useState } from 'react';
import { Cpu, GitCompareArrows } from 'lucide-react';
import type { SequentialComparison, SequentialSummary } from '@/types';
import { KeyValue, Paginator, Pill, Section, Stat, metric } from './primitives';

const ROWS_PER_PAGE = 25;

/** Renders one test cycle's vector as "A=1 B=0 ...". */
function formatVector(vector: Record<string, number>): string {
  return Object.entries(vector)
    .map(([pi, v]) => `${pi}=${v}`)
    .join(' ');
}

export function SequentialReport({ sequential }: { sequential: SequentialSummary }) {
  const { analysis, llm_usage } = sequential;
  const r = analysis.results;

  return (
    <div className="flex flex-col gap-6">
      <Section
        title="Sequential ATPG summary"
        subtitle="Time-frame-expansion engine (PODEM search + a deterministic, reset-driven multi-cycle fault simulator). No flip-flop-bearing circuit reaches a combinational-only engine."
        actions={<Pill tone="info">Sequential</Pill>}
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="Fault coverage" value={metric(r.fault_coverage, { suffix: ' %' })} emphasis />
          <Stat
            label="Detected / total faults"
            value={
              r.detected_faults !== null && r.total_faults !== null
                ? `${r.detected_faults} / ${r.total_faults}`
                : null
            }
          />
          <Stat label="Undetected faults" value={r.undetected_faults} />
          <Stat label="Aborted faults" value={r.aborted_faults} />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
          <Stat label="Test sequences" value={r.sequence_count} />
          <Stat label="Total clock cycles" value={r.total_clock_cycles} />
          <Stat label="Longest sequence (cycles)" value={r.max_sequence_length} />
        </div>
      </Section>

      {sequential.comparison && <SequentialComparisonPanel comparison={sequential.comparison} />}

      {llm_usage?.enabled && <LlmUsagePanel llm_usage={llm_usage} />}

      <SequenceTable sequential={sequential} />
      <FaultLists analysis={analysis} />

      {analysis.notes.length > 0 && (
        <Section title="Notes">
          <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1">
            {analysis.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function SequentialComparisonPanel({ comparison }: { comparison: SequentialComparison }) {
  const { baseline, llm_assisted } = comparison;
  const deltaPositive = (comparison.coverage_delta ?? 0) > 0;

  return (
    <Section
      title="Deterministic-only vs LLM-assisted"
      subtitle="Both runs use the same random seed, so random-fill and PODEM search identical candidates first -- only the LLM-2-assisted phase (and the deeper local search it unlocks) can differ between the two columns below."
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
      subtitle="LLM-1 (once, cached) summarised the circuit; LLM-2 was consulted only for faults the deterministic phases (random-fill + PODEM) left undetected, with a compact per-call context -- never the full circuit or fault list."
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

function SequenceTable({ sequential }: { sequential: SequentialSummary }) {
  const sequences = sequential.analysis.sequences;
  const newlyDetected = sequential.analysis.newly_detected_by_sequence;
  const [page, setPage] = useState(0);
  const pageCount = Math.ceil(sequences.length / ROWS_PER_PAGE);
  const rows = sequences
    .map((seq, i) => ({ seq, i }))
    .slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);

  return (
    <Section
      title={`Test sequences (${sequences.length.toLocaleString()})`}
      subtitle="Each row is one independent multi-cycle sequence, applied from a fresh reset state. Every sequence shown contributed at least one fault detection -- this is the minimal test set the engine found, not every candidate it tried."
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

function FaultLists({ analysis }: { analysis: SequentialSummary['analysis'] }) {
  const [page, setPage] = useState(0);
  const undetected = analysis.undetected;
  const pageCount = Math.ceil(undetected.length / ROWS_PER_PAGE);
  const rows = undetected.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);

  return (
    <Section
      title={`Undetected faults (${undetected.length.toLocaleString()})`}
      subtitle="Faults the deterministic simulator could not confirm detected within this run's sequence/fault budget."
    >
      <KeyValue
        rows={[
          ['Detected', metric(analysis.detected.length)],
          ['Undetected', metric(undetected.length)],
        ]}
      />
      {undetected.length === 0 ? (
        <p className="text-sm text-muted-foreground mt-4">
          Every fault in this run's checkpoint fault list was detected.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5 mt-4">
            {rows.map((fault) => (
              <span
                key={fault}
                className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-secondary text-yellow-500"
              >
                {fault}
              </span>
            ))}
          </div>
          <Paginator page={page} pageCount={pageCount} total={undetected.length} onPage={setPage} />
        </>
      )}
    </Section>
  );
}
