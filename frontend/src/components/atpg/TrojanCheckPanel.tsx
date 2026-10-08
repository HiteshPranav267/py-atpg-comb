import { AlertTriangle, Crosshair, Info } from 'lucide-react';
import type { AtpgReport, SelectionScore } from '@/types';
import { Pill, Section, Stat } from './primitives';

const RUN_LABEL: Record<string, string> = {
  non_targeted: 'Non-targeted',
  targeted: 'Targeted',
};

function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

/**
 * What coverage cannot show: whether the patterns ever drive the circuit's
 * rare nets (where a Trojan trigger hides) to their rare value, and -- for
 * designs whose Trojan gates are recognisable by name -- how the gate
 * selection and suspects score against them.
 */
export function TrojanCheckPanel({ report }: { report: AtpgReport }) {
  const runs = Object.entries(report.runs).filter(([, run]) => run?.rare_nodes && !run.rare_nodes.unavailable_reason);
  const truth = report.trojan_ground_truth;
  if (runs.length === 0 && !truth) return null;

  const neverFires = truth?.redundant_trojan_faults
    ? Object.values(truth.redundant_trojan_faults).find((r) => r.trigger_never_fires)
    : undefined;
  const watchedRun = runs.find(([, run]) => run!.rare_nodes!.watched?.length);
  const watched = watchedRun?.[1]!.rare_nodes!.watched ?? [];
  const keyWatched = watched.filter((w) => w.is_rare || /trigger/i.test(w.net)).slice(0, 12);

  return (
    <Section
      title="Trojan check"
      subtitle="Fault coverage cannot reveal a Trojan: its trigger is a rare condition. This shows whether the patterns ever reach the circuit's rare nets, and scores the run against the Trojan's own gates when they can be identified."
      actions={<Crosshair className="w-4 h-4 text-muted-foreground" />}
    >
      {neverFires && (
        <div className="mb-4 p-3 rounded-md border border-red-500/40 bg-red-500/10 text-sm flex gap-2">
          <AlertTriangle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <div>
            <div className="font-medium text-red-400">The Trojan's trigger can never fire</div>
            <div className="text-muted-foreground mt-1 leading-relaxed">
              The ATPG tool proved {neverFires.trigger_faults.map((f) => <code key={f} className="font-mono mx-1">{f}</code>)}
              untestable: no input pattern drives the trigger to 1. Under full scan every register
              state is allowed, so the trigger condition is contradictory and the payload is never
              activated in any operation. {neverFires.count} fault(s) on the Trojan logic are untestable
              for this reason, which is why coverage drops.
            </div>
          </div>
        </div>
      )}

      {runs.length > 0 && (
        <div className="flex flex-col gap-4">
          {runs.map(([key, run]) => {
            const rn = run!.rare_nodes!;
            return (
              <div key={key}>
                <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                  {RUN_LABEL[key] ?? key} patterns
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <Stat label="Rare nets" value={rn.rare_nodes ?? null} hint={`value seen in < ${((rn.threshold ?? 0.05) * 100).toFixed(0)}% of random patterns`} />
                  <Stat label="Activated" value={rn.activation_pct != null ? `${rn.activation_pct}%` : null} hint={`${rn.activated} reached their rare value at least once`} emphasis />
                  <Stat label={`Activated ≥ ${rn.n_detect}×`} value={rn.n_detect_pct != null ? `${rn.n_detect_pct}%` : null} hint="N-detect criterion" />
                  <Stat label="Never activated" value={rn.never_activated_count ?? null} hint="where a trigger can hide" />
                </div>
                {!!rn.never_activated?.length && (
                  <div className="mt-2 text-xs text-muted-foreground leading-relaxed">
                    Rarest never-activated:{' '}
                    {rn.never_activated.slice(0, 12).map((n) => (
                      <code key={n.net} className="font-mono mr-2">
                        {n.net}={n.rare_value}
                      </code>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {truth && (
        <div className="mt-5 border-t border-border pt-4 flex flex-col gap-3">
          <div className="text-sm">
            <span className="font-medium">{truth.trojan_gates.length} Trojan gates identified by name</span>
            <span className="text-xs text-muted-foreground"> ({truth.source})</span>
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            <ScoreCard title="Targeted gate selection" score={truth.targeted_selection} />
            <ScoreCard title="Structural suspects" score={truth.structural_suspects} />
            <ScoreCard title="Verdict suspects" score={truth.verdict_suspects} />
          </div>
          {keyWatched.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs min-w-[420px]">
                <thead>
                  <tr className="text-left text-muted-foreground border-b border-border">
                    <th className="py-1.5 pr-3 font-medium">Trojan net</th>
                    <th className="py-1.5 pr-3 font-medium">Rare value</th>
                    <th className="py-1.5 pr-3 font-medium text-right">Random probability</th>
                    <th className="py-1.5 font-medium text-right">Patterns activating ({RUN_LABEL[watchedRun![0]] ?? watchedRun![0]})</th>
                  </tr>
                </thead>
                <tbody>
                  {keyWatched.map((w) => (
                    <tr key={w.net} className="border-b border-border/50 last:border-0">
                      <td className="py-1.5 pr-3 font-mono">{w.net}</td>
                      <td className="py-1.5 pr-3 font-mono">{w.rare_value}</td>
                      <td className="py-1.5 pr-3 font-mono text-right">{w.random_probability}</td>
                      <td className={`py-1.5 font-mono text-right ${w.patterns_activating ? '' : 'text-red-400'}`}>
                        {w.patterns_activating}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {runs[0]?.[1]?.rare_nodes?.note && (
        <p className="mt-4 text-xs text-muted-foreground flex gap-2">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          {runs[0][1]!.rare_nodes!.note}
        </p>
      )}
    </Section>
  );
}

function ScoreCard({ title, score }: { title: string; score?: SelectionScore }) {
  return (
    <div className="border border-border rounded-lg p-3 flex flex-col gap-1">
      <span className="text-xs text-muted-foreground font-medium">{title}</span>
      {score ? (
        <>
          <div className="flex items-baseline gap-3 text-sm">
            <span>
              recall <b className="tabular-nums">{pct(score.recall)}</b>
            </span>
            <span>
              precision <b className="tabular-nums">{pct(score.precision)}</b>
            </span>
          </div>
          <span className="text-[11px] text-muted-foreground">
            {score.trojan_gates_hit.length} of {score.selected} selected gates are Trojan gates
          </span>
          {score.trojan_gates_hit.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {score.trojan_gates_hit.slice(0, 8).map((g) => (
                <Pill key={g} tone="info">
                  {g}
                </Pill>
              ))}
            </div>
          )}
        </>
      ) : (
        <span className="text-xs text-muted-foreground">Not run</span>
      )}
    </div>
  );
}
