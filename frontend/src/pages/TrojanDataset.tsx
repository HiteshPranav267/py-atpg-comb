import { FileBox, Activity } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/services/api';

export default function TrojanDataset() {
  const [circuitPath, setCircuitPath] = useState('');
  const [variants, setVariants] = useState(10);
  const [strategy, setStrategy] = useState('random');
  const [status, setStatus] = useState<string>('');
  const [loading, setLoading] = useState(false);

  const handleGenerate = async () => {
    setLoading(true);
    setStatus('');
    try {
      const res = await api.generateTrojanDataset(circuitPath, variants, strategy);
      setStatus(`Success: Generated ${res.variants_generated} variants of ${res.circuit}`);
    } catch (err: any) {
      setStatus(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-8 max-w-4xl mx-auto w-full">
      <div className="flex items-center gap-4 mb-8">
        <FileBox className="w-8 h-8 text-primary" />
        <h1 className="text-3xl font-bold tracking-tight">Trojan Dataset Generator</h1>
      </div>

      <div className="bg-card border border-border p-6 rounded-lg shadow-sm flex flex-col gap-6">
        <div>
          <label className="block text-sm font-medium mb-2">Base Circuit Path</label>
          <input 
            type="text" 
            value={circuitPath}
            onChange={e => setCircuitPath(e.target.value)}
            className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none font-mono text-sm"
            placeholder="tests/fixtures/c17.v"
          />
        </div>

        <div className="grid grid-cols-2 gap-6">
          <div>
            <label className="block text-sm font-medium mb-2">Number of Variants</label>
            <input 
              type="number" 
              value={variants}
              onChange={e => setVariants(parseInt(e.target.value))}
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
          disabled={loading || !circuitPath}
          className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-md font-medium transition-colors disabled:opacity-50"
        >
          {loading && <Activity className="w-4 h-4 animate-spin" />}
          Generate Dataset
        </button>

        {status && (
          <div className={`p-4 rounded-md border text-sm font-mono ${status.startsWith('Error') ? 'bg-red-500/10 border-red-500/50 text-red-400' : 'bg-green-500/10 border-green-500/50 text-green-400'}`}>
            {status}
          </div>
        )}
      </div>
    </div>
  );
}
