import { Target, AlertTriangle, CheckCircle2, ShieldQuestion } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAppStore } from '@/store';
import { DataTable } from '@/components/tables/DataTable';
import { createColumnHelper } from '@tanstack/react-table';

interface ResultRow {
  rank: number;
  gate_id: string;
  score: number;
  reason?: string;
  atpg_verdict: string;
  toggle_rate?: number;
  cc0?: number;
  cc1?: number;
  co?: number;
}

export default function Results() {
  const { activeCircuit, activeRanking, activeFeatureSet, activeAtpgReport } = useAppStore();

  if (!activeRanking) {
    return (
      <div className="p-8 max-w-4xl mx-auto w-full">
        <div className="flex items-center gap-4 mb-8">
          <Target className="w-8 h-8 text-primary" />
          <h1 className="text-3xl font-bold tracking-tight">Results</h1>
        </div>
        <div className="bg-card border border-border rounded-lg p-8 text-center text-muted-foreground">
          <p>No ranking results yet. Results combines your ranker's suspect list with ATPG verification once both have run.</p>
          <div className="flex items-center justify-center gap-4 mt-4">
            <Link to="/circuits" className="text-primary hover:underline text-sm">1. Load Circuit</Link>
            <Link to="/features" className="text-primary hover:underline text-sm">2. Extract Features</Link>
            <Link to="/ranking" className="text-primary hover:underline text-sm">3. Run Ranking</Link>
          </div>
        </div>
      </div>
    );
  }

  const featureLookup = new Map((activeFeatureSet?.gate_features || []).map((gf) => [gf.gate_id, gf]));
  const heatmap = activeAtpgReport?.heatmap || {};

  const rows: ResultRow[] = activeRanking.ranked_gates.map((g) => {
    const gf = featureLookup.get(g.gate_id);
    const h = heatmap[g.gate_id];
    return {
      rank: g.rank,
      gate_id: g.gate_id,
      score: g.score,
      reason: g.reason,
      atpg_verdict: h?.verdict ?? 'NOT_RUN',
      toggle_rate: gf?.toggle_rate,
      cc0: gf?.cc0,
      cc1: gf?.cc1,
      co: gf?.co,
    };
  });

  const columnHelper = createColumnHelper<ResultRow>();
  const columns = [
    columnHelper.accessor('rank', { header: 'Rank' }),
    columnHelper.accessor('gate_id', {
      header: 'Gate ID',
      cell: info => <span className="font-mono text-primary">{info.getValue()}</span>
    }),
    columnHelper.accessor('score', {
      header: 'Ranker Score',
      cell: info => <span className="font-mono">{info.getValue().toFixed(4)}</span>
    }),
    columnHelper.accessor('atpg_verdict', {
      header: 'ATPG Verdict',
      cell: info => <VerdictBadge verdict={info.getValue()} />
    }),
    columnHelper.accessor('toggle_rate', {
      header: 'Toggle Rate',
      cell: info => info.getValue() !== undefined ? info.getValue()!.toFixed(4) : '—'
    }),
    columnHelper.accessor('cc0', { header: 'CC0', cell: info => info.getValue() ?? '—' }),
    columnHelper.accessor('cc1', { header: 'CC1', cell: info => info.getValue() ?? '—' }),
    columnHelper.accessor('co', { header: 'CO', cell: info => info.getValue() ?? '—' }),
    columnHelper.accessor('reason', {
      header: 'Reason',
      cell: info => <span className="text-muted-foreground italic text-xs">{info.getValue() || '-'}</span>
    }),
  ];

  const trojanCount = rows.filter(r => r.atpg_verdict === 'TROJAN_CANDIDATE').length;

  return (
    <div className="h-full flex flex-col">
      <div className="h-14 border-b border-border bg-card flex items-center justify-between px-6 shrink-0">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-bold text-primary flex items-center gap-2">
            <Target className="w-5 h-5" /> Results
          </h2>
          {activeCircuit && (
            <span className="text-sm font-mono text-muted-foreground border-l border-border pl-4">
              {activeCircuit.name} • {activeRanking.ranker_name}
            </span>
          )}
        </div>
        {!activeAtpgReport && (
          <span className="text-xs text-yellow-500 flex items-center gap-1">
            <ShieldQuestion className="w-3.5 h-3.5" /> ATPG not run — verdicts unavailable. <Link to="/atpg" className="underline">Run ATPG</Link>
          </span>
        )}
        {activeAtpgReport && (
          <span className="text-xs text-muted-foreground">
            {trojanCount} of {rows.length} ranked gates flagged as Trojan candidates by ATPG cross-reference
          </span>
        )}
      </div>

      <div className="flex-1 overflow-hidden p-4 bg-background">
        <div className="h-full flex flex-col rounded-lg border border-border bg-card overflow-hidden">
          <DataTable columns={columns} data={rows} className="h-full border-none rounded-none" />
        </div>
      </div>
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  if (verdict === 'TROJAN_CANDIDATE') {
    return <span className="text-xs text-red-400 font-medium bg-red-500/10 px-2 py-1 rounded flex items-center gap-1 w-fit"><AlertTriangle className="w-3 h-3" /> Trojan Candidate</span>;
  }
  if (verdict === 'CLEARED') {
    return <span className="text-xs text-green-500 font-medium bg-green-500/10 px-2 py-1 rounded flex items-center gap-1 w-fit"><CheckCircle2 className="w-3 h-3" /> Cleared</span>;
  }
  if (verdict === 'NOT_RUN') {
    return <span className="text-xs text-muted-foreground font-medium bg-secondary px-2 py-1 rounded w-fit">Not Run</span>;
  }
  return <span className="text-xs text-yellow-500 font-medium bg-yellow-500/10 px-2 py-1 rounded flex items-center gap-1 w-fit"><ShieldQuestion className="w-3 h-3" /> No Data</span>;
}
