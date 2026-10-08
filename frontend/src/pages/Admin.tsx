import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, KeyRound, RefreshCw, ShieldAlert, Trash2, XCircle } from 'lucide-react';
import { api } from '@/services/api';
import { useAppStore } from '@/store';
import type { AdminRun } from '@/types';

function formatSeconds(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
}

function errorDetail(err: unknown): string {
  const e = err as { response?: { data?: { detail?: string } }; message?: string };
  return e?.response?.data?.detail || e?.message || 'Request failed';
}

export default function Admin() {
  const { adminToken, setAdminToken } = useAppStore();
  const [draft, setDraft] = useState(adminToken ?? '');
  const queryClient = useQueryClient();

  const runsQuery = useQuery({
    queryKey: ['admin-runs', adminToken],
    queryFn: () => api.adminListRuns(adminToken as string),
    enabled: !!adminToken,
    refetchInterval: 3000,
    retry: false,
  });

  const cancelMutation = useMutation({
    mutationFn: (id?: string) => api.adminCancelRuns(adminToken as string, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin-runs'] }),
  });

  const data = runsQuery.data;
  const total = (data?.running.length ?? 0) + (data?.queued.length ?? 0);

  return (
    <div className="p-6 max-w-5xl mx-auto flex flex-col gap-6">
      <div className="flex items-start gap-3">
        <ShieldAlert className="w-7 h-7 text-primary mt-1 shrink-0" />
        <div>
          <h1 className="text-2xl font-bold">Admin</h1>
          <p className="text-sm text-muted-foreground">
            See which analyses are occupying the backend and clear the queue. The server runs one
            analysis at a time; everything else waits behind it. Cancelled runs stop within a second
            or two and free their slot.
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
        <p className="text-xs text-muted-foreground">
          Stored only in this browser. Admin features are off until the backend has a{' '}
          <code className="font-mono bg-secondary px-1 rounded">PYATPG_ADMIN_TOKEN</code> environment
          variable (set it in Render → Environment).
        </p>
      </div>

      {runsQuery.isError && (
        <div className="p-4 rounded-md border border-red-500/50 bg-red-500/10 text-red-400 text-sm flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{errorDetail(runsQuery.error)}</span>
        </div>
      )}

      {adminToken && data && (
        <div className="bg-card border border-border rounded-lg">
          <div className="flex items-center justify-between p-4 border-b border-border">
            <div className="text-sm">
              <span className="font-medium">{data.running.length}</span> running ·{' '}
              <span className="font-medium">{data.queued.length}</span> queued ·{' '}
              <span className="text-muted-foreground">{data.slots} slot(s)</span>
              <RefreshCw
                className={`inline w-3.5 h-3.5 ml-2 text-muted-foreground ${
                  runsQuery.isFetching ? 'animate-spin' : ''
                }`}
              />
            </div>
            <button
              onClick={() => cancelMutation.mutate(undefined)}
              disabled={total === 0 || cancelMutation.isPending}
              className="flex items-center gap-2 bg-red-600 hover:bg-red-600/90 text-white px-4 py-1.5 rounded-md text-sm font-medium disabled:opacity-50"
            >
              <Trash2 className="w-4 h-4" /> Clear queue (cancel all)
            </button>
          </div>

          {total === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">The backend is idle.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground border-b border-border">
                  <th className="p-3 font-medium">State</th>
                  <th className="p-3 font-medium">Current step</th>
                  <th className="p-3 font-medium">Running for</th>
                  <th className="p-3 font-medium">Browser</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {[...data.running, ...data.queued].map((run) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    onCancel={() => cancelMutation.mutate(run.id)}
                    busy={cancelMutation.isPending}
                  />
                ))}
              </tbody>
            </table>
          )}

          {cancelMutation.isError && (
            <p className="p-4 text-sm text-red-400">{errorDetail(cancelMutation.error)}</p>
          )}
        </div>
      )}
    </div>
  );
}

function RunRow({ run, onCancel, busy }: { run: AdminRun; onCancel: () => void; busy: boolean }) {
  return (
    <tr className="border-b border-border last:border-0">
      <td className="p-3">
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            run.state === 'running' ? 'bg-primary/15 text-primary' : 'bg-secondary text-muted-foreground'
          }`}
        >
          {run.cancelled ? 'cancelling' : run.state}
        </span>
      </td>
      <td className="p-3">
        {run.stage}
        <span className="text-xs text-muted-foreground ml-2 font-mono">
          {formatSeconds(run.stage_elapsed_s)}
        </span>
      </td>
      <td className="p-3 font-mono text-xs">{formatSeconds(run.elapsed_s)}</td>
      <td className="p-3 text-xs">
        {run.client_connected ? 'waiting' : <span className="text-amber-500">gone</span>}
      </td>
      <td className="p-3 text-right">
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
