import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Construction,
  Database,
  Download,
  History,
  KeyRound,
  ListChecks,
  Power,
  RefreshCw,
  RotateCcw,
  Server,
  ShieldAlert,
  SlidersHorizontal,
  Table2,
  Trash2,
  XCircle,
} from 'lucide-react';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import type { AdminDbFilters, AdminDbRun, AdminHistoryRun, AdminRun, AdminSetting } from '@/types';

function formatSeconds(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
  const h = Math.floor(s / 3600);
  if (h < 48) return `${h}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 2 ** 20) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 2 ** 30) return `${(b / 2 ** 20).toFixed(1)} MB`;
  return `${(b / 2 ** 30).toFixed(2)} GB`;
}

function errorDetail(err: unknown): string {
  const e = err as { response?: { data?: { detail?: string } }; message?: string };
  return e?.response?.data?.detail || e?.message || 'Request failed';
}

function Card({
  icon,
  title,
  actions,
  children,
}: {
  icon: ReactNode;
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="bg-card border border-border rounded-lg">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold flex items-center gap-2">
          {icon}
          {title}
        </h2>
        {actions}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="text-sm text-red-400 flex items-start gap-2 mt-2">
      <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
      {errorDetail(error)}
    </p>
  );
}

export default function Admin() {
  const { adminToken, setAdminToken } = useAppStore();
  const [draft, setDraft] = useState(adminToken ?? '');

  return (
    <div className="p-6 max-w-5xl mx-auto flex flex-col gap-6">
      <div className="flex items-start gap-3">
        <ShieldAlert className="w-7 h-7 text-primary mt-1 shrink-0" />
        <div>
          <h1 className="text-2xl font-bold">Admin</h1>
          <p className="text-sm text-muted-foreground">
            Monitor the backend, manage its queue and settings, and clean up storage. Setting
            changes apply immediately and last until the server restarts.
          </p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-lg p-4 flex flex-col gap-3">
        <label className="text-sm font-medium flex items-center gap-2" htmlFor="admin-token">
          <KeyRound className="w-4 h-4" /> Admin token
        </label>
        <div className="flex gap-2">
          <input
            id="admin-token"
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="The server's PYATPG_ADMIN_TOKEN"
            className="flex-1 bg-background border border-border rounded-md px-3 py-1.5 text-sm font-mono"
          />
          <button
            onClick={() => setAdminToken(draft.trim() || null)}
            className="bg-primary text-primary-foreground px-4 py-1.5 rounded-md text-sm font-medium"
          >
            Save
          </button>
          {adminToken && (
            <button
              onClick={() => {
                setAdminToken(null);
                setDraft('');
              }}
              className="border border-border px-4 py-1.5 rounded-md text-sm"
            >
              Forget
            </button>
          )}
        </div>
      </div>

      {adminToken && <AdminPanels token={adminToken} />}
    </div>
  );
}

function AdminPanels({ token }: { token: string }) {
  const status = useQuery({
    queryKey: ['admin-status', token],
    queryFn: () => api.adminStatus(token),
    refetchInterval: 5000,
    retry: false,
  });

  if (status.isError) {
    return (
      <div className="p-4 rounded-md border border-red-500/50 bg-red-500/10 text-red-400 text-sm flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <span>{errorDetail(status.error)}</span>
      </div>
    );
  }
  if (!status.data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <>
      <StatusCard token={token} />
      <QueueCard token={token} />
      <SettingsCard token={token} />
      <HistoryCard token={token} />
      <RunDatabaseCard token={token} />
      <StorageCard token={token} />
    </>
  );
}

// ---------------------------------------------------------------- status

function StatusCard({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ['admin-status', token],
    queryFn: () => api.adminStatus(token),
    refetchInterval: 5000,
    retry: false,
  });
  const maintenance = useMutation({
    mutationFn: (on: boolean) => api.adminUpdateSetting(token, 'maintenance', on ? true : null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-status'] });
      queryClient.invalidateQueries({ queryKey: ['admin-settings'] });
    },
  });
  const [restartNote, setRestartNote] = useState<string | null>(null);
  const restart = useMutation({
    mutationFn: () => api.adminRestart(token),
    onSuccess: (r) =>
      setRestartNote(
        r.hosted
          ? 'Restarting — the backend should be back within about a minute.'
          : 'The server process is stopping. A server started by hand must be started again.'
      ),
  });
  if (!data) return null;

  const mem = data.memory;
  const memPct = mem.rss_mb != null && mem.limit_mb ? Math.round((mem.rss_mb / mem.limit_mb) * 100) : null;

  return (
    <Card
      icon={<Server className="w-4 h-4" />}
      title="Server"
      actions={
        <div className="flex gap-2">
          <button
            onClick={() => maintenance.mutate(!data.maintenance)}
            disabled={maintenance.isPending}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium border disabled:opacity-50 ${
              data.maintenance
                ? 'bg-amber-500/15 text-amber-500 border-amber-500/50'
                : 'border-border hover:bg-secondary'
            }`}
          >
            <Construction className="w-3.5 h-3.5" />
            {data.maintenance ? 'Maintenance ON — turn off' : 'Enter maintenance mode'}
          </button>
          <button
            onClick={() => {
              if (window.confirm('Restart the backend? Every running and queued analysis is lost.')) {
                restart.mutate();
              }
            }}
            disabled={restart.isPending || !!restartNote}
            className="flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium bg-red-600 text-white hover:bg-red-600/90 disabled:opacity-50"
          >
            <Power className="w-3.5 h-3.5" /> Restart backend
          </button>
        </div>
      }
    >
      {data.maintenance && (
        <p className="text-sm text-amber-500 mb-3">
          Maintenance mode is on: new analyses are rejected with a "try again shortly" message.
          Runs already in progress continue.
        </p>
      )}
      {restartNote && <p className="text-sm text-amber-500 mb-3">{restartNote}</p>}
      <dl className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 text-sm">
        <Stat label="Uptime" value={formatSeconds(data.uptime_s)} />
        <Stat
          label="Memory"
          value={
            mem.rss_mb == null
              ? 'n/a'
              : `${mem.rss_mb} MB${mem.limit_mb ? ` / ${mem.limit_mb} MB (${memPct}%)` : ''}`
          }
          warn={memPct != null && memPct >= 80}
        />
        <Stat label="Analyses" value={`${data.slots_in_use} / ${data.slots} slot(s) busy`} />
        <Stat label="CPUs" value={String(data.cpu_count ?? 'n/a')} />
        <Stat label="Deployed commit" value={data.commit ?? 'unknown'} mono />
        <Stat label="Python" value={data.python} />
        <Stat label="Host" value={data.hosted ? 'Render' : 'local'} />
        <Stat label="Platform" value={data.platform} />
      </dl>
      <div className="mt-4 border-t border-border pt-3 text-xs flex flex-col gap-1">
        {Object.entries(data.tools).map(([tool, where]) => (
          <div key={tool} className="flex gap-2">
            <span className="w-20 text-muted-foreground capitalize">{tool}</span>
            <span className={`font-mono break-all ${where.startsWith('unavailable') ? 'text-amber-500' : ''}`}>
              {where}
            </span>
          </div>
        ))}
      </div>
      <ErrorLine error={maintenance.error || restart.error} />
    </Card>
  );
}

function Stat({ label, value, mono, warn }: { label: string; value: string; mono?: boolean; warn?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`${mono ? 'font-mono text-xs' : ''} ${warn ? 'text-amber-500 font-medium' : ''}`}>{value}</dd>
    </div>
  );
}

// ---------------------------------------------------------------- queue

function QueueCard({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const runsQuery = useQuery({
    queryKey: ['admin-runs', token],
    queryFn: () => api.adminListRuns(token),
    refetchInterval: 3000,
    retry: false,
  });
  const cancel = useMutation({
    mutationFn: (id?: string) => api.adminCancelRuns(token, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin-runs'] }),
  });
  const data = runsQuery.data;
  const total = (data?.running.length ?? 0) + (data?.queued.length ?? 0);

  return (
    <Card
      icon={<ListChecks className="w-4 h-4" />}
      title="Queue"
      actions={
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            {data ? `${data.running.length} running · ${data.queued.length} queued` : '…'}
            <RefreshCw className={`inline w-3 h-3 ml-1.5 ${runsQuery.isFetching ? 'animate-spin' : ''}`} />
          </span>
          <button
            onClick={() => cancel.mutate(undefined)}
            disabled={total === 0 || cancel.isPending}
            className="flex items-center gap-1.5 bg-red-600 hover:bg-red-600/90 text-white px-3 py-1 rounded-md text-xs font-medium disabled:opacity-50"
          >
            <Trash2 className="w-3.5 h-3.5" /> Clear queue (cancel all)
          </button>
        </div>
      }
    >
      {total === 0 ? (
        <p className="text-sm text-muted-foreground">The backend is idle.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted-foreground border-b border-border">
              <th className="py-2 pr-3 font-medium">State</th>
              <th className="py-2 pr-3 font-medium">Current step</th>
              <th className="py-2 pr-3 font-medium">Running for</th>
              <th className="py-2 pr-3 font-medium">Browser</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {[...(data?.running ?? []), ...(data?.queued ?? [])].map((run) => (
              <QueueRow key={run.id} run={run} onCancel={() => cancel.mutate(run.id)} busy={cancel.isPending} />
            ))}
          </tbody>
        </table>
      )}
      <ErrorLine error={runsQuery.error || cancel.error} />
    </Card>
  );
}

function QueueRow({ run, onCancel, busy }: { run: AdminRun; onCancel: () => void; busy: boolean }) {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-2 pr-3">
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            run.state === 'running' ? 'bg-primary/15 text-primary' : 'bg-secondary text-muted-foreground'
          }`}
        >
          {run.cancelled ? 'cancelling' : run.state}
        </span>
      </td>
      <td className="py-2 pr-3">
        {run.stage}
        <span className="text-xs text-muted-foreground ml-2 font-mono">{formatSeconds(run.stage_elapsed_s)}</span>
      </td>
      <td className="py-2 pr-3 font-mono text-xs">{formatSeconds(run.elapsed_s)}</td>
      <td className="py-2 pr-3 text-xs">
        {run.client_connected ? 'waiting' : <span className="text-amber-500">gone</span>}
      </td>
      <td className="py-2 text-right">
        <button
          onClick={onCancel}
          disabled={busy || run.cancelled}
          className="inline-flex items-center gap-1 text-red-400 hover:text-red-300 text-xs disabled:opacity-50"
        >
          <XCircle className="w-3.5 h-3.5" /> Cancel
        </button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------- settings

function SettingsCard({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['admin-settings', token],
    queryFn: () => api.adminSettings(token),
    retry: false,
  });
  const update = useMutation({
    mutationFn: ({ key, value }: { key: string; value: number | boolean | string | null }) =>
      api.adminUpdateSetting(token, key, value),
    onSuccess: (settings) => {
      queryClient.setQueryData(['admin-settings', token], settings);
      queryClient.invalidateQueries({ queryKey: ['admin-status'] });
    },
  });

  return (
    <Card icon={<SlidersHorizontal className="w-4 h-4" />} title="Live settings">
      <p className="text-xs text-muted-foreground mb-3">
        Applied immediately to new work; reset to the environment/default value on restart. For
        size limits, 0 means unlimited.
      </p>
      <div className="flex flex-col divide-y divide-border">
        {(data ?? [])
          .filter((s) => s.key !== 'maintenance')
          .map((s) => (
            <SettingRow
              // Remount when the server's value changes, so the input shows it.
              key={`${s.key}:${s.value}:${s.source}`}
              setting={s}
              busy={update.isPending}
              onSave={(value) => update.mutate({ key: s.key, value })}
            />
          ))}
      </div>
      <ErrorLine error={error || update.error} />
    </Card>
  );
}

function SettingRow({
  setting,
  busy,
  onSave,
}: {
  setting: AdminSetting;
  busy: boolean;
  onSave: (value: number | boolean | string | null) => void;
}) {
  const [draft, setDraft] = useState(String(setting.value));
  const dirty = draft !== String(setting.value);
  // A number box cannot show `true`, so on/off and text settings get their own input.
  const parsed: number | boolean | string =
    setting.type === 'bool' ? draft === 'true' : setting.type === 'str' ? draft.trim() : Number(draft);
  const invalid =
    draft.trim() === '' || (setting.type !== 'bool' && setting.type !== 'str' && Number.isNaN(Number(draft)));
  return (
    <div className="py-2.5 flex flex-col md:flex-row md:items-center gap-2">
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium">{setting.label}</div>
        <div className="text-xs text-muted-foreground">
          {setting.help} <span className="font-mono">({setting.env})</span>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {setting.type === 'bool' ? (
          <select
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-28 bg-background border border-border rounded-md px-2 py-1 text-sm font-mono"
          >
            <option value="true">on</option>
            <option value="false">off</option>
          </select>
        ) : (
          <input
            type={setting.type === 'str' ? 'text' : 'number'}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-28 bg-background border border-border rounded-md px-2 py-1 text-sm font-mono"
          />
        )}
        <button
          onClick={() => onSave(parsed)}
          disabled={busy || !dirty || invalid}
          className="bg-primary text-primary-foreground px-3 py-1 rounded-md text-xs font-medium disabled:opacity-40"
        >
          Apply
        </button>
        <span
          className={`text-[11px] w-24 ${setting.source === 'admin override' ? 'text-primary' : 'text-muted-foreground'}`}
        >
          {setting.source}
        </span>
        <button
          onClick={() => onSave(null)}
          disabled={busy || setting.source !== 'admin override'}
          title="Remove the override"
          className="text-muted-foreground hover:text-foreground disabled:opacity-30"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- history

const OUTCOME_STYLE: Record<AdminHistoryRun['outcome'], string> = {
  ok: 'bg-emerald-500/15 text-emerald-500',
  error: 'bg-red-500/15 text-red-400',
  cancelled: 'bg-amber-500/15 text-amber-500',
  rejected: 'bg-secondary text-muted-foreground',
};

function HistoryCard({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['admin-history', token],
    queryFn: () => api.adminHistory(token),
    refetchInterval: 5000,
    retry: false,
  });
  const clear = useMutation({
    mutationFn: () => api.adminClearHistory(token),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin-history'] }),
  });

  return (
    <Card
      icon={<History className="w-4 h-4" />}
      title="Recent runs (last 50)"
      actions={
        <button
          onClick={() => clear.mutate()}
          disabled={!data?.length || clear.isPending}
          className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          Clear log
        </button>
      }
    >
      {!data?.length ? (
        <p className="text-sm text-muted-foreground">No runs since the server started.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted-foreground border-b border-border">
              <th className="py-2 pr-3 font-medium">Finished</th>
              <th className="py-2 pr-3 font-medium">Run</th>
              <th className="py-2 pr-3 font-medium">Took</th>
              <th className="py-2 font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {data.map((run) => (
              <tr key={`${run.id}-${run.started_at}`} className="border-b border-border last:border-0 align-top">
                <td className="py-2 pr-3 text-xs whitespace-nowrap">
                  {new Date(run.started_at).toLocaleTimeString()}
                </td>
                <td className="py-2 pr-3">
                  <div className="break-all">{run.label}</div>
                  {run.detail && <div className="text-xs text-muted-foreground mt-0.5 break-words">{run.detail}</div>}
                </td>
                <td className="py-2 pr-3 font-mono text-xs">{formatSeconds(run.duration_s)}</td>
                <td className="py-2">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${OUTCOME_STYLE[run.outcome]}`}>
                    {run.outcome}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ErrorLine error={error || clear.error} />
    </Card>
  );
}

// ---------------------------------------------------------------- storage

function StorageCard({ token }: { token: string }) {
  const queryClient = useQueryClient();
  const [freed, setFreed] = useState<string | null>(null);
  const { data, error } = useQuery({
    queryKey: ['admin-storage', token],
    queryFn: () => api.adminStorage(token),
    retry: false,
  });
  const clear = useMutation({
    mutationFn: (target: string) => api.adminClearStorage(token, target),
    onSuccess: (res) => {
      queryClient.setQueryData(['admin-storage', token], res.storage);
      setFreed(`Freed ${formatBytes(res.freed_bytes)}.`);
    },
  });

  return (
    <Card
      icon={<Database className="w-4 h-4" />}
      title="Storage"
      actions={freed && <span className="text-xs text-emerald-500">{freed}</span>}
    >
      <div className="flex flex-col divide-y divide-border">
        {(data ?? []).map((item) => (
          <div key={item.key} className="py-2.5 flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium">{item.label}</div>
              <div className="text-xs text-muted-foreground">{item.help}</div>
            </div>
            <span className="font-mono text-xs w-20 text-right">{formatBytes(item.size_bytes)}</span>
            <button
              onClick={() => {
                if (window.confirm(`Clear ${item.label}?`)) clear.mutate(item.key);
              }}
              disabled={clear.isPending || item.size_bytes === 0}
              className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300 disabled:opacity-40"
            >
              <Trash2 className="w-3.5 h-3.5" /> Clear
            </button>
          </div>
        ))}
      </div>
      <ErrorLine error={error || clear.error} />
    </Card>
  );
}

// ---------------------------------------------------------------- run database

const DB_OUTCOME_STYLE: Record<AdminDbRun['outcome'], string> = {
  ...OUTCOME_STYLE,
  timeout: 'bg-orange-500/15 text-orange-400',
};

const DB_FILTER_OPTIONS: { key: keyof AdminDbFilters; label: string; values: string[] }[] = [
  {
    key: 'run_type',
    label: 'Type',
    values: ['atpg', 'circuit_parse', 'feature_extract', 'ranking', 'trojan_generate', 'benchmark'],
  },
  { key: 'outcome', label: 'Outcome', values: ['ok', 'error', 'cancelled', 'rejected', 'timeout'] },
  {
    key: 'source_suite',
    label: 'Suite',
    values: ['iscas85', 'iscas89', 'itc99', 'trust_hub_chip', 'trust_hub_board', 'generated_trojan', 'custom', 'unknown'],
  },
  { key: 'circuit_kind', label: 'Circuit', values: ['combinational', 'sequential'] },
  { key: 'trojan_label', label: 'Trojan', values: ['trojan_free', 'trojan_inserted', 'unknown'] },
  { key: 'llm_provider', label: 'LLM', values: ['groq', 'gemini', 'openai', 'anthropic', 'nvidia', 'openai_compatible'] },
];

const DB_PAGE = 50;

function RunDatabaseCard({ token }: { token: string }) {
  const [filters, setFilters] = useState<AdminDbFilters>({});
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);

  const status = useQuery({
    queryKey: ['admin-db-status', token],
    queryFn: () => api.adminDbStatus(token),
    refetchInterval: 15000,
    retry: false,
  });
  const enabled = !!status.data?.enabled;
  const applied: AdminDbFilters = { ...filters, search: search.trim() || undefined };
  const runs = useQuery({
    queryKey: ['admin-db-runs', token, applied, page],
    queryFn: () => api.adminDbRuns(token, applied, DB_PAGE, page * DB_PAGE),
    enabled,
    refetchInterval: 15000,
    retry: false,
  });

  const setFilter = (key: keyof AdminDbFilters, value: string) => {
    setFilters((f) => ({ ...f, [key]: value || undefined }));
    setPage(0);
  };

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await api.adminDbRunsCsv(token, applied);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pyatpg-runs-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err);
    } finally {
      setExporting(false);
    }
  };

  const s = status.data;
  const total = runs.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / DB_PAGE));

  return (
    <Card
      icon={<Table2 className="w-4 h-4" />}
      title="Run database"
      actions={
        enabled && (
          <button
            onClick={() => void exportCsv()}
            disabled={exporting || total === 0}
            className="flex items-center gap-1 text-xs text-primary hover:underline disabled:opacity-40"
          >
            <Download className="w-3.5 h-3.5" /> {exporting ? 'Exporting…' : 'Export CSV'}
          </button>
        )
      }
    >
      {!s ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : !s.enabled ? (
        <p className="text-sm text-muted-foreground">
          Runs are not being recorded: set <code className="font-mono">DATABASE_URL</code> on the server
          {s.configured ? ' (it is set, but the psycopg package is missing or recording is turned off)' : ''}.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat label="Recorded runs" value={String(s.total_runs ?? '—')} />
            <Stat label="Last 24 h" value={String(s.runs_last_24h ?? '—')} />
            <Stat label="Circuits" value={String(s.circuits ?? '—')} />
            <Stat
              label="Failed writes"
              value={`${s.failed}${s.dropped ? ` (+${s.dropped} dropped)` : ''}`}
              warn={s.failed > 0 || s.dropped > 0}
            />
          </div>
          {(s.connection_error || s.last_error) && (
            <p className="text-xs text-red-400 break-words">
              {s.connection_error ??
                `Last write error${s.last_error_at ? ` (${new Date(s.last_error_at).toLocaleString()})` : ''}: ${s.last_error}`}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Database: <span className="font-mono">{s.host}</span>
          </p>

          <div className="flex flex-wrap gap-2">
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="Search circuit / file"
              className="bg-background border border-border rounded-md px-2.5 py-1 text-xs w-full sm:w-44"
            />
            {DB_FILTER_OPTIONS.map((f) => (
              <select
                key={f.key}
                value={filters[f.key] ?? ''}
                onChange={(e) => setFilter(f.key, e.target.value)}
                className="bg-background border border-border rounded-md px-2 py-1 text-xs"
              >
                <option value="">{f.label}: all</option>
                {f.values.map((v) => (
                  <option key={v} value={v}>
                    {v.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            ))}
          </div>

          {!runs.data?.runs.length ? (
            <p className="text-sm text-muted-foreground">{runs.isLoading ? 'Loading…' : 'No matching runs.'}</p>
          ) : (
            <div className="overflow-x-auto -mx-4 px-4">
              <table className="w-full text-sm min-w-[720px]">
                <thead>
                  <tr className="text-left text-muted-foreground border-b border-border">
                    <th className="py-2 pr-3 font-medium">When</th>
                    <th className="py-2 pr-3 font-medium">Run</th>
                    <th className="py-2 pr-3 font-medium">Circuit</th>
                    <th className="py-2 pr-3 font-medium">LLM</th>
                    <th className="py-2 pr-3 font-medium text-right">Coverage</th>
                    <th className="py-2 pr-3 font-medium text-right">Took</th>
                    <th className="py-2 font-medium">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.data.runs.map((run) => (
                    <DbRunRow key={run.id} run={run} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {total > DB_PAGE && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {page * DB_PAGE + 1}–{Math.min(total, (page + 1) * DB_PAGE)} of {total}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="border border-border rounded px-2 py-0.5 disabled:opacity-40"
                >
                  Newer
                </button>
                <button
                  onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
                  disabled={page >= pages - 1}
                  className="border border-border rounded px-2 py-0.5 disabled:opacity-40"
                >
                  Older
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      <ErrorLine error={status.error || runs.error || exportError} />
    </Card>
  );
}

function DbRunRow({ run }: { run: AdminDbRun }) {
  const circuitInfo = [
    run.source_suite?.replace(/_/g, ' '),
    run.circuit_kind,
    run.gates != null ? `${run.gates.toLocaleString()} gates` : null,
    run.flip_flops ? `${run.flip_flops.toLocaleString()} FF` : null,
    run.trojan_label === 'trojan_inserted' ? 'Trojan' : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <tr className="border-b border-border last:border-0 align-top">
      <td className="py-2 pr-3 text-xs whitespace-nowrap">
        {run.received_at ? new Date(run.received_at).toLocaleString() : '—'}
      </td>
      <td className="py-2 pr-3 text-xs">
        <div>{run.run_type.replace(/_/g, ' ')}</div>
        {run.mode && <div className="text-muted-foreground">{run.mode.replace(/_/g, ' ')}</div>}
      </td>
      <td className="py-2 pr-3 text-xs">
        <div className="font-medium break-all">{run.circuit ?? run.upload_filename ?? '—'}</div>
        <div className="text-muted-foreground">{circuitInfo}</div>
      </td>
      <td className="py-2 pr-3 text-xs">
        {run.use_llm ? (
          <>
            <div>{run.llm_provider ?? '—'}</div>
            <div className="text-muted-foreground break-all">{run.llm_model}</div>
          </>
        ) : (
          <span className="text-muted-foreground">off</span>
        )}
      </td>
      <td className="py-2 pr-3 font-mono text-xs text-right">
        {run.fault_coverage != null ? `${run.fault_coverage.toFixed(2)}%` : '—'}
      </td>
      <td className="py-2 pr-3 font-mono text-xs text-right whitespace-nowrap">
        {run.duration_s != null ? formatSeconds(run.duration_s) : '—'}
      </td>
      <td className="py-2 text-xs">
        <span
          title={run.error_message ?? undefined}
          className={`px-2 py-0.5 rounded font-medium ${DB_OUTCOME_STYLE[run.outcome] ?? ''}`}
        >
          {run.outcome}
        </span>
        {run.error_class && <div className="text-muted-foreground mt-1">{run.error_class}</div>}
      </td>
    </tr>
  );
}
