import { FileBox, Activity } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';

export default function TrojanDataset() {
  const { activeCircuit, activeCircuitFile } = useAppStore();
  const [variants, setVariants] = useState(10);
  const [strategy, setStrategy] = useState('random');

  const generateMutation = useMutation({
    mutationFn: api.generateTrojanDataset,
  });

  const handleGenerate = () => {
    if (activeCircuitFile) {
      generateMutation.mutate({ file: activeCircuitFile, variants, strategy });
    }
  };

  const result = generateMutation.data;
  const errorMsg = generateMutation.isError
    ? ((generateMutation.error as any)?.response?.data?.detail || (generateMutation.error as any)?.message || 'Generation failed')
    : null;

  return (
    <div className="p-8 max-w-4xl mx-auto w-full">
      <div className="flex items-center gap-4 mb-8">
        <FileBox className="w-8 h-8 text-primary" />
        <h1 className="text-3xl font-bold tracking-tight">Trojan Dataset Generator</h1>
      </div>

      <div className="bg-card border border-border p-6 rounded-lg shadow-sm flex flex-col gap-6">
        <div className="flex items-center justify-between border-b border-border pb-4">
          <div>
            <h3 className="font-semibold text-lg">Base Circuit</h3>
            <p className="text-muted-foreground text-sm font-mono mt-1">
              {activeCircuit
                ? activeCircuit.name
                : <>No circuit loaded. Please <Link to="/circuits" className="text-primary hover:underline">load a circuit</Link> first.</>}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-6">
          <div>
            <label className="block text-sm font-medium mb-2">Number of Variants</label>
            <input
              type="number"
              value={variants}
              onChange={e => setVariants(parseInt(e.target.value) || 0)}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Insertion Strategy</label>
            <select
              value={strategy}
              onChange={e => setStrategy(e.target.value)}
              className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none"
            >
              <option value="random">Random</option>
              <option value="rare_node">Rare Node Targeting</option>
            </select>
          </div>
        </div>

        <button
          onClick={handleGenerate}
          disabled={!activeCircuitFile || generateMutation.isPending}
          className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-md font-medium transition-colors disabled:opacity-50"
        >
          {generateMutation.isPending && <Activity className="w-4 h-4 animate-spin" />}
          Generate Dataset
        </button>

        {result && (
          <div className="p-4 rounded-md border border-green-500/50 bg-green-500/10 text-green-400 text-sm font-mono">
            Generated {result.variants_generated} variants of "{result.circuit}" using the {result.strategy} strategy.
            <br />Saved to <span className="font-bold">{result.output_dir}</span> on the server — use this path as the
            Dataset Directory (parent of it, e.g. "datasets") in the Benchmark tab to evaluate rankers against it.
          </div>
        )}
        {errorMsg && (
          <div className="p-4 rounded-md border border-red-500/50 bg-red-500/10 text-red-400 text-sm font-mono">
            Error: {errorMsg}
          </div>
        )}
      </div>
    </div>
  );
}
