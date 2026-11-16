import { useState, useEffect } from 'react';

import { Trophy, PlayCircle, Hash } from 'lucide-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import { DataTable } from '@/components/tables/DataTable';
import { createColumnHelper } from '@tanstack/react-table';
import type { RankedGate } from '@/types';

export default function Ranking() {
  const { activeFeatureSet, activeRanking, setActiveRanking } = useAppStore();
  const [selectedRanker, setSelectedRanker] = useState<string>('');

  const { data: rankers = [] } = useQuery({
    queryKey: ['rankers'],
    queryFn: api.getRankers,
  });

  useEffect(() => {
    if (rankers.length > 0 && !selectedRanker) {
      setSelectedRanker(rankers[0]);
    }
  }, [rankers, selectedRanker]);

  const rankMutation = useMutation({
    mutationFn: api.runRanking,
    onSuccess: (data) => {
      setActiveRanking(data);
    }
  });

  const handleRank = () => {
    if (activeFeatureSet && selectedRanker) {
      rankMutation.mutate({
        feature_set: activeFeatureSet,
        ranker_name: selectedRanker
      });
    }
  };

  const columnHelper = createColumnHelper<RankedGate>();
  
  const columns = [
    columnHelper.accessor('rank', {
      header: 'Rank',
      cell: info => (
        <span className="flex items-center gap-1.5 font-bold text-accent-foreground">
          <Hash className="w-3 h-3" /> {info.getValue()}
        </span>
      )
    }),
    columnHelper.accessor('gate_id', {
      header: 'Gate ID',
      cell: info => <span className="font-mono text-primary">{info.getValue()}</span>
    }),
    columnHelper.accessor('score', { 
      header: 'Score',
      cell: info => <span className="font-mono">{info.getValue().toFixed(4)}</span>
    }),
    columnHelper.accessor('reason', { 
      header: 'Reason',
      cell: info => <span className="text-muted-foreground italic text-xs">{info.getValue() || '-'}</span>
    }),
  ];

  const data = activeRanking?.ranked_gates || [];

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="h-14 border-b border-border bg-card flex items-center justify-between px-6 shrink-0">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-bold text-primary flex items-center gap-2">
            <Trophy className="w-5 h-5" /> Ranking Engine
          </h2>
          {activeRanking && (
            <span className="text-sm font-mono text-muted-foreground border-l border-border pl-4">
              Time: {activeRanking.execution_time.toFixed(2)} ms
            </span>
          )}
        </div>
        
        <div className="flex items-center gap-4">
          <select 
            value={selectedRanker}
            onChange={(e) => setSelectedRanker(e.target.value)}
            className="bg-background border border-border text-foreground text-sm rounded-md px-3 py-1.5 focus:outline-none focus:border-primary"
          >
            <option value="" disabled>Select Ranker</option>
            {rankers.map(r => <option key={r} value={r}>{r}</option>)}
          </select>

          <button 
            onClick={handleRank}
            disabled={!activeFeatureSet || !selectedRanker || rankMutation.isPending}
            className="flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-50"
          >
            <PlayCircle className="w-4 h-4" /> 
            {rankMutation.isPending ? 'Ranking...' : 'Run Ranker'}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden p-4 bg-background">
        <div className="h-full flex flex-col rounded-lg border border-border bg-card overflow-hidden">
          <div className="p-3 border-b border-border bg-secondary/30 flex items-center justify-between">
            <span className="text-sm font-medium">Ranked Gates (Suspects)</span>
            {!activeFeatureSet && (
              <span className="text-xs text-red-400">Please extract features first!</span>
            )}
          </div>
          <div className="flex-1 overflow-hidden p-0">
            <DataTable columns={columns} data={data} className="h-full border-none rounded-none" />
          </div>
        </div>
      </div>
    </div>
  );
}
