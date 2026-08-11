import { useState } from 'react';
import { X, Trash2, RotateCcw } from 'lucide-react';
import { useAppStore } from '@/store';

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const {
    defaultPatterns, setDefaultPatterns,
    defaultUseLlm, setDefaultUseLlm,
    apiBaseUrlOverride, setApiBaseUrlOverride,
  } = useAppStore();

  const [urlDraft, setUrlDraft] = useState(apiBaseUrlOverride ?? '');
  const [cleared, setCleared] = useState(false);

  // Distinguished so the UI can say which of the two a URL came from: a stale
  // build-time value looks identical to a saved override otherwise.
  const buildTimeBase: string | undefined = import.meta.env.VITE_API_URL;
  const effectiveBase = apiBaseUrlOverride || buildTimeBase || '(same-origin, proxied via /api)';

  const handleSaveUrl = () => {
    const trimmed = urlDraft.trim().replace(/\/$/, '');
    setApiBaseUrlOverride(trimmed || null);
  };

  const handleClearSession = () => {
    const { setActiveCircuit, setActiveFeatureSet, setActiveRanking, setActiveAtpgReport } = useAppStore.getState();
    setActiveCircuit(null);
    setActiveFeatureSet(null);
    setActiveRanking(null);
    setActiveAtpgReport(null);
    setCleared(true);
    setTimeout(() => setCleared(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-card border border-border rounded-lg shadow-lg w-full max-w-lg mx-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-lg">Settings</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 flex flex-col gap-6">
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">ATPG Defaults</h3>
            <p className="text-xs text-muted-foreground">Applied as the starting values next time you open the ATPG page.</p>
            <div className="grid grid-cols-2 gap-4 mt-1">
              <div>
                <label className="block text-xs font-medium mb-1.5">Toggle Simulation Patterns</label>
                <input
                  type="number"
                  value={defaultPatterns}
                  onChange={(e) => setDefaultPatterns(parseInt(e.target.value) || 0)}
                  className="w-full bg-background border border-border rounded px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5">Gate Prioritization</label>
                <select
                  value={defaultUseLlm ? 'llm' : 'heuristic'}
                  onChange={(e) => setDefaultUseLlm(e.target.value === 'llm')}
                  className="w-full bg-background border border-border rounded px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
                >
                  <option value="llm">Gemini (LLM)</option>
                  <option value="heuristic">Offline heuristic</option>
                </select>
              </div>
            </div>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Backend Connection</h3>
            <p className="text-xs text-muted-foreground">
              Point this app at a different backend (e.g. local <code className="font-mono">http://localhost:8000</code> vs. the deployed Render URL) without rebuilding. Leave blank to use the default.
            </p>
            <div className="flex gap-2 mt-1">
              <input
                type="text"
                value={urlDraft}
                onChange={(e) => setUrlDraft(e.target.value)}
                placeholder="http://localhost:8000"
                className="flex-1 bg-background border border-border rounded px-3 py-1.5 text-sm font-mono focus:border-primary focus:outline-none"
              />
              <button
                onClick={handleSaveUrl}
                className="bg-primary text-primary-foreground px-3 py-1.5 rounded text-sm font-medium hover:bg-primary/90"
              >
                Save
              </button>
              {apiBaseUrlOverride && (
                <button
                  onClick={() => { setUrlDraft(''); setApiBaseUrlOverride(null); }}
                  title="Reset to default"
                  className="text-muted-foreground hover:text-foreground px-2"
                >
                  <RotateCcw className="w-4 h-4" />
                </button>
              )}
            </div>
            <p className="text-xs text-muted-foreground font-mono mt-1">Currently: {effectiveBase}</p>
            <p className="text-xs text-muted-foreground mt-1">
              Source:{' '}
              {apiBaseUrlOverride ? (
                <>saved in this browser — clearing it falls back to the build default below.</>
              ) : buildTimeBase ? (
                <>
                  baked in when the frontend was built (<span className="font-mono">VITE_API_URL</span>).
                  Changing it requires updating that variable and redeploying the frontend; a
                  value entered above only overrides it in this browser.
                </>
              ) : (
                <>no build default set — requests go to this site's own origin under /api.</>
              )}
            </p>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Session Data</h3>
            <p className="text-xs text-muted-foreground">
              Clears the currently loaded circuit, extracted features, ranking, and ATPG report from this browser. Does not affect your benchmark history.
            </p>
            <button
              onClick={handleClearSession}
              className="flex items-center gap-2 w-fit bg-secondary hover:bg-secondary/80 text-secondary-foreground px-3 py-1.5 rounded text-sm font-medium transition-colors mt-1"
            >
              <Trash2 className="w-4 h-4" /> {cleared ? 'Cleared!' : 'Clear Session Data'}
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}
