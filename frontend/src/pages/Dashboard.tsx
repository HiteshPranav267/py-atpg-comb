import { ShieldAlert, Cpu, Activity, Database } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAppStore } from '@/store';

export default function Dashboard() {
  const { activeCircuit, activeFeatureSet, benchmarkHistory } = useAppStore();

  const avgAccuracy = benchmarkHistory.length > 0
    ? `${(benchmarkHistory.reduce((sum, r) => sum + r.top1_accuracy, 0) / benchmarkHistory.length * 100).toFixed(1)}%`
    : '—';

  return (
    <div className="p-8 max-w-6xl mx-auto w-full">
      <div className="flex items-center gap-4 mb-8">
        <ShieldAlert className="w-8 h-8 text-primary" />
        <h1 className="text-3xl font-bold tracking-tight">PyATPG Dashboard</h1>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-12">
        <Card title="Active Circuit" value={activeCircuit ? `${activeCircuit.gates} gates` : '—'} icon={Cpu} />
        <Card title="Features Extracted" value={activeFeatureSet ? String(activeFeatureSet.gate_features.length) : '—'} icon={Database} />
        <Card title="Last Extraction Time" value={activeFeatureSet ? `${activeFeatureSet.extraction_time_ms.toFixed(1)}ms` : '—'} icon={Activity} />
        <Card title="Avg Benchmark Top-1 Acc" value={avgAccuracy} icon={ShieldAlert} />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <div className="border border-border bg-card rounded-lg p-6 flex flex-col gap-4">
          <h2 className="text-xl font-semibold">Quick Start</h2>
          <p className="text-muted-foreground text-sm">
            Begin analyzing hardware Trojans by loading a Verilog circuit, extracting topological and ATPG testability features, and running the ranking framework.
          </p>
          <div className="flex flex-col gap-2 mt-4">
            <Link to="/circuits" className="bg-secondary text-secondary-foreground hover:bg-secondary/80 px-4 py-2 rounded text-sm font-medium transition-colors text-center">
              1. Load Circuit
            </Link>
            <Link to="/features" className="bg-secondary text-secondary-foreground hover:bg-secondary/80 px-4 py-2 rounded text-sm font-medium transition-colors text-center">
              2. Extract Features
            </Link>
            <Link to="/ranking" className="bg-primary text-primary-foreground hover:bg-primary/90 px-4 py-2 rounded text-sm font-medium transition-colors text-center">
              3. Run Ranking
            </Link>
          </div>
        </div>

        <div className="border border-border bg-card rounded-lg p-6 flex flex-col gap-4">
          <h2 className="text-xl font-semibold">Recent Benchmarks</h2>
          <div className="space-y-4">
            {benchmarkHistory.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No benchmarks run yet. Start one from the{' '}
                <Link to="/benchmark" className="text-primary hover:underline">Benchmark</Link> tab.
              </p>
            )}
            {benchmarkHistory.slice(0, 5).map((r) => (
              <div key={`${r.job_id}-${r.circuit_name}`} className="flex items-center justify-between border-b border-border pb-3 last:border-0">
                <div>
                  <div className="text-sm font-medium">{r.circuit_name}</div>
                  <div className="text-xs text-muted-foreground font-mono">
                    {r.ranker_name} • Top-1: {(r.top1_accuracy * 100).toFixed(0)}%
                  </div>
                </div>
                <span className="text-xs text-green-500 font-medium bg-green-500/10 px-2 py-1 rounded">Completed</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Card({ title, value, icon: Icon }: { title: string, value: string, icon: any }) {
  return (
    <div className="border border-border bg-card p-5 rounded-lg flex flex-col gap-2 shadow-sm">
      <div className="flex items-center justify-between text-muted-foreground">
        <span className="text-sm font-medium">{title}</span>
        <Icon className="w-4 h-4" />
      </div>
      <div className="text-2xl font-bold text-foreground">{value}</div>
    </div>
  );
}
