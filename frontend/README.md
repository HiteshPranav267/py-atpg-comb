# PyATPG frontend

React 19 + TypeScript + Vite + TailwindCSS user interface for the PyATPG
backend. See the [main README](../README.md) for what the analysis does.

```bash
npm install
npm run dev     # http://localhost:5173, proxies /api to http://localhost:8000
npm run build   # production build (Vercel)
```

The backend URL comes from `VITE_API_URL` at build time, or from
**Settings → Backend Connection** in the running app (saved per browser).

## Pages

- **ATPG** — upload a circuit, choose mode, full scan and LLM use, and run.
  While it runs the page polls `/api/atpg/progress/{id}` every 1.5 s and
  shows the current step and one line of what the LLM is thinking. Results:
  run reports, comparison, Trojan verdict, Trojan check (rare-net activation,
  ground-truth recall with vs without the LLM), LLM usage.
- **Circuits, Features, Ranking, Trojan Dataset, Reports, Analytics, Help.**
- **Settings** — backend URL; LLM provider (Groq preselected), model
  (*Load from account* lists the models a key can use), base URL and key,
  plus backup keys. Stored in the browser only.
- **Admin** — needs the server's admin token: server status, queue (cancel
  runs), live settings, recent runs, storage, restart, and the run database
  browser with CSV export.

Key code: `src/services/api.ts` (API client), `src/pages/ATPG.tsx`,
`src/components/atpg/` (result panels), `src/pages/Admin.tsx`,
`src/components/layout/LlmKeyEditor.tsx`, `src/store/index.ts` (persisted
settings).
