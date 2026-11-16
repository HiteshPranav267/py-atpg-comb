import { Play, Database, LayoutGrid, AlertTriangle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import { DataTable } from '@/components/tables/DataTable';
import { createColumnHelper } from '@tanstack/react-table';
import type { GateFeature } from '@/types';

export default function FeatureAnalysis() {
  const { activeCircuit, activeCircuitFile, activeFeatureSet, setActiveFeatureSet } = useAppStore();

  const extractMutation = useMutation({
    mutationFn: api.extractFeatures,
    onSuccess: (data) => {
      setActiveFeatureSet(data);
    }
  });

  const handleExtract = () => {
    if (activeCircuitFile) {
      extractMutation.mutate(activeCircuitFile);
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
          {activeCircuit && (
            <span className="text-sm font-mono text-muted-foreground border-l border-border pl-4">
              {activeCircuit.name}
            </span>
          )}
          {activeFeatureSet && (
            <span className="text-sm font-mono text-muted-foreground border-l border-border pl-4">
              Time: {activeFeatureSet.extraction_time_ms.toFixed(2)} ms
            </span>
          )}
        </div>
        <div>
          <button
            onClick={handleExtract}
            disabled={!activeCircuitFile || extractMutation.isPending}
            title={!activeCircuitFile ? 'Upload a circuit in the Circuits tab first' : undefined}
            className="flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-50"
          >
            <Play className="w-4 h-4 fill-current" />
            {extractMutation.isPending ? 'Extracting...' : 'Extract Features'}
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
          <div className="flex-1 overflow-hidden p-0 relative">
            {!activeCircuitFile && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-muted-foreground text-sm">
                <AlertTriangle className="w-6 h-6" />
                <span>No circuit loaded. <Link to="/circuits" className="text-primary hover:underline">Upload one in Circuits</Link> first.</span>
              </div>
            )}
            <DataTable columns={columns} data={data} className="h-full border-none rounded-none" />
          </div>
        </div>
      </div>
    </div>
  );
}
