import type { LlmProviderSpec } from '@/types';

/**
 * Shown until (or instead of, if the backend is unreachable) the list from
 * GET /api/llm/providers arrives. Mirrors pyatpg.ranking.providers so the
 * picker is usable offline; the backend's list wins once it loads.
 */
export const FALLBACK_PROVIDERS: LlmProviderSpec[] = [
  { id: 'groq', label: 'Groq', env_vars: ['GROQ_API_KEY'], default_model: 'openai/gpt-oss-120b', key_prefix: 'gsk_', openai_compatible: true, base_url: 'https://api.groq.com/openai/v1', hint: 'Keys from console.groq.com start with gsk_. Free tier available; each model has its own rate limits, so switching model is the way past a limit.', models: ['openai/gpt-oss-120b', 'openai/gpt-oss-20b', 'llama-3.3-70b-versatile', 'llama-3.1-8b-instant'], base_url_presets: [] },
  { id: 'gemini', label: 'Google Gemini', env_vars: ['GEMINI_API_KEY'], default_model: 'gemini-flash-latest', key_prefix: 'AIza', openai_compatible: false, base_url: null, hint: 'Keys from aistudio.google.com start with AIza. Each model has its own free-tier quota, so switching model is the way past a daily limit.', models: ['gemini-flash-latest', 'gemini-flash-lite-latest', 'gemini-pro-latest', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro'], base_url_presets: [] },
  { id: 'openai', label: 'OpenAI', env_vars: ['OPENAI_API_KEY'], default_model: 'gpt-4o-mini', key_prefix: 'sk-', openai_compatible: true, base_url: null, hint: 'Keys from platform.openai.com start with sk-.', models: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1', 'gpt-4.1-mini', 'o4-mini'], base_url_presets: [] },
  { id: 'anthropic', label: 'Anthropic Claude', env_vars: ['ANTHROPIC_API_KEY'], default_model: 'claude-sonnet-5', key_prefix: 'sk-ant-', openai_compatible: false, base_url: null, hint: 'Keys from console.anthropic.com start with sk-ant-.', models: ['claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5-20251001'], base_url_presets: [] },
  { id: 'nvidia', label: 'NVIDIA NIM', env_vars: ['NVIDIA_API_KEY'], default_model: 'meta/llama-3.3-70b-instruct', key_prefix: 'nvapi-', openai_compatible: true, base_url: 'https://integrate.api.nvidia.com/v1', hint: 'Keys from build.nvidia.com start with nvapi-. Any NIM model id works.', models: ['meta/llama-3.3-70b-instruct', 'meta/llama-3.1-405b-instruct', 'nvidia/llama-3.1-nemotron-70b-instruct', 'mistralai/mixtral-8x22b-instruct-v0.1', 'deepseek-ai/deepseek-r1'], base_url_presets: [] },
  { id: 'openai_compatible', label: 'Custom (OpenAI-compatible)', env_vars: [], default_model: '', key_prefix: '', openai_compatible: true, base_url: null, hint: 'Cerebras, OpenRouter, Together, Ollama, vLLM, LM Studio... anything exposing /v1/chat/completions. Set the base URL and model.', models: [], base_url_presets: [
    { label: 'Ollama (local)', url: 'http://localhost:11434/v1' },
    { label: 'LM Studio (local)', url: 'http://localhost:1234/v1' },
    { label: 'Cerebras', url: 'https://api.cerebras.ai/v1' },
    { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
    { label: 'Together AI', url: 'https://api.together.xyz/v1' },
  ] },
];

/** Client-side mirror of the backend's prefix sniffing, for the "auto" hint. */
export function detectProvider(key: string, providers: LlmProviderSpec[]): LlmProviderSpec | null {
  const trimmed = key.trim();
  if (!trimmed) return null;
  const withPrefix = providers
    .filter((p) => p.key_prefix)
    .sort((a, b) => b.key_prefix.length - a.key_prefix.length);
  return withPrefix.find((p) => trimmed.startsWith(p.key_prefix)) ?? null;
}
