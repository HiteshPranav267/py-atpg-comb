import { useQuery } from '@tanstack/react-query';
import { BarChart3, Cpu, GitBranch, Layers, ShieldQuestion, Sparkles } from 'lucide-react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '@/services/api';
import { Section, Stat, Pill, KeyValue, metric } from '@/components/atpg/primitives';
import type { AnalyticsActivityEvent } from '@/types';

const EVENT_LABELS: Record<string, string> = {
  circuit_parsed: 'Circuit parsed',
  atpg_run: 'ATPG run',
  ranking_run: 'Ranking run',
  trojan_generated: 'Trojan dataset generated',
  benchmark_run: 'Benchmark run',
};

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

export default function Analytics() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['analytics-summary'],
    queryFn: api.getAnalyticsSummary,
    refetchInterval: 30000,
  });

  return (
    <div className="p-8 max-w-6xl mx-auto w-full flex flex-col gap-6">
      <div className="flex items-center gap-4">
        <BarChart3 className="w-8 h-8 text-primary" />
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
          <p className="text-sm text-muted-foreground">
            What this instance of PyATPG has actually done — circuits parsed, ATPG runs and their
            coverage, LLM usage, and other activity. Tracked in the backend, not by a third party.
          </p>
        </div>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {isError && (
        <p className="text-sm text-red-400">
          Could not load analytics: {(error as Error)?.message ?? 'unknown error'}
        </p>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Circuits parsed" value={data.circuits_parsed} />
            <Stat label="ATPG runs" value={data.atpg_runs.total} emphasis />
            <Stat label="Ranking runs" value={data.ranking_runs} />
            <Stat label="Trojan datasets generated" value={data.trojan_generations} />
          </div>

          <Section
            title="ATPG runs by circuit kind"
            subtitle="Combinational circuits run through the ATPG tool (Atalanta); circuits with flip-flops run through the native sequential (time-frame-expansion) engine."
            actions={<Cpu className="w-4 h-4 text-muted-foreground" />}
          >
            <div className="grid grid-cols-2 gap-4">
              <div className="border border-border rounded-lg p-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Combinational
                </span>
                <div className="text-2xl font-bold tabular-nums mt-1">{data.atpg_runs.combinational}</div>
                <div className="text-xs text-muted-foreground mt-1">
                  Avg. coverage: {metric(data.atpg_runs.avg_coverage_combinational, { suffix: ' %' })}
                </div>
              </div>
              <div className="border border-border rounded-lg p-4">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Sequential
                </span>
                <div className="text-2xl font-bold tabular-nums mt-1">{data.atpg_runs.sequential}</div>
                <div className="text-xs text-muted-foreground mt-1">
                  Avg. coverage: {metric(data.atpg_runs.avg_coverage_sequential, { suffix: ' %' })}
                </div>
              </div>
            </div>
          </Section>

          {data.coverage_over_time.length > 1 && (
            <Section
              title="Fault coverage over time"
              subtitle="Each point is one ATPG run, in the order it happened."
              actions={<GitBranch className="w-4 h-4 text-muted-foreground" />}
            >
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={data.coverage_over_time.map((p, i) => ({
                      index: i + 1,
                      coverage: p.coverage,
                      label: p.circuit_name ?? '?',
                    }))}
                  >
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis dataKey="index" tick={{ fontSize: 11 }} className="fill-muted-foreground" />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fontSize: 11 }}
                      className="fill-muted-foreground"
                      unit="%"
                    />
                    <Tooltip
                      contentStyle={{ fontSize: 12 }}
                      labelFormatter={(i) => `Run #${i}`}
                      formatter={(value, _name, item) => [
                        `${value} %`,
                        (item?.payload as { label?: string } | undefined)?.label,
                      ]}
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line
                      type="monotone"
                      dataKey="coverage"
                      name="Fault coverage"
                      stroke="var(--color-primary, #6366f1)"
                      strokeWidth={2}
                      dot={{ r: 3 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Section>
          )}

          <Section
            title="LLM usage"
            subtitle="Aggregated across every ATPG run that had an API key configured. Sequential runs report real token estimates (see the context-reduction design in the ATPG page); combinational runs currently only report whether the LLM was used."
            actions={<Sparkles className="w-4 h-4 text-muted-foreground" />}
          >
            <KeyValue
              rows={[
                ['Runs with LLM assistance', metric(data.llm.runs_with_llm)],
                ['Total API calls', metric(data.llm.total_api_calls)],
                ['Total tokens (est.)', metric(data.llm.total_tokens_est)],
              ]}
            />
          </Section>

          <Section
            title={`Recent activity (${data.recent_activity.length.toLocaleString()})`}
            subtitle="Most recent first."
            actions={<Layers className="w-4 h-4 text-muted-foreground" />}
          >
            {data.recent_activity.length === 0 ? (
              <p className="text-sm text-muted-foreground flex items-center gap-2">
                <ShieldQuestion className="w-4 h-4" /> No activity recorded yet.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground border-b border-border">
                      <th className="py-2 pr-4 font-medium">When</th>
                      <th className="py-2 pr-4 font-medium">Event</th>
                      <th className="py-2 font-medium">Details</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-xs">
                    {data.recent_activity.map((event, i) => (
                      <ActivityRow key={i} event={event} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

function ActivityRow({ event }: { event: AnalyticsActivityEvent }) {
  const { ts, type, ...rest } = event;
  const details = Object.entries(rest)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${v}`)
    .join(' ');
  return (
    <tr className="border-b border-border/50">
      <td className="py-1.5 pr-4 text-muted-foreground whitespace-nowrap">{formatTime(ts)}</td>
      <td className="py-1.5 pr-4">
        <Pill tone="info">{EVENT_LABELS[type] ?? type}</Pill>
      </td>
      <td className="py-1.5 text-muted-foreground break-all">{details || '-'}</td>
    </tr>
  );
}
