import { useEffect, useState } from 'react';
import { RotateCcw, Eye, EyeOff, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '@/services/api';
import type { LlmModelInfo, LlmProviderSpec, LlmProvidersResponse } from '@/types';
import { detectProvider } from './llmProviders';

/** Sentinel select value meaning "type your own", distinct from any real id/url. */
const CUSTOM = '__custom__';

export interface LlmKeyValue {
  provider: string;
  apiKey: string | null;
  model: string | null;
  baseUrl: string | null;
}

/**
 * One LLM credential: provider, model, endpoint and key. Used for the main
 * key and, identically, for each backup key.
 *
 * The provider choice is committed as soon as it changes; key, model and
 * base URL are drafts committed together by Save, since they only make
 * sense as a set.
 */
export function LlmKeyEditor({
  title,
  value,
  providers,
  providerInfo,
  onPatch,
  onReset,
  resetTitle,
  onRemove,
}: {
  title?: string;
  value: LlmKeyValue;
  providers: LlmProviderSpec[];
  providerInfo: LlmProvidersResponse | null;
  /** Commit some fields immediately. */
  onPatch: (patch: Partial<LlmKeyValue>) => void;
  /** Main key only: clear the stored key/model/endpoint. */
  onReset?: () => void;
  resetTitle?: string;
  /** Backup keys only: delete this slot. */
  onRemove?: () => void;
}) {
  const { provider, apiKey, model, baseUrl } = value;
  const [keyDraft, setKeyDraft] = useState(apiKey ?? '');
  const [modelDraft, setModelDraft] = useState(model ?? '');
  const [baseUrlDraft, setBaseUrlDraft] = useState(baseUrl ?? '');
  const [keyVisible, setKeyVisible] = useState(false);
  const [keySaved, setKeySaved] = useState(false);
  // Models fetched from the vendor for the key in the box -- exact ids, and
  // only the ones this credential can actually run. Keyed by the provider
  // they were fetched for, so switching vendor never shows a stale list.
  const [liveModels, setLiveModels] = useState<{ provider: string; models: LlmModelInfo[] } | null>(null);
  const [modelsStatus, setModelsStatus] = useState<'idle' | 'loading' | 'error'>('idle');
  const [modelsError, setModelsError] = useState<string | null>(null);

  const selectedSpec = providers.find((p) => p.id === provider) ?? null;
  const detectedSpec = provider === 'auto' ? detectProvider(keyDraft, providers) : null;
  const effectiveSpec = selectedSpec ?? detectedSpec;
  const isCustomProvider = provider === 'openai_compatible';
  const showBaseUrl = effectiveSpec?.openai_compatible ?? false;

  const fetchModels = async () => {
    setModelsStatus('loading');
    setModelsError(null);
    try {
      const result = await api.listLlmModels({
        provider: provider === 'auto' ? undefined : provider,
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
    if (apiKey) void fetchModels();
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

  const handleSave = () => {
    onPatch({
      apiKey: keyDraft.trim() || null,
      model: modelDraft.trim() || null,
      baseUrl: baseUrlDraft.trim().replace(/\/$/, '') || null,
    });
    setKeySaved(true);
    setTimeout(() => setKeySaved(false), 2000);
  };

  const handleReset = () => {
    setKeyDraft('');
    setModelDraft('');
    setBaseUrlDraft('');
    onReset?.();
  };

  return (
    <div className="flex flex-col gap-2">
      {(title || onRemove) && (
        <div className="flex items-center justify-between">
          {title && <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">{title}</h4>}
          {onRemove && (
            <button
              type="button"
              onClick={onRemove}
              title="Remove this backup key"
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="w-3.5 h-3.5" /> Remove
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium mb-1.5">Provider</label>
          <select
            value={provider}
            onChange={(e) => {
              const next = e.target.value;
              // A vendor's default endpoint is implied; only a custom one needs typing.
              if (next !== 'openai_compatible') {
                setBaseUrlDraft('');
                onPatch({ provider: next, baseUrl: null });
              } else {
                onPatch({ provider: next });
              }
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
          onClick={handleSave}
          className="bg-primary text-primary-foreground px-3 py-1.5 rounded text-sm font-medium hover:bg-primary/90"
        >
          {keySaved ? 'Saved' : 'Save'}
        </button>
        {onReset && (apiKey || model || baseUrl) && (
          <button
            onClick={handleReset}
            title={resetTitle ?? 'Remove the stored key, model and endpoint'}
            className="text-muted-foreground hover:text-foreground px-2"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        {effectiveSpec ? (
          <>
            {provider === 'auto' && <><strong>Detected:</strong> {effectiveSpec.label}. </>}
            {effectiveSpec.hint}
          </>
        ) : provider === 'auto' && keyDraft.trim() ? (
          <>
            Key prefix not recognised — the server will use its default provider
            ({providerInfo?.default_provider ?? 'groq'}). Pick the vendor explicitly if that's wrong.
          </>
        ) : (
          <>Paste a Gemini, OpenAI, Anthropic or NVIDIA key and the vendor is picked for you.</>
        )}
      </p>
      {!apiKey && keyDraft.trim() && (
        <p className="text-[11px] text-amber-500">Not saved yet — press Save to use this key.</p>
      )}
    </div>
  );
}
