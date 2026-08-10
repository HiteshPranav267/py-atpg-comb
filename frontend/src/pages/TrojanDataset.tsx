import { FileBox, Activity, Download, Info } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import type { TrojanGenerateResult } from '@/types';

export default function TrojanDataset() {
  const { activeCircuit, activeCircuitFile } = useAppStore();
  const [variants, setVariants] = useState(10);
  const [strategy, setStrategy] = useState('random');

  const generateMutation = useMutation({
    mutationFn: api.generateTrojanDataset,
    onSuccess: (result: TrojanGenerateResult) => {
      const url = URL.createObjectURL(result.blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = result.filename;
      a.click();
      URL.revokeObjectURL(url);
    },
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
    <div className="p-8 max-w-4xl mx-auto w-full flex flex-col gap-6">
      <div className="flex items-center gap-4">
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
              min={1}
              step={1}
              value={variants}
              onChange={e => {
                const parsed = parseInt(e.target.value, 10);
                setVariants(Number.isFinite(parsed) ? Math.max(1, parsed) : 1);
              }}
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
              <option value="rare_node">Rare Node Targeting (HTS/OCR)</option>
            </select>
          </div>
        </div>

        <button
          onClick={handleGenerate}
          disabled={!activeCircuitFile || variants < 1 || generateMutation.isPending}
          className="flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded-md font-medium transition-colors disabled:opacity-50"
        >
          {generateMutation.isPending ? <Activity className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
          {generateMutation.isPending ? 'Generating...' : 'Generate & Download'}
        </button>

        {result && (
          <div className="p-4 rounded-md border border-green-500/50 bg-green-500/10 text-green-400 text-sm">
            Downloaded <span className="font-mono font-bold">{result.filename}</span> — {result.variantsGenerated} variant
            {result.variantsGenerated !== 1 ? 's' : ''} of "{result.circuit}" using the {result.strategy} strategy.
            {' '}The zip also includes a clean (uninfected) copy, each as both Verilog (.v) and .bench, for comparison.
            <br />
            <span className="text-muted-foreground">
              A copy was also saved on the server under <span className="font-mono">datasets/{result.circuit}</span> — point the{' '}
              <Link to="/benchmark" className="text-primary hover:underline">Benchmark</Link> tab's Dataset Directory at{' '}
              <span className="font-mono">datasets</span> to evaluate rankers against it.
            </span>
          </div>
        )}
        {errorMsg && (
          <div className="p-4 rounded-md border border-red-500/50 bg-red-500/10 text-red-400 text-sm font-mono">
            Error: {errorMsg}
          </div>
        )}
      </div>

      <InsertionExplainer />
    </div>
  );
}

function InsertionExplainer() {
  return (
    <div className="border border-border bg-secondary/20 rounded-lg p-5 flex flex-col gap-4">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Info className="w-4 h-4 text-primary" /> How Trojan insertion works here
      </div>

      <div className="grid md:grid-cols-[minmax(0,1fr)_280px] gap-5 items-center">
        <div className="text-sm text-muted-foreground leading-relaxed flex flex-col gap-2">
          <p>
            Every inserted Trojan has two parts: a <strong className="text-foreground">trigger</strong> (an AND gate
            watching a few existing signals) and a <strong className="text-foreground">payload</strong> (an XOR gate
            spliced into a target signal). The payload only flips its target's value when every trigger input happens
            to be 1 at the same time — otherwise the circuit behaves exactly as before.
          </p>
          <p>
            <span className="font-medium text-foreground">Random</span> picks trigger and target signals with no
            regard for how likely that "every input is 1" condition is to occur.{' '}
            <span className="font-medium text-foreground">Rare Node Targeting</span> instead computes, for every
            signal, how rarely it naturally sits at a hard-to-reach value (SCOAP-derived <em>HTS</em>/<em>OCR</em>{' '}
            scores) and builds the trigger only from the rarest ones — so the resulting Trojan stays dormant almost
            all the time, which is what makes real Hardware Trojans hard to catch with ordinary testing.
          </p>
          <p>
            Each generated variant ships with a <span className="font-mono text-xs bg-secondary px-1 rounded">metadata.json</span> recording
            exactly which gates are the trigger/payload (the ground truth used by the <Link to="/benchmark" className="text-primary hover:underline">Benchmark</Link> tab)
            and an <strong className="text-foreground">ICP score</strong> — the percentage of the circuit's inputs
            actually needed to activate it. Higher ICP means a rarer, stealthier trigger condition.
          </p>
        </div>

        <TrojanDiagram />
      </div>
    </div>
  );
}

function TrojanDiagram() {
  return (
    <figure className="flex flex-col items-center gap-2">
      <svg viewBox="0 0 260 170" className="w-full h-auto" role="img" aria-label="Diagram of a trigger AND gate feeding a payload XOR gate spliced into a target net">
        {/* rare input nets */}
        <text x="4" y="30" className="fill-muted-foreground" fontSize="9" fontFamily="monospace">rare_net_1</text>
        <line x1="60" y1="27" x2="86" y2="27" stroke="currentColor" className="text-muted-foreground" strokeWidth="1.5" />
        <text x="4" y="54" className="fill-muted-foreground" fontSize="9" fontFamily="monospace">rare_net_2</text>
        <line x1="60" y1="51" x2="86" y2="51" stroke="currentColor" className="text-muted-foreground" strokeWidth="1.5" />

        {/* trigger AND gate */}
        <path d="M86 15 h20 a24 24 0 0 1 0 48 h-20 Z" fill="none" stroke="#f87171" strokeWidth="2" />
        <text x="96" y="66" fontSize="8" fill="#f87171" fontFamily="sans-serif" fontWeight="600">Trigger (AND)</text>

        {/* trigger output to payload */}
        <line x1="130" y1="39" x2="170" y2="39" stroke="#f87171" strokeWidth="1.5" />
        <line x1="170" y1="39" x2="170" y2="95" stroke="#f87171" strokeWidth="1.5" />
        <line x1="170" y1="95" x2="186" y2="95" stroke="#f87171" strokeWidth="1.5" />

        {/* target net line, intercepted */}
        <text x="4" y="98" className="fill-muted-foreground" fontSize="9" fontFamily="monospace">target_net</text>
        <line x1="60" y1="95" x2="186" y2="95" stroke="currentColor" className="text-muted-foreground" strokeWidth="1.5" />

        {/* payload XOR gate */}
        <path d="M186 78 q10 17 0 34" fill="none" stroke="#4ade80" strokeWidth="2" />
        <path d="M190 78 q14 0 30 17 q-16 17 -30 17 q10 -17 0 -34 Z" fill="none" stroke="#4ade80" strokeWidth="2" />
        <text x="178" y="128" fontSize="8" fill="#4ade80" fontFamily="sans-serif" fontWeight="600">Payload (XOR)</text>

        {/* output */}
        <line x1="220" y1="95" x2="250" y2="95" stroke="#4ade80" strokeWidth="1.5" />
        <text x="200" y="15" className="fill-muted-foreground" fontSize="9" fontFamily="monospace" textAnchor="middle">flips only if trigger = 1</text>
      </svg>
      <figcaption className="text-[11px] text-muted-foreground text-center leading-snug">
        Two rare signals AND together into a trigger; the payload XORs the target only when that trigger fires.
      </figcaption>
    </figure>
  );
}
