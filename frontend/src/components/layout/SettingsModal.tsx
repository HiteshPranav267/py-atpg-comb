import { useEffect, useState } from 'react';
import { X, Trash2, RotateCcw, Eye, EyeOff, RefreshCw } from 'lucide-react';
import { useAppStore } from '@/store';
import { api } from '@/services/api';
import type { LlmModelInfo, LlmProviderSpec, LlmProvidersResponse } from '@/types';

/**
 * Shown until (or instead of, if the backend is unreachable) the list from
 * GET /api/llm/providers arrives. Mirrors pyatpg.ranking.providers so the
 * picker is usable offline; the backend's list wins once it loads.
 */
const FALLBACK_PROVIDERS: LlmProviderSpec[] = [
  { id: 'gemini', label: 'Google Gemini', env_vars: ['GEMINI_API_KEY'], default_model: 'gemini-flash-latest', key_prefix: 'AIza', openai_compatible: false, base_url: null, hint: 'Keys from aistudio.google.com start with AIza. Each model has its own free-tier quota, so switching model is the way past a daily limit.', models: ['gemini-flash-latest', 'gemini-flash-lite-latest', 'gemini-pro-latest', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro'], base_url_presets: [] },
  { id: 'openai', label: 'OpenAI', env_vars: ['OPENAI_API_KEY'], default_model: 'gpt-4o-mini', key_prefix: 'sk-', openai_compatible: true, base_url: null, hint: 'Keys from platform.openai.com start with sk-.', models: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1', 'gpt-4.1-mini', 'o4-mini'], base_url_presets: [] },
  { id: 'anthropic', label: 'Anthropic Claude', env_vars: ['ANTHROPIC_API_KEY'], default_model: 'claude-sonnet-5', key_prefix: 'sk-ant-', openai_compatible: false, base_url: null, hint: 'Keys from console.anthropic.com start with sk-ant-.', models: ['claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5-20251001'], base_url_presets: [] },
  { id: 'nvidia', label: 'NVIDIA NIM', env_vars: ['NVIDIA_API_KEY'], default_model: 'meta/llama-3.3-70b-instruct', key_prefix: 'nvapi-', openai_compatible: true, base_url: 'https://integrate.api.nvidia.com/v1', hint: 'Keys from build.nvidia.com start with nvapi-. Any NIM model id works.', models: ['meta/llama-3.3-70b-instruct', 'meta/llama-3.1-405b-instruct', 'nvidia/llama-3.1-nemotron-70b-instruct', 'mistralai/mixtral-8x22b-instruct-v0.1', 'deepseek-ai/deepseek-r1'], base_url_presets: [] },
  { id: 'openai_compatible', label: 'Custom (OpenAI-compatible)', env_vars: [], default_model: '', key_prefix: '', openai_compatible: true, base_url: null, hint: 'Groq, OpenRouter, Together, Ollama, vLLM, LM Studio... anything exposing /v1/chat/completions. Set the base URL and model.', models: [], base_url_presets: [
    { label: 'Ollama (local)', url: 'http://localhost:11434/v1' },
    { label: 'LM Studio (local)', url: 'http://localhost:1234/v1' },
    { label: 'Groq', url: 'https://api.groq.com/openai/v1' },
    { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
    { label: 'Together AI', url: 'https://api.together.xyz/v1' },
  ] },
];

/** Sentinel select value meaning "type your own", distinct from any real id/url. */
const CUSTOM = '__custom__';

/** Client-side mirror of the backend's prefix sniffing, for the "auto" hint. */
function detectProvider(key: string, providers: LlmProviderSpec[]): LlmProviderSpec | null {
  const trimmed = key.trim();
  if (!trimmed) return null;
  const withPrefix = providers
    .filter((p) => p.key_prefix)
    .sort((a, b) => b.key_prefix.length - a.key_prefix.length);
  return withPrefix.find((p) => trimmed.startsWith(p.key_prefix)) ?? null;
}

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const {
    defaultPatterns, setDefaultPatterns,
    defaultUseLlm, setDefaultUseLlm,
    apiBaseUrlOverride, setApiBaseUrlOverride,
    llmApiKey, setLlmApiKey,
    llmProvider, setLlmProvider,
    llmModel, setLlmModel,
    llmBaseUrl, setLlmBaseUrl,
  } = useAppStore();

  const [urlDraft, setUrlDraft] = useState(apiBaseUrlOverride ?? '');
  const [keyDraft, setKeyDraft] = useState(llmApiKey ?? '');
  const [modelDraft, setModelDraft] = useState(llmModel ?? '');
  const [baseUrlDraft, setBaseUrlDraft] = useState(llmBaseUrl ?? '');
  const [keyVisible, setKeyVisible] = useState(false);
  const [keySaved, setKeySaved] = useState(false);
  const [cleared, setCleared] = useState(false);
  const [providerInfo, setProviderInfo] = useState<LlmProvidersResponse | null>(null);
  // Models fetched from the vendor for the key in the box -- exact ids, and
  // only the ones this credential can actually run. Keyed by the provider
  // they were fetched for, so switching vendor never shows a stale list.
  const [liveModels, setLiveModels] = useState<{ provider: string; models: LlmModelInfo[] } | null>(null);
  const [modelsStatus, setModelsStatus] = useState<'idle' | 'loading' | 'error'>('idle');
  const [modelsError, setModelsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getLlmProviders()
      .then((info) => { if (!cancelled) setProviderInfo(info); })
      .catch(() => { /* backend down or old: keep the built-in list */ });
    return () => { cancelled = true; };
  }, []);

  const providers = providerInfo?.providers ?? FALLBACK_PROVIDERS;
  const selectedSpec = providers.find((p) => p.id === llmProvider) ?? null;
  const detectedSpec = llmProvider === 'auto' ? detectProvider(keyDraft, providers) : null;
  const effectiveSpec = selectedSpec ?? detectedSpec;
  const isCustomProvider = llmProvider === 'openai_compatible';
  const showBaseUrl = effectiveSpec?.openai_compatible ?? false;

  const fetchModels = async () => {
    setModelsStatus('loading');
    setModelsError(null);
    try {
      const result = await api.listLlmModels({
        provider: llmProvider === 'auto' ? undefined : llmProvider,
        api_key: keyDraft.trim() || undefined,
        base_url: baseUrlDraft.trim() || undefined,
      });
      setLiveModels({ provider: result.provider, models: result.models });
      setModelsStatus('idle');
    } catch (err: any) {
      setLiveModels(null);
      setModelsStatus('error');
      setModelsError(err?.response?.data?.detail || err?.message || 'Could not list models');
    }
  };

  // A stored key means the user has already committed to this account, so
  // show what it can run without making them click.
  useEffect(() => {
    if (llmApiKey) void fetchModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const accountModels: LlmModelInfo[] =
    liveModels && (!effectiveSpec || liveModels.provider === effectiveSpec.id) ? liveModels.models : [];
  const accountIds = new Set(accountModels.map((m) => m.id));
  // Curated suggestions, minus anything the account list already carries.
  const suggestedModels = (effectiveSpec?.models ?? []).filter((m) => !accountIds.has(m));

  // The model/base-URL <select>s show one of: blank/"default", a known
  // option, or the custom sentinel (which reveals a free-text input below).
  const knownModels = [...accountModels.map((m) => m.id), ...suggestedModels];
  const modelSelectValue = modelDraft === '' ? '' : knownModels.includes(modelDraft) ? modelDraft : CUSTOM;
  const basePresets = effectiveSpec?.base_url_presets ?? [];
  const baseSelectValue = baseUrlDraft === ''
    ? ''
    : basePresets.some((p) => p.url === baseUrlDraft) ? baseUrlDraft : CUSTOM;

  // Save key, model and base URL together: they only make sense as a set.
  const handleSaveKey = () => {
    setLlmApiKey(keyDraft.trim() || null);
    setLlmModel(modelDraft.trim() || null);
    setLlmBaseUrl(baseUrlDraft.trim().replace(/\/$/, '') || null);
    setKeySaved(true);
    setTimeout(() => setKeySaved(false), 2000);
  };

  const handleClearKey = () => {
    setKeyDraft('');
    setModelDraft('');
    setBaseUrlDraft('');
    setLlmApiKey(null);
    setLlmModel(null);
    setLlmBaseUrl(null);
  };

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
                  <option value="llm">LLM-assisted</option>
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
            <h3 className="text-sm font-semibold">LLM Provider &amp; API Key</h3>
            <p className="text-xs text-muted-foreground">
              Used for LLM-based gate targeting, circuit analysis and the LLM ranker. Any
              supported vendor works — pick one, or leave it on <em>Auto</em> and paste a key:
              the vendor is recognised from the key's prefix. Applies immediately, without
              redeploying the backend. Leave the key blank to use whatever the server has
              configured.
            </p>

            <div className="grid grid-cols-2 gap-4 mt-1">
              <div>
                <label className="block text-xs font-medium mb-1.5">Provider</label>
                <select
                  value={llmProvider}
                  onChange={(e) => {
                    const next = e.target.value;
                    setLlmProvider(next);
                    // A vendor's default endpoint is implied; only a custom one needs typing.
                    if (next !== 'openai_compatible') { setBaseUrlDraft(''); setLlmBaseUrl(null); }
                  }}
                  className="w-full bg-background border border-border rounded px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
                >
                  <option value="auto">Auto-detect from key</option>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-medium">
                    Model <span className="text-muted-foreground font-normal">(optional)</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => void fetchModels()}
                    disabled={modelsStatus === 'loading'}
                    title="Ask the vendor which models this key can use (exact ids)"
                    className="flex items-center gap-1 text-[11px] text-primary hover:underline disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3 h-3 ${modelsStatus === 'loading' ? 'animate-spin' : ''}`} />
                    {modelsStatus === 'loading' ? 'Loading…' : 'Load from account'}
                  </button>
                </div>
                <select
                  value={modelSelectValue}
                  onChange={(e) => {
                    const val = e.target.value;
                    setModelDraft(val === CUSTOM ? (modelDraft || ' ').trim() : val);
                  }}
                  className="w-full bg-background border border-border rounded px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
                >
                  <option value="">
                    Use provider default{effectiveSpec?.default_model ? ` (${effectiveSpec.default_model})` : ''}
                  </option>
                  {accountModels.length > 0 && (
                    <optgroup label="Available to your key">
                      {accountModels.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.label !== m.id ? `${m.label} — ${m.id}` : m.id}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {suggestedModels.length > 0 && (
                    <optgroup label={accountModels.length > 0 ? 'Other suggestions' : 'Suggestions'}>
                      {suggestedModels.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </optgroup>
                  )}
                  <option value={CUSTOM}>Custom model id...</option>
                </select>
                {modelsStatus === 'error' && modelsError && (
                  <p className="text-[11px] text-destructive mt-1">{modelsError}</p>
                )}
                {accountModels.length > 0 && modelsStatus !== 'error' && (
                  <p className="text-[11px] text-muted-foreground mt-1">
                    {accountModels.length} text-generation model(s) reported by {effectiveSpec?.label ?? 'the vendor'} for this key.
                  </p>
                )}
                {modelSelectValue === CUSTOM && (
                  <input
                    type="text"
                    value={modelDraft}
                    onChange={(e) => setModelDraft(e.target.value)}
                    placeholder="e.g. llama3, mixtral-8x7b"
                    autoComplete="off"
                    spellCheck={false}
                    className="w-full mt-1.5 bg-background border border-border rounded px-3 py-1.5 text-sm font-mono focus:border-primary focus:outline-none"
                  />
                )}
              </div>
            </div>

            {showBaseUrl && (
              <div>
                <label className="block text-xs font-medium mb-1.5">
                  Base URL{' '}
                  {isCustomProvider
                    ? <span className="text-destructive font-normal">(required)</span>
                    : <span className="text-muted-foreground font-normal">(optional override)</span>}
                </label>
                {basePresets.length > 0 ? (
                  <>
                    <select
                      value={baseSelectValue}
                      onChange={(e) => {
                        const val = e.target.value;
                        setBaseUrlDraft(val === CUSTOM ? (baseUrlDraft || ' ').trim() : val);
                      }}
                      className="w-full bg-background border border-border rounded px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
                    >
                      <option value="">
                        {isCustomProvider ? 'Select an endpoint...' : `Use default${effectiveSpec?.base_url ? ` (${effectiveSpec.base_url})` : ''}`}
                      </option>
                      {basePresets.map((p) => (
                        <option key={p.url} value={p.url}>{p.label} — {p.url}</option>
                      ))}
                      <option value={CUSTOM}>Custom URL...</option>
                    </select>
                    {baseSelectValue === CUSTOM && (
                      <input
                        type="text"
                        value={baseUrlDraft}
                        onChange={(e) => setBaseUrlDraft(e.target.value)}
                        placeholder="https://your-endpoint/v1"
                        autoComplete="off"
                        spellCheck={false}
                        className="w-full mt-1.5 bg-background border border-border rounded px-3 py-1.5 text-sm font-mono focus:border-primary focus:outline-none"
                      />
                    )}
                  </>
                ) : (
                  <input
                    type="text"
                    value={baseUrlDraft}
                    onChange={(e) => setBaseUrlDraft(e.target.value)}
                    placeholder={effectiveSpec?.base_url || 'http://localhost:11434/v1'}
                    autoComplete="off"
                    spellCheck={false}
                    className="w-full bg-background border border-border rounded px-3 py-1.5 text-sm font-mono focus:border-primary focus:outline-none"
                  />
                )}
              </div>
            )}

            <label className="block text-xs font-medium mt-1">API Key</label>
            <div className="flex gap-2">
              <input
                type={keyVisible ? 'text' : 'password'}
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                placeholder={effectiveSpec?.key_prefix ? `${effectiveSpec.key_prefix}...` : 'paste any provider key'}
                autoComplete="off"
                spellCheck={false}
                className="flex-1 bg-background border border-border rounded px-3 py-1.5 text-sm font-mono focus:border-primary focus:outline-none"
              />
              <button
                onClick={() => setKeyVisible((v) => !v)}
                title={keyVisible ? 'Hide' : 'Show'}
                className="text-muted-foreground hover:text-foreground px-2"
              >
                {keyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
              <button
                onClick={handleSaveKey}
                className="bg-primary text-primary-foreground px-3 py-1.5 rounded text-sm font-medium hover:bg-primary/90"
              >
                {keySaved ? 'Saved' : 'Save'}
              </button>
              {(llmApiKey || llmModel || llmBaseUrl) && (
                <button
                  onClick={handleClearKey}
                  title="Remove the stored key, model and endpoint"
                  className="text-muted-foreground hover:text-foreground px-2"
                >
                  <RotateCcw className="w-4 h-4" />
                </button>
              )}
            </div>

            <p className="text-xs text-muted-foreground">
              {effectiveSpec ? (
                <>
                  {llmProvider === 'auto' && <><strong>Detected:</strong> {effectiveSpec.label}. </>}
                  {effectiveSpec.hint}
                </>
              ) : llmProvider === 'auto' && keyDraft.trim() ? (
                <>
                  Key prefix not recognised — the server will use its default provider
                  ({providerInfo?.default_provider ?? 'gemini'}). Pick the vendor explicitly if that's wrong.
                </>
              ) : (
                <>Paste a Gemini, OpenAI, Anthropic or NVIDIA key and the vendor is picked for you.</>
              )}
            </p>

            <p className="text-xs text-muted-foreground">
              {llmApiKey ? (
                <>
                  A key is stored in this browser and sent with each request. Note it is kept in
                  this browser's local storage in plain text — clear it on a shared machine.
                </>
              ) : providerInfo?.server_default ? (
                <>
                  No key stored here; requests use the server's{' '}
                  {providers.find((p) => p.id === providerInfo.server_default?.provider)?.label ?? providerInfo.server_default.provider}
                  {' '}({providerInfo.server_default.model}).
                </>
              ) : providerInfo ? (
                <>No key stored here and the server has none configured — LLM features fall back to structural heuristics.</>
              ) : (
                <>No key stored here; requests use whatever the server has configured.</>
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
