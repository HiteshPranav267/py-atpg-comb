import { FileText, Download } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAppStore } from '@/store';

export default function Reports() {
  const { activeCircuit, activeFeatureSet, activeRanking, activeAtpgReport, benchmarkHistory } = useAppStore();

  const hasAnything = activeCircuit || activeFeatureSet || activeRanking || activeAtpgReport || benchmarkHistory.length > 0;

  const handleDownload = () => {
    const report = {
      generated_at: new Date().toISOString(),
      // Pulled to the top level, not just nested in atpg_report, so it's the
      // first thing visible when comparing timing numbers across exports
      // taken on different machines.
      system_info: activeAtpgReport?.system_info ?? null,
      circuit: activeCircuit,
      feature_extraction: activeFeatureSet ? {
        gate_count: activeFeatureSet.gate_features.length,
        extraction_time_ms: activeFeatureSet.extraction_time_ms,
        atpg_coverage: activeFeatureSet.atpg_coverage,
      } : null,
      ranking: activeRanking ? {
        ranker_name: activeRanking.ranker_name,
        execution_time: activeRanking.execution_time,
        top_10: activeRanking.ranked_gates.slice(0, 10),
      } : null,
      atpg_report: activeAtpgReport,
      benchmark_history: benchmarkHistory,
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `pyatpg_report_${(activeCircuit?.name || 'session').replace(/\W+/g, '_')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="p-8 max-w-4xl mx-auto w-full">
      <div className="flex items-center gap-4 mb-8">
        <FileText className="w-8 h-8 text-primary" />
        <h1 className="text-3xl font-bold tracking-tight">Reports</h1>
      </div>

      {!hasAnything ? (
        <div className="bg-card border border-border rounded-lg p-8 text-center text-muted-foreground">
          <p>Nothing to report yet. Load a circuit and run the pipeline first.</p>
          <Link to="/circuits" className="text-primary hover:underline text-sm mt-2 inline-block">Go to Circuits</Link>
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <div className="bg-card border border-border rounded-lg p-6">
            <h3 className="font-semibold text-lg mb-4">Session Summary</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <SummaryRow label="Circuit" value={activeCircuit?.name ?? '—'} />
              <SummaryRow label="Gates" value={activeCircuit ? String(activeCircuit.gates) : '—'} />
              <SummaryRow label="Features Extracted" value={activeFeatureSet ? String(activeFeatureSet.gate_features.length) : 'Not run'} />
              <SummaryRow label="Extraction Time" value={activeFeatureSet ? `${activeFeatureSet.extraction_time_ms.toFixed(1)}ms` : '—'} />
              <SummaryRow label="Ranker Used" value={activeRanking?.ranker_name ?? 'Not run'} />
              <SummaryRow label="Gates Ranked" value={activeRanking ? String(activeRanking.ranked_gates.length) : '—'} />
              <SummaryRow label="ATPG Mode" value={activeAtpgReport?.mode_label ?? 'Not run'} />
              <SummaryRow
                label="Fault Coverage"
                value={(() => {
                  if (!activeAtpgReport) return '—';
                  const run =
                    activeAtpgReport.runs.non_targeted ?? activeAtpgReport.runs.targeted;
                  const coverage = run?.results.fault_coverage;
                  return coverage === null || coverage === undefined
                    ? 'Not reported by the ATPG tool'
                    : `${coverage} %`;
                })()}
              />
              <SummaryRow label="Benchmark Runs (session)" value={String(benchmarkHistory.length)} />
              <SummaryRow
                label="CPU (this run)"
                value={
                  activeAtpgReport?.system_info
                    ? `${activeAtpgReport.system_info.cpu_model} (${activeAtpgReport.system_info.logical_cores ?? '?'} cores)`
                    : 'Not run'
                }
              />
            </div>
          </div>

          {activeRanking && (
            <div className="bg-card border border-border rounded-lg p-6">
              <h3 className="font-semibold text-lg mb-4">Top 5 Suspects</h3>
              <ol className="space-y-2 text-sm font-mono">
                {activeRanking.ranked_gates.slice(0, 5).map((g) => (
                  <li key={g.gate_id} className="flex justify-between border-b border-border pb-2 last:border-0">
                    <span>#{g.rank} <span className="text-primary">{g.gate_id}</span></span>
                    <span className="text-muted-foreground">score {g.score.toFixed(4)}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          <button
            onClick={handleDownload}
            className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2.5 rounded-md text-sm font-medium transition-colors w-fit"
          >
            <Download className="w-4 h-4" /> Download Full Report (JSON)
          </button>
        </div>
      )}
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
