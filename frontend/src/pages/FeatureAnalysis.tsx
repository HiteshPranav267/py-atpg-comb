import { useRef } from 'react';
import { Play, Database, LayoutGrid } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import { DataTable } from '@/components/tables/DataTable';
import { createColumnHelper } from '@tanstack/react-table';
import type { GateFeature } from '@/types';

export default function FeatureAnalysis() {
  const { activeFeatureSet, setActiveFeatureSet } = useAppStore();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const extractMutation = useMutation({
    mutationFn: api.extractFeatures,
    onSuccess: (data) => {
      setActiveFeatureSet(data);
    }
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      extractMutation.mutate(file);
    }
  };

  const columnHelper = createColumnHelper<GateFeature>();
  
  const columns = [
    columnHelper.accessor('gate_id', {
      header: 'Gate ID',
      cell: info => <span className="font-bold text-primary">{info.getValue()}</span>
    }),
    columnHelper.accessor('gate_type', { header: 'Type' }),
    columnHelper.accessor('role', { header: 'Role' }),
    columnHelper.accessor('level', { header: 'Level' }),
    columnHelper.accessor('fanin', { header: 'Fan-In' }),
    columnHelper.accessor('fanout', { header: 'Fan-Out' }),
    columnHelper.accessor('pi_distance', { header: 'PI Dist' }),
    columnHelper.accessor('po_distance', { header: 'PO Dist' }),
    columnHelper.accessor('is_trojan_gate', { 
      header: 'Is Trojan',
      cell: info => info.getValue() ? <span className="text-red-400 font-bold">YES</span> : 'No'
    }),
  ];

  const data = activeFeatureSet?.gate_features || [];

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="h-14 border-b border-border bg-card flex items-center justify-between px-6 shrink-0">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-bold text-primary flex items-center gap-2">
            <LayoutGrid className="w-5 h-5" /> Feature Extraction
          </h2>
          {activeFeatureSet && (
            <span className="text-sm font-mono text-muted-foreground border-l border-border pl-4">
              Time: {activeFeatureSet.extraction_time_ms.toFixed(2)} ms
            </span>
          )}
        </div>
        <div>
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleFileUpload}
            className="hidden" 
            accept=".v,.verilog"
          />
          <button 
            onClick={() => fileInputRef.current?.click()}
            disabled={extractMutation.isPending}
            className="flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-50"
          >
            <Play className="w-4 h-4 fill-current" /> 
            {extractMutation.isPending ? 'Extracting...' : 'Extract Features (Upload)'}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden p-4 bg-background">
        <div className="h-full flex flex-col rounded-lg border border-border bg-card overflow-hidden">
          <div className="p-3 border-b border-border bg-secondary/30 flex items-center gap-2">
            <Database className="w-4 h-4 text-muted-foreground" />
            <span className="text-sm font-medium">Extracted Gate Features</span>
            {activeFeatureSet && (
              <span className="ml-auto text-xs text-muted-foreground font-mono bg-background px-2 py-1 rounded">
                Total Gates: {data.length}
              </span>
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
