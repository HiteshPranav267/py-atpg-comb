import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, AlertTriangle, Brain, Cpu, Play, Zap } from 'lucide-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import type { AnalysisMode, AnalysisModeOption, AtpgReport, TargetingInfo } from '@/types';
import { Section, Stat, Pill, KeyValue, metric } from '@/components/atpg/primitives';
import { RunReport } from '@/components/atpg/RunReport';
import { SequentialReport } from '@/components/atpg/SequentialReport';
import {
  CircuitAnalysisPanel,
  ComparisonPanel,
  CorrelationPanel,
  LlmUsagePanel,
  VerdictPanel,
} from '@/components/atpg/AnalysisPanels';
import { TrojanCheckPanel } from '@/components/atpg/TrojanCheckPanel';

/** Fallback list if /atpg/modes is unreachable; labels match the backend. */
const FALLBACK_MODES: AnalysisModeOption[] = [
  {
    value: 'targeted',
    label: 'LLM Heuristic (Targeted)',
    description: 'Select gates, then run the ATPG tool against only those faults.',
  },
  {
    value: 'non_targeted',
    label: 'ATPG Tool (Non-Targeted)',
    description: "Run the ATPG tool over the circuit's full collapsed fault list.",
  },
  { value: 'compare', label: 'Compare Both', description: 'Run both and compare them.' },
];

type RunKey = 'targeted' | 'non_targeted';

export default function ATPG() {
  const { activeCircuit, activeCircuitFile, activeAtpgReport, setActiveAtpgReport, defaultPatterns } =
    useAppStore();
  const [mode, setMode] = useState<AnalysisMode>('compare');
  const [patterns, setPatterns] = useState(defaultPatterns);
  const [fullScan, setFullScan] = useState(true);

  const { data: modes = FALLBACK_MODES } = useQuery({
    queryKey: ['atpg-modes'],
    queryFn: api.getAnalysisModes,
    staleTime: Infinity,
    retry: false,
    placeholderData: FALLBACK_MODES,
  });

  const [progressId, setProgressId] = useState<string | null>(null);

  const runMutation = useMutation({
    mutationFn: api.runAtpg,
    onSuccess: (data) => setActiveAtpgReport(data),
  });

  // Follow the run while it is in flight, so a long analysis shows what it
  // is doing instead of a spinner that looks hung.
  const { data: progress } = useQuery({
    queryKey: ['atpg-progress', progressId],
    queryFn: () => api.getAtpgProgress(progressId as string),
    enabled: runMutation.isPending && !!progressId,
    refetchInterval: 1500,
    retry: false,
  });

  const handleRun = () => {
    if (activeCircuitFile) {
      const id =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      setProgressId(id);
      runMutation.mutate({
        file: activeCircuitFile,
        mode,
        patterns,
        use_llm: true,
        full_scan: fullScan,
        progress_id: id,
      });
    }
  };

  const report = activeAtpgReport;
  const selected = modes.find((m) => m.value === mode);
  const errorMsg = runMutation.isError
    ? (runMutation.error as any)?.response?.data?.detail ||
      (runMutation.error as any)?.message ||
      'ATPG run failed'
    : null;

  return (
    <div className="p-8 max-w-6xl mx-auto w-full flex flex-col gap-6">
      <div className="flex items-center gap-4">
        <Zap className="w-8 h-8 text-primary" />
        <div>
          <h1 className="text-3xl font-bold tracking-tight">ATPG Engine</h1>
          <p className="text-sm text-muted-foreground">
            Test pattern generation for combinational and sequential circuits alike — the right
            engine is selected automatically based on whether the circuit has flip-flops. Every
            metric shown is read from the engine's own output.
          </p>
        </div>
      </div>

      <div className="bg-card border border-border p-6 rounded-lg flex flex-col gap-6">
        <div className="border-b border-border pb-4">
          <h3 className="font-semibold">Active circuit</h3>
          <p className="text-muted-foreground text-sm font-mono mt-1">
            {activeCircuit ? (
              activeCircuit.name
            ) : (
              <>
                No circuit loaded.{' '}
                <Link to="/circuits" className="text-primary hover:underline">
                  Load a circuit
                </Link>{' '}
                first.
              </>
            )}
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          <div>
            <label className="block text-sm font-medium mb-2">Analysis mode</label>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as AnalysisMode)}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            >
              {modes.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground mt-2 leading-relaxed italic">
              For circuits with flip-flops: Non-Targeted runs deterministic-only. Targeted and
              Compare both enable LLM assistance (needs an API key in Settings) and show a
              deterministic-vs-LLM-assisted comparison side by side.
            </p>
            {selected && (
              <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
                {selected.description}
              </p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Toggle simulation patterns</label>
            <input
              type="number"
              min={1}
              value={patterns}
              onChange={(e) => setPatterns(Math.max(1, parseInt(e.target.value, 10) || 1))}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            />
            <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
              Used for structural toggle-rate profiling only. The ATPG tool chooses its own
              pattern counts.
            </p>
          </div>

          <div className="md:col-span-2">
            <label className="block text-sm font-medium mb-2">Flip-flop model</label>
            <select
              value={fullScan ? 'full_scan' : 'sequential'}
              onChange={(e) => setFullScan(e.target.value === 'full_scan')}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            >
              <option value="full_scan">Full scan (every flip-flop is a scan cell)</option>
              <option value="sequential">No scan (sequential engine, from reset)</option>
            </select>
            <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
              Only affects circuits with flip-flops. Full scan treats each flip-flop output as a
              pseudo-input and its input as a pseudo-output, then runs combinational ATPG — the
              industry-standard model, fast even on large designs. No scan reaches state only by
              clocking from reset and observes only the real outputs: far harder, slower, and
              lower coverage.
            </p>
          </div>
        </div>

        <button
          onClick={handleRun}
          disabled={!activeCircuitFile || runMutation.isPending}
          className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-md font-medium transition-colors disabled:opacity-50"
        >
          {runMutation.isPending ? (
            <Activity className="w-4 h-4 animate-spin" />
          ) : (
            <Play className="w-4 h-4 fill-current" />
          )}
          {runMutation.isPending ? 'Running ATPG...' : 'Run analysis'}
        </button>

        {runMutation.isPending && progress && !progress.done && (
          <div className="flex flex-col gap-1.5">
            <div className="text-sm text-muted-foreground flex items-center gap-2" aria-live="polite">
              <Activity className="w-3.5 h-3.5 animate-spin shrink-0" />
              <span>
                {progress.stage}
                <span className="font-mono text-xs ml-2">
                  {formatSeconds(progress.stage_elapsed_s)} in this step ·{' '}
                  {formatSeconds(progress.elapsed_s)} total
                </span>
              </span>
            </div>
            {progress.thought && (
              <div className="ml-5 pl-3 border-l-2 border-primary/40 flex gap-2 items-start">
                <Brain className="w-3.5 h-3.5 text-primary/70 shrink-0 mt-0.5" />
                <p
                  key={progress.thought}
                  className="llm-thought text-xs italic text-muted-foreground truncate min-w-0"
                  title="What the model is reasoning about right now"
                >
                  {progress.thought}
                </p>
              </div>
            )}
          </div>
        )}

        {errorMsg && (
          <div className="p-4 rounded-md border border-red-500/50 bg-red-500/10 text-red-400 text-sm font-mono flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span className="whitespace-pre-wrap">{errorMsg}</span>
          </div>
        )}
      </div>

      {report && <ReportView report={report} />}
    </div>
  );
}

function formatSeconds(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
}

function ReportView({ report }: { report: AtpgReport }) {
  const runKeys = Object.keys(report.runs) as RunKey[];
  const [activeRun, setActiveRun] = useState<RunKey>(
    runKeys.includes('non_targeted') ? 'non_targeted' : runKeys[0]
  );
  const current = report.runs[activeRun] ?? report.runs[runKeys[0]];

  if (report.sequential) {
    return (
      <div className="flex flex-col gap-6">
        <CircuitOverview report={report} />
        {report.notes.length > 0 && (
          <div className="flex flex-col gap-2">
            {report.notes.map((note) => (
              <p key={note} className="text-xs text-muted-foreground bg-secondary/50 px-3 py-2 rounded-md">
                {note}
              </p>
            ))}
          </div>
        )}
        <SequentialReport
          sequential={report.sequential}
          jobId={report.job_id}
          cpuTime={report.cpu_time}
          systemInfo={report.system_info}
        />
        {report.verdict && <VerdictPanel verdict={report.verdict} />}
        <TrojanCheckPanel report={report} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <CircuitOverview report={report} />

      {report.comparison && <ComparisonPanel comparison={report.comparison} />}

      {runKeys.length > 1 && (
        <div className="flex gap-2 border-b border-border">
          {runKeys.map((key) => {
            const run = report.runs[key];
            if (!run) return null;
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
                {run.label}
              </button>
            );
          })}
        </div>
      )}

      {current && (
        <RunReport
          run={current}
          jobId={report.job_id}
          cpuTime={report.cpu_time?.by_run[activeRun]}
          systemInfo={report.system_info}
        />
      )}

      {report.verdict && <VerdictPanel verdict={report.verdict} />}
        <TrojanCheckPanel report={report} />
      {report.circuit_analysis && <CircuitAnalysisPanel analysis={report.circuit_analysis} />}
      {report.correlation && <CorrelationPanel correlation={report.correlation} />}
      {report.llm_usage?.enabled && (
        <LlmUsagePanel
          usage={report.llm_usage}
          targeting={'gate_count' in report.targeting ? (report.targeting as TargetingInfo) : null}
        />
      )}
    </div>
  );
}

function CircuitOverview({ report }: { report: AtpgReport }) {
  const c = report.circuit;
  const targeting = 'gate_count' in report.targeting ? report.targeting : null;

  return (
    <Section
      title="Circuit overview"
      subtitle={
        <>
          Parsed from <span className="font-mono">{c.filename}</span> ({c.format}). Job{' '}
          <span className="font-mono">{report.job_id}</span>.
        </>
      }
      actions={
        <div className="flex items-center gap-2">
          <Cpu className="w-4 h-4 text-muted-foreground" />
          <Pill tone="info">{report.mode_label}</Pill>
        </div>
      }
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Primary inputs" value={c.primary_inputs} />
        <Stat label="Primary outputs" value={c.primary_outputs} />
        <Stat label="Gates" value={c.gates} />
        <Stat label="Logic depth" value={c.logic_depth} />
      </div>

      <div className="grid md:grid-cols-2 gap-x-8 mt-5">
        <KeyValue
          rows={[
            ['Total nodes', metric(c.total_nodes)],
            ['Total edges', metric(c.total_edges)],
            ['Max fan-in', metric(c.max_fan_in)],
            ['Max fan-out', metric(c.max_fan_out)],
            ['Average fan-in', metric(c.avg_fan_in)],
            ['Average fan-out', metric(c.avg_fan_out)],
          ]}
        />
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
            Gate types
          </h4>
          <div className="flex flex-wrap gap-2">
            {Object.entries(c.gate_types ?? {}).map(([type, count]) => (
              <span
                key={type}
                className="text-xs px-2 py-1 rounded border border-border bg-secondary font-mono"
              >
                {type} <span className="text-primary">{count}</span>
              </span>
            ))}
          </div>
        </div>
      </div>

      {targeting && (
        <div className="mt-5 pt-4 border-t border-border">
          <div className="flex items-center gap-2 mb-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Fault targeting
            </h4>
            <Pill tone={targeting.used_llm ? 'info' : 'neutral'}>
              {targeting.used_llm ? 'LLM selection' : 'Structural heuristic'}
            </Pill>
          </div>
          <p className="text-sm text-muted-foreground">
            {targeting.gate_count} gate(s) selected for the targeted fault list.
          </p>
          <div className="flex flex-wrap gap-1.5 mt-3">
            {targeting.gates.map((gate) => {
              // Highlight gates the structural detector flagged as
              // trojan-shaped: those are the ones worth looking at first.
              const flagged = targeting.structural_suspects?.includes(gate);
              return (
                <span
                  key={gate}
                  title={flagged ? 'Flagged as trojan-shaped by the structural detector' : undefined}
                  className={[
                    'font-mono text-[11px] px-1.5 py-0.5 rounded',
                    flagged
                      ? 'bg-red-500/15 text-red-400 ring-1 ring-red-500/40'
                      : 'bg-secondary text-primary',
                  ].join(' ')}
                >
                  {gate}
                </span>
              );
            })}
          </div>
          {targeting.suspect_reasons && targeting.suspect_reasons.length > 0 && (
            <div className="mt-3 p-3 rounded-md border border-red-500/40 bg-red-500/10">
              <p className="text-xs font-medium text-red-400 mb-1.5">
                Structurally trojan-shaped logic detected
              </p>
              <SuspectReasons reasons={targeting.suspect_reasons} />
            </div>
          )}
          {targeting.toggle_seed != null && (
            <p className="text-[11px] text-muted-foreground mt-2 font-mono">
              ranking stimulus: seed {targeting.toggle_seed}
              {targeting.toggle_patterns ? `, ${targeting.toggle_patterns} patterns` : ''}
            </p>
          )}
          {targeting.notes.map((note) => (
            <p key={note} className="text-xs text-yellow-500 mt-2 leading-relaxed">
              {note}
            </p>
          ))}
        </div>
      )}
    </Section>
  );
}

/**
 * Detector reasons, one line per distinct reason ("14 gates: part of the
 * 14-gate side tree ..."), the first few shown. One line per gate ran to
 * hundreds of near-identical rows on a large circuit.
 */
function SuspectReasons({ reasons }: { reasons: string[] }) {
  const [showAll, setShowAll] = useState(false);
  const groups = new Map<string, string[]>();
  for (const entry of reasons) {
    const cut = entry.indexOf(': ');
    const gate = cut > 0 ? entry.slice(0, cut) : entry;
    const why = cut > 0 ? entry.slice(cut + 2) : '';
    groups.set(why, [...(groups.get(why) ?? []), gate]);
  }
  const rows = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  const shown = showAll ? rows : rows.slice(0, 5);
  return (
    <div className="flex flex-col gap-1">
      {shown.map(([why, gates]) => (
        <p key={why} className="text-[11px] text-red-300/90 leading-relaxed">
          <span className="font-mono">
            {gates.length === 1 ? gates[0] : `${gates.length} gates (${gates.slice(0, 3).join(', ')}${gates.length > 3 ? ', ...' : ''})`}
          </span>
          {why && `: ${why}`}
        </p>
      ))}
      {rows.length > 5 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="self-start text-[11px] text-red-400 hover:underline"
        >
          {showAll ? 'Show fewer' : `Show all ${rows.length} reasons`}
        </button>
      )}
    </div>
  );
}
