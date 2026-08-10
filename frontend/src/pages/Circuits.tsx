import { useRef } from 'react';
import Split from 'react-split';
import { UploadCloud, Folder, Cpu } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import { DataTable } from '@/components/tables/DataTable';
import { createColumnHelper } from '@tanstack/react-table';

export default function Circuits() {
  const { activeCircuit, setActiveCircuit } = useAppStore();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMutation = useMutation({
    mutationFn: api.uploadCircuit,
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      uploadMutation.mutate(file, {
        onSuccess: (data) => setActiveCircuit(data, file),
      });
    }
  };

  const columnHelper = createColumnHelper<any>();
  const columns = [
    columnHelper.accessor('name', { header: 'Property' }),
    columnHelper.accessor('value', { header: 'Value' })
  ];

  const data = activeCircuit ? [
    { name: 'Name', value: activeCircuit.name },
    { name: 'Total Nodes', value: activeCircuit.total_nodes },
    { name: 'Gates', value: activeCircuit.gates },
    { name: 'Inputs', value: activeCircuit.primary_inputs },
    { name: 'Outputs', value: activeCircuit.primary_outputs },
    { name: 'Levels', value: activeCircuit.levels },
  ] : [];

  const errorMsg = uploadMutation.isError
    ? ((uploadMutation.error as any)?.response?.data?.detail || (uploadMutation.error as any)?.message || 'Upload failed')
    : null;

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="h-14 border-b border-border bg-card flex items-center justify-between px-6 shrink-0">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-bold text-primary flex items-center gap-2">
            <Cpu className="w-5 h-5" /> Circuits Viewer
          </h2>
          {activeCircuit && (
            <span className="text-sm font-mono text-muted-foreground border-l border-border pl-4">
              {activeCircuit.name}
            </span>
          )}
        </div>
        <div>
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleFileUpload}
            className="hidden"
            accept=".v,.verilog,.bench"
          />
          <button 
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            className="flex items-center gap-2 bg-secondary hover:bg-secondary/80 text-foreground px-4 py-1.5 rounded-md text-sm transition-colors border border-border disabled:opacity-50"
          >
            <UploadCloud className="w-4 h-4" />
            {uploadMutation.isPending ? 'Uploading...' : 'Upload Circuit'}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="px-6 py-2 bg-red-500/10 border-b border-red-500/50 text-red-400 text-sm font-mono shrink-0">
          Error: {errorMsg}
        </div>
      )}

      {/* Split Pane */}
      <Split 
        sizes={[25, 75]} 
        minSize={200}
        expandToMin={false}
        gutterSize={1}
        gutterAlign="center"
        snapOffset={30}
        dragInterval={1}
        direction="horizontal"
        className="flex flex-1 overflow-hidden"
        gutter={() => {
          const div = document.createElement('div')
          div.className = `bg-border hover:bg-primary transition-colors cursor-col-resize w-[2px] h-full relative z-10`
          return div
        }}
      >
        {/* Left Pane - Hierarchy (Placeholder) */}
        <div className="bg-card overflow-y-auto p-4 flex flex-col">
          <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-4">Hierarchy</h3>
          {activeCircuit ? (
            <div className="space-y-2 font-mono text-sm">
              <div className="flex items-center gap-2 text-primary">
                <Folder className="w-4 h-4" /> {activeCircuit.name}
              </div>
              <div className="pl-6 space-y-2 text-muted-foreground border-l border-border ml-2 mt-2">
                <div>Primary Inputs ({activeCircuit.primary_inputs})</div>
                <div>Primary Outputs ({activeCircuit.primary_outputs})</div>
                <div>Internal Gates ({activeCircuit.gates})</div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground text-center mt-10">No circuit loaded</div>
          )}
        </div>

        {/* Right Pane - Gate List / Properties */}
        <div className="bg-background flex flex-col p-4 overflow-hidden">
          <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-4">Circuit Properties</h3>
          <div className="flex-1 overflow-hidden">
            <DataTable columns={columns} data={data} className="h-full" />
          </div>
        </div>
      </Split>
    </div>
  );
}
