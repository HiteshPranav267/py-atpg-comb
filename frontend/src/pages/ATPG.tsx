import { Zap, Play } from 'lucide-react';
import { useAppStore } from '@/store';

export default function ATPG() {
  const { activeCircuit } = useAppStore();

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
              {activeCircuit ? activeCircuit.name : 'No circuit loaded. Please load a circuit in the Circuits tab first.'}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-6">
          <div>
            <label className="block text-sm font-medium mb-2">Fault Model</label>
            <select className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none">
              <option value="stuck-at">Stuck-at Faults</option>
              <option value="transition" disabled>Transition Delay (Coming Soon)</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-2">Test Generation Mode</label>
            <select className="w-full bg-background border border-border rounded px-3 py-2 focus:border-primary focus:outline-none">
              <option value="full">Full Fault Coverage</option>
              <option value="random">Random Pattern Simulation</option>
            </select>
          </div>
        </div>

        <button 
          disabled={!activeCircuit}
          className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-md font-medium transition-colors disabled:opacity-50"
        >
          <Play className="w-4 h-4 fill-current" />
          Run ATALANTA
        </button>
      </div>
    </div>
  );
}
