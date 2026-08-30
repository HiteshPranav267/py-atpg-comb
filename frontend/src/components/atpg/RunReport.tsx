import { useState } from 'react';
import { Download } from 'lucide-react';
import type { AtpgRun } from '@/types';
import { api } from '@/services/api';
import { KeyValue, Paginator, Pill, Section, Stat, metric } from './primitives';

const ROWS_PER_PAGE = 25;

/** Human labels for the artifact keys the backend emits. */
const ARTIFACT_LABELS: Record<string, string> = {
  bench: 'BENCH netlist',
  fault_list: 'Fault list',
  test_vectors: 'Test vectors',
  undetected_faults: 'Undetected faults',
  log: 'ATPG tool log',
  stderr: 'ATPG tool stderr',
};

export function RunReport({ run, jobId }: { run: AtpgRun; jobId: string }) {
  return (
    <div className="flex flex-col gap-6">
      <AtpgSummary run={run} />
      <CircuitAsRead run={run} />
      <RunParameters run={run} />
      <FaultTable run={run} />
      <VectorTable run={run} />
      <Artifacts run={run} jobId={jobId} />
      <RawOutput run={run} />
    </div>
  );
}

function AtpgSummary({ run }: { run: AtpgRun }) {
  const r = run.results;
  return (
    <Section
      title="ATPG summary"
      subtitle={
        <>
          Read directly from {run.tool} {run.version ?? ''} output. Values the tool did not
          report are marked rather than defaulted to zero.
        </>
      }
      actions={<Pill tone={run.targeted ? 'info' : 'neutral'}>{run.targeted ? 'Targeted' : 'Full fault list'}</Pill>}
    >
      {run.targeted ? (
        <p className="text-xs text-yellow-500/90 border border-yellow-500/30 bg-yellow-500/5 rounded-md px-3 py-2 mb-4">
          Coverage below is measured over this run's <strong>targeted fault list</strong>
          {r.collapsed_faults != null ? ` (${r.collapsed_faults.toLocaleString()} collapsed faults)` : ''}, not
          over the whole circuit. A short fault list is easy to detect in full, so a high percentage
          here does not mean the circuit is well tested.
        </p>
      ) : null}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {/* The denominator differs between a targeted and a full run, so the
            label has to say which one this is -- an unqualified "Fault
            coverage" of 99% reads as whole-circuit coverage either way. */}
        <Stat
          label={run.targeted ? 'Fault coverage (targeted list)' : 'Fault coverage'}
          value={metric(r.fault_coverage, { suffix: ' %' })}
          hint={run.targeted ? 'of the targeted faults, not the circuit' : 'of the full collapsed fault list'}
          emphasis
        />
        <Stat label="Patterns before compaction" value={r.patterns_before_compaction} />
        <Stat label="Patterns after compaction" value={r.patterns_after_compaction} />
        <Stat
          label="Collapsed faults"
          value={r.collapsed_faults}
          hint={run.targeted ? 'the coverage denominator' : undefined}
        />
        <Stat label="Detected faults" value={r.detected_faults} hint="collapsed − redundant − aborted" />
        <Stat label="Redundant faults" value={r.redundant_faults} />
        <Stat label="Aborted faults" value={r.aborted_faults} hint="unfinished; 0 means the run completed" />
        <Stat label="Backtrackings" value={r.backtrackings} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mt-4">
        <Stat label="Initialization" value={metric(run.timing.initialization, { suffix: ' s' })} />
        <Stat label="Fault simulation" value={metric(run.timing.fault_simulation, { suffix: ' s' })} />
        <Stat label="FAN" value={metric(run.timing.fan, { suffix: ' s' })} />
        <Stat label="Total CPU" value={metric(run.timing.total, { suffix: ' s' })} />
        <Stat label="Memory used" value={metric(run.memory_mb, { suffix: ' MB' })} />
      </div>
    </Section>
  );
}

function CircuitAsRead({ run }: { run: AtpgRun }) {
  const c = run.circuit;
  return (
    <Section
      title="Circuit structure (as read by the ATPG tool)"
      subtitle="The ATPG tool's own view of the netlist, kept separate from our parser's view so any discrepancy stays visible."
    >
      <KeyValue
        rows={[
          ['Name', metric(c.name)],
          ['Primary inputs', metric(c.primary_inputs)],
          ['Primary outputs', metric(c.primary_outputs)],
          ['Gates', metric(c.gates)],
          ['Logic depth', metric(c.logic_depth)],
        ]}
      />
    </Section>
  );
}

function RunParameters({ run }: { run: AtpgRun }) {
  const p = run.parameters;
  return (
    <Section title="ATPG parameters" subtitle="The settings this run actually used.">
      <KeyValue
        rows={[
          ['Generation mode', metric(p.generation_mode)],
          ['Random pattern limit (packets)', metric(p.random_pattern_limit)],
          ['Backtrack limit', metric(p.backtrack_limit)],
          ['Backtrack limit (phase 1)', metric(p.backtrack_limit_phase1)],
          ['Backtrack limit (phase 2)', metric(p.backtrack_limit_phase2)],
          ['Random seed', metric(p.random_seed)],
          ['Compaction mode', metric(p.compaction_mode)],
          ['Shuffling compaction limit', metric(p.shuffling_compaction_limit)],
          ['Number of shuffles', metric(p.shuffles)],
        ]}
      />
      <p className="text-xs text-muted-foreground mt-4 font-mono break-all">
        $ {run.command.join(' ')}
      </p>
    </Section>
  );
}

function FaultTable({ run }: { run: AtpgRun }) {
  const [page, setPage] = useState(0);
  const faults = run.faults;
  const pageCount = Math.ceil(faults.length / ROWS_PER_PAGE);
  const rows = faults.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);

  return (
    <Section
      title={`Fault analysis (${faults.length.toLocaleString()} undetected)`}
      subtitle="The tool's undetected-fault report lists only faults it could not detect. A gate's absence means its faults were detected."
    >
      {faults.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          The ATPG tool detected every fault in this run's fault list — the undetected-fault report is empty.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground border-b border-border">
                  <th className="py-2 pr-4 font-medium">Fault</th>
                  <th className="py-2 pr-4 font-medium">Gate / net</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {rows.map((f) => (
                  <tr key={f.fault_name} className="border-b border-border/50">
                    <td className="py-1.5 pr-4">{f.fault_name}</td>
                    <td className="py-1.5 pr-4 text-primary">{f.gate}</td>
                    <td className="py-1.5 pr-4">{f.fault_type}</td>
                    <td className="py-1.5">
                      <span className="text-yellow-500">{f.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator page={page} pageCount={pageCount} total={faults.length} onPage={setPage} />
        </>
      )}
    </Section>
  );
}

function VectorTable({ run }: { run: AtpgRun }) {
  const [page, setPage] = useState(0);
  const vectors = run.test_vectors.vectors;
  const pageCount = Math.ceil(vectors.length / ROWS_PER_PAGE);
  const rows = vectors.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);

  return (
    <Section
      title={`Test patterns (${run.test_vectors.count.toLocaleString()})`}
      subtitle={`${run.test_vectors.primary_inputs.length} primary inputs, ${run.test_vectors.primary_outputs.length} primary outputs. Each row is one generated pattern and its fault-free response.`}
    >
      {vectors.length === 0 ? (
        <p className="text-sm text-muted-foreground">This run produced no test vector file.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground border-b border-border">
                  <th className="py-2 pr-4 font-medium w-16">#</th>
                  <th className="py-2 pr-4 font-medium">Input vector</th>
                  <th className="py-2 font-medium">Output response</th>
                </tr>
              </thead>
              <tbody className="font-mono text-xs">
                {rows.map((v) => (
                  <tr key={v.index} className="border-b border-border/50">
                    <td className="py-1.5 pr-4 text-muted-foreground">{v.index}</td>
                    <td className="py-1.5 pr-4 break-all">{v.inputs}</td>
                    <td className="py-1.5 text-primary break-all">{v.outputs}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator page={page} pageCount={pageCount} total={vectors.length} onPage={setPage} />
        </>
      )}
    </Section>
  );
}

function Artifacts({ run, jobId }: { run: AtpgRun; jobId: string }) {
  const entries = Object.entries(run.artifacts);
  return (
    <Section
      title="Artifacts"
      subtitle="Raw files exactly as the ATPG tool wrote them. Nothing is regenerated for download."
    >
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
              {ARTIFACT_LABELS[key] ?? key}
              <span className="text-[11px] text-muted-foreground font-mono">{filename}</span>
            </a>
          ))}
        </div>
      )}
    </Section>
  );
}

function RawOutput({ run }: { run: AtpgRun }) {
  const [open, setOpen] = useState(false);
  return (
    <Section
      title="Raw ATPG tool output"
      subtitle="The unparsed stdout this report was derived from."
      actions={
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-xs px-3 py-1.5 border border-border rounded-md hover:bg-secondary"
        >
          {open ? 'Hide' : 'Show'}
        </button>
      }
    >
      {open ? (
        <pre className="text-[11px] font-mono bg-background border border-border rounded p-4 overflow-x-auto max-h-[28rem] overflow-y-auto whitespace-pre">
          {run.raw_log || '(no stdout captured)'}
        </pre>
      ) : (
        <p className="text-sm text-muted-foreground">
          {run.raw_log.split('\n').length.toLocaleString()} lines captured.
        </p>
      )}
    </Section>
  );
}
