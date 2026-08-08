import { Zap, Play, Activity, AlertTriangle, CheckCircle2, ShieldQuestion } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';

export default function ATPG() {
  const { activeCircuit, activeCircuitFile, activeAtpgReport, setActiveAtpgReport, defaultPatterns, defaultUseLlm } = useAppStore();
  const [patterns, setPatterns] = useState(defaultPatterns);
  const [useLlm, setUseLlm] = useState(defaultUseLlm);

  const runMutation = useMutation({
    mutationFn: api.runAtpg,
    onSuccess: (data) => setActiveAtpgReport(data),
  });

  const handleRun = () => {
    if (activeCircuitFile) {
      runMutation.mutate({ file: activeCircuitFile, patterns, use_llm: useLlm });
    }
  };

  const report = activeAtpgReport;

  return (
    <div className="p-8 max-w-4xl mx-auto w-full">
      <div className="flex items-center gap-4 mb-8">
        <Zap className="w-8 h-8 text-primary" />
        <h1 className="text-3xl font-bold tracking-tight">ATPG Engine (ATALANTA)</h1>
      </div>

      <div className="bg-card border border-border p-6 rounded-lg shadow-sm flex flex-col gap-6">
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div>
            <h3 className="font-semibold text-lg">Active Circuit</h3>
            <p className="text-muted-foreground text-sm font-mono mt-1">
              {activeCircuit
                ? activeCircuit.name
                : <>No circuit loaded. Please <Link to="/circuits" className="text-primary hover:underline">load a circuit</Link> first.</>}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-6">
          <div>
            <label className="block text-sm font-medium mb-2">Toggle Simulation Patterns</label>
            <input
              type="number"
              value={patterns}
              onChange={(e) => setPatterns(parseInt(e.target.value) || 0)}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Gate Prioritization</label>
            <select
              value={useLlm ? 'llm' : 'heuristic'}
              onChange={(e) => setUseLlm(e.target.value === 'llm')}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            >
              <option value="llm">Gemini (LLM few-shot)</option>
              <option value="heuristic">Offline heuristic (no API key)</option>
            </select>
          </div>
        </div>

        <button
          onClick={handleRun}
          disabled={!activeCircuitFile || runMutation.isPending}
          className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-md font-medium transition-colors disabled:opacity-50"
        >
          {runMutation.isPending
            ? <Activity className="w-4 h-4 animate-spin" />
            : <Play className="w-4 h-4 fill-current" />}
          {runMutation.isPending ? 'Running (Atalanta + AI prioritizer)...' : 'Run ATALANTA'}
        </button>

        {runMutation.isError && (
          <div className="p-4 rounded-md border border-red-500/50 bg-red-500/10 text-red-400 text-sm font-mono flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {(runMutation.error as any)?.response?.data?.detail || (runMutation.error as any)?.message || 'ATPG run failed'}
          </div>
        )}
      </div>

      {report && (
        <div className="mt-6 flex flex-col gap-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Baseline Coverage" value={report.baseline_coverage ? `${report.baseline_coverage.coverage_pct}%` : '—'} />
            <Stat label="Baseline Patterns" value={report.baseline_coverage ? String(report.baseline_coverage.test_patterns) : '—'} />
            <Stat label="Targeted Coverage" value={report.targeted_coverage ? `${report.targeted_coverage.coverage_pct}%` : 'N/A'} />
            <Stat label="Pattern Reduction" value={report.comparison ? `${report.comparison.pattern_reduction_pct}%` : 'N/A'} />
          </div>

          {report.note && (
            <div className="p-4 rounded-md border border-yellow-500/40 bg-yellow-500/10 text-yellow-500 text-sm">
              {report.note}
            </div>
          )}

          <div className="bg-card border border-border rounded-lg p-6">
            <h3 className="font-semibold text-lg mb-1">AI-Flagged Suspects ({report.ai_suspects.length})</h3>
            <p className="text-xs text-muted-foreground mb-4">
              {report.used_llm ? 'Prioritized via Gemini few-shot reasoning' : 'Prioritized via offline SCOAP/toggle heuristic'}
            </p>
            {report.ai_suspects.length === 0 ? (
              <p className="text-sm text-muted-foreground">No suspicious gates flagged for this circuit.</p>
            ) : (
              <div className="space-y-2">
                {report.ai_suspects.map((gid) => {
                  const h = report.heatmap[gid];
                  return (
                    <div key={gid} className="flex items-center justify-between border-b border-border pb-2 last:border-0">
                      <span className="font-mono text-sm text-primary">{gid}</span>
                      <VerdictBadge verdict={h?.verdict ?? 'NO_DATA'} />
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {report.trojan_candidates.length > 0 && (
            <div className="bg-card border border-red-500/40 rounded-lg p-6">
              <h3 className="font-semibold text-lg mb-4 text-red-400 flex items-center gap-2">
                <AlertTriangle className="w-5 h-5" /> Trojan Candidates
              </h3>
              <div className="space-y-3">
                {report.trojan_candidates.map((c) => (
                  <div key={c.gate_id} className="text-sm">
                    <span className="font-mono text-red-400 font-bold">{c.gate_id}</span>
                    <span className="text-muted-foreground ml-2">{c.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border bg-card p-4 rounded-lg flex flex-col gap-1">
      <span className="text-xs text-muted-foreground font-medium">{label}</span>
      <span className="text-xl font-bold">{value}</span>
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  if (verdict === 'TROJAN_CANDIDATE') {
    return <span className="text-xs text-red-400 font-medium bg-red-500/10 px-2 py-1 rounded flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> Trojan Candidate</span>;
  }
  if (verdict === 'CLEARED') {
    return <span className="text-xs text-green-500 font-medium bg-green-500/10 px-2 py-1 rounded flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> Cleared</span>;
  }
  return <span className="text-xs text-yellow-500 font-medium bg-yellow-500/10 px-2 py-1 rounded flex items-center gap-1"><ShieldQuestion className="w-3 h-3" /> No Data</span>;
}
