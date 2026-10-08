import { Brain, Cpu, GitCompareArrows, Info, Link2, ShieldAlert, ShieldCheck } from 'lucide-react';
import type {
  CircuitAnalysis,
  CorrelationReport,
  LlmUsage,
  RunComparison,
  TargetingInfo,
  TrojanVerdict,
} from '@/types';
import { KeyValue, Pill, Section, Stat, metric } from './primitives';

export function VerdictPanel({ verdict }: { verdict: TrojanVerdict }) {
  const suspected = verdict.verdict === 'trojan_suspected';
  const inconclusive = verdict.verdict === 'inconclusive';
  return (
    <Section
      title="Trojan verdict"
      subtitle="Trigger-like logic (never-toggling gate groups, logic-clocked registers, rare conditions ATPG never produced), with weaker structural leads after it."
      actions={
        suspected ? (
          <ShieldAlert className="w-4 h-4 text-red-400" />
        ) : (
          <ShieldCheck className="w-4 h-4 text-muted-foreground" />
        )
      }
    >
      <div className="flex items-center gap-2 mb-2">
        <Pill tone={suspected ? 'warn' : 'neutral'}>
          {suspected ? 'Trojan suspected' : inconclusive ? 'Inconclusive' : 'No trojan indicated'}
        </Pill>
        {(suspected || inconclusive) && (
          <span className="text-xs text-muted-foreground font-mono">
            top confidence {verdict.confidence.toFixed(2)}
          </span>
        )}
      </div>
      <p className="text-sm leading-relaxed">{verdict.summary}</p>

      {verdict.suspects.length > 0 && (
        <div className="flex flex-col gap-3 mt-5">
          {verdict.suspects.map((item) => (
            <div key={item.gate} className="border border-border rounded-md p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-mono text-sm text-primary font-bold">{item.gate}</span>
                <Pill tone={item.atpg_status === 'detected' ? 'warn' : 'neutral'}>
                  {item.atpg_status}
                </Pill>
                <span className="text-[11px] text-muted-foreground font-mono">{item.evidence}</span>
                <span className="text-xs text-muted-foreground font-mono ml-auto">
                  confidence {item.confidence.toFixed(2)}
                </span>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">{item.statement}</p>
              {item.reasons.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1">
                  {item.reasons.map((reason) => (
                    <li key={reason} className="text-xs text-muted-foreground/80 font-mono leading-relaxed">
                      {reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      <ul className="mt-5 flex flex-col gap-2 border-t border-border pt-4">
        {verdict.caveats.map((caveat) => (
          <li key={caveat} className="text-xs text-muted-foreground leading-relaxed flex gap-2">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            {caveat}
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function ComparisonPanel({ comparison }: { comparison: RunComparison }) {
  const simulated =
    comparison.targeted_simulated_coverage != null &&
    comparison.non_targeted_simulated_coverage != null;
  return (
    <Section
      title="Targeted vs non-targeted"
      subtitle="Both sets of numbers come from separate ATPG runs on the same netlist."
      actions={<GitCompareArrows className="w-4 h-4 text-muted-foreground" />}
    >
      {/* The one like-for-like pair: each run's patterns fault-simulated over
          the same whole-circuit fault list. Shown first because every other
          coverage figure below has a different denominator. */}
      {simulated && (
        <div className="mb-5">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
            Whole-circuit coverage of each pattern set (same fault list)
          </h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat
              label="Targeted patterns"
              value={metric(comparison.targeted_simulated_coverage, { suffix: ' %' })}
              hint={`${comparison.targeted_patterns ?? '?'} patterns simulated over ${comparison.simulated_fault_count?.toLocaleString() ?? '?'} circuit faults`}
              emphasis
            />
            <Stat
              label="Non-targeted patterns"
              value={metric(comparison.non_targeted_simulated_coverage, { suffix: ' %' })}
              hint={`${comparison.non_targeted_patterns ?? '?'} patterns simulated over the same faults`}
            />
            <Stat
              label="Coverage delta"
              value={metric(comparison.simulated_coverage_delta, { suffix: ' pts' })}
              hint="targeted − non-targeted, like for like"
            />
            <Stat
              label="Circuit faults simulated"
              value={comparison.simulated_fault_count}
              hint="gate-output and primary-input stuck-at faults, uncollapsed"
            />
          </div>
        </div>
      )}

      <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
        As reported by the ATPG tool (each over its own fault list)
      </h4>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {/* Each coverage figure carries its own fault count: the targeted run
            measures over only the gates it selected, so a small enough list
            reaches 100% without covering much of the circuit. */}
        <Stat
          label="Targeted coverage"
          value={metric(comparison.targeted_coverage, { suffix: ' %' })}
          hint={
            comparison.targeted_faults != null
              ? `of ${comparison.targeted_faults} selected faults` +
                (comparison.targeted_fault_share_pct != null
                  ? ` (${comparison.targeted_fault_share_pct}% of circuit)`
                  : '')
              : undefined
          }
        />
        <Stat
          label="Non-targeted coverage"
          value={metric(comparison.non_targeted_coverage, { suffix: ' %' })}
          hint={
            comparison.non_targeted_faults != null
              ? `of ${comparison.non_targeted_faults} circuit faults`
              : undefined
          }
        />
        <Stat
          label="Coverage delta"
          value={metric(comparison.coverage_delta, { suffix: ' pts' })}
          hint="different denominators -- not like for like"
        />
        <Stat
          label="Pattern reduction"
          value={metric(comparison.pattern_reduction_pct, { suffix: ' %' })}
        />
        <Stat
          label="Targeted detections, share of circuit"
          value={metric(comparison.targeted_circuit_wide_coverage, { suffix: ' %' })}
          hint="only the faults in the targeted list; a lower bound"
        />
      </div>

      <div className="mt-4">
        <KeyValue
          rows={[
            ['Targeted patterns', metric(comparison.targeted_patterns)],
            ['Non-targeted patterns', metric(comparison.non_targeted_patterns)],
            ['Targeted CPU total', metric(comparison.targeted_runtime, { suffix: ' s' })],
            ['Non-targeted CPU total', metric(comparison.non_targeted_runtime, { suffix: ' s' })],
          ]}
        />
      </div>

      {comparison.notes.map((note) => (
        <div
          key={note}
          className="mt-4 flex gap-2 p-3 rounded-md border border-yellow-500/40 bg-yellow-500/10 text-yellow-500 text-xs leading-relaxed"
        >
          <Info className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{note}</span>
        </div>
      ))}
    </Section>
  );
}

export function CircuitAnalysisPanel({ analysis }: { analysis: CircuitAnalysis }) {
  const fromModel = analysis.source === 'llm';

  return (
    <Section
      title="Circuit analysis"
      subtitle={
        fromModel
          ? `Interpretation produced by ${analysis.model ?? 'a language model'}. It contributes no ATPG measurements.`
          : 'Computed from SCOAP controllability/observability and simulated toggle rates. No language model was used.'
      }
      actions={
        <div className="flex items-center gap-2">
          <Brain className="w-4 h-4 text-muted-foreground" />
          <Pill tone={fromModel ? 'info' : 'neutral'}>
            {fromModel ? 'Model-generated' : 'Structural'}
          </Pill>
        </div>
      }
    >
      {analysis.unavailable_reason && (
        <p className="text-sm text-yellow-500 mb-4">{analysis.unavailable_reason}</p>
      )}

      {analysis.architecture_summary && (
        <p className="text-sm leading-relaxed mb-5">{analysis.architecture_summary}</p>
      )}

      <div className="flex flex-col gap-4">
        {analysis.observations.map((observation) => (
          <div key={observation.title} className="border border-border rounded-md p-4">
            <div className="flex items-center justify-between gap-3 mb-1">
              <h4 className="font-medium text-sm">{observation.title}</h4>
              {observation.confidence && (
                <Pill tone="neutral">confidence: {observation.confidence}</Pill>
              )}
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed">{observation.detail}</p>
            {observation.gates.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-3">
                {observation.gates.map((gate) => (
                  <span
                    key={gate}
                    className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-secondary text-primary"
                  >
                    {gate}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {analysis.suspicious_regions.length > 0 && (
        <div className="mt-5">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
            Structurally interesting regions
          </h4>
          <div className="flex flex-col gap-3">
            {analysis.suspicious_regions.map((region) => (
              <div key={region.name} className="border border-border rounded-md p-4">
                <div className="flex items-center justify-between gap-3 mb-1">
                  <h5 className="font-medium text-sm">{region.name}</h5>
                  {region.confidence && <Pill tone="neutral">confidence: {region.confidence}</Pill>}
                </div>
                <p className="text-sm text-muted-foreground leading-relaxed">{region.rationale}</p>
                {region.gates.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-3">
                    {region.gates.map((gate) => (
                      <span
                        key={gate}
                        className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-secondary text-primary"
                      >
                        {gate}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {analysis.reasoning.length > 0 && (
        <ul className="mt-5 flex flex-col gap-2">
          {analysis.reasoning.map((line) => (
            <li key={line} className="text-xs text-muted-foreground leading-relaxed flex gap-2">
              <span className="text-primary">·</span>
              {line}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function CorrelationPanel({ correlation }: { correlation: CorrelationReport }) {
  return (
    <Section
      title="Correlated insights"
      subtitle="Where the circuit analysis and the ATPG tool's fault results happen to point at the same gate."
      actions={<Link2 className="w-4 h-4 text-muted-foreground" />}
    >
      <p className="text-sm leading-relaxed">{correlation.summary}</p>

      <div className="flex gap-4 mt-3 text-xs text-muted-foreground">
        <span>{correlation.analysis_gates_examined} gate(s) raised by analysis</span>
        <span>·</span>
        <span>{correlation.undetected_gates_examined} gate(s) with undetected faults</span>
      </div>

      {correlation.correlated.length > 0 && (
        <div className="flex flex-col gap-3 mt-5">
          {correlation.correlated.map((item) => (
            <div key={item.gate} className="border border-border rounded-md p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-mono text-sm text-primary font-bold">{item.gate}</span>
                <Pill tone="warn">{item.atpg_status}</Pill>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">{item.statement}</p>
              {item.fault_names.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {item.fault_names.map((fault) => (
                    <span
                      key={fault}
                      className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-secondary"
                    >
                      {fault}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <ul className="mt-5 flex flex-col gap-2 border-t border-border pt-4">
        {correlation.caveats.map((caveat) => (
          <li key={caveat} className="text-xs text-muted-foreground leading-relaxed flex gap-2">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            {caveat}
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** Human labels for the stages the backend logs model calls under. */
const STAGE_LABELS: Record<string, string> = {
  targeting: 'Gate selection (targeting)',
  analysis: 'Circuit analysis',
  llm1: 'Circuit summary (LLM-1)',
  llm2: 'Fault prioritisation (LLM-2)',
};

export function LlmUsagePanel({
  usage,
  targeting,
}: {
  usage: LlmUsage;
  targeting: TargetingInfo | null;
}) {
  const exact = usage.tokens_exact !== false;
  const suffix = exact ? '' : ' (est.)';
  const stages = Object.entries(usage.by_stage ?? {});
  const context = targeting?.context;
  return (
    <Section
      title="LLM usage"
      subtitle={
        <>
          Every model call this run made
          {usage.provider ? ` via ${usage.provider}` : ''}
          {usage.model ? ` / ${usage.model}` : ''}.{' '}
          {exact
            ? "Token counts are the provider's own usage report."
            : 'The provider reported no usage, so token counts are estimated as characters ÷ 4.'}
        </>
      }
      actions={<Cpu className="w-4 h-4 text-muted-foreground" />}
    >
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="API calls" value={usage.api_calls ?? 0} />
        <Stat
          label="Cached hits"
          value={usage.cached_hits ?? 0}
          hint="answers reused from an earlier identical run; cost no tokens"
        />
        <Stat label={`Prompt tokens${suffix}`} value={usage.prompt_tokens_est ?? 0} />
        <Stat label={`Response tokens${suffix}`} value={usage.response_tokens_est ?? 0} />
      </div>

      {stages.length > 0 && (
        <div className="mt-4">
          <KeyValue
            rows={stages.map(([stage, s]) => [
              STAGE_LABELS[stage] ?? stage,
              `${(s.prompt_tokens_est ?? 0).toLocaleString()} in / ${(s.response_tokens_est ?? 0).toLocaleString()} out` +
                (s.calls ? ` · ${s.calls} call${s.calls === 1 ? '' : 's'}` : '') +
                (s.cached_hits ? ` · ${s.cached_hits} cached` : ''),
            ])}
          />
        </div>
      )}

      {context && context.gates_total != null && (
        <p className="text-xs text-muted-foreground mt-4 leading-relaxed border-t border-border pt-4">
          Context sent for gate selection: {context.gates_sent?.toLocaleString()} of{' '}
          {context.gates_total.toLocaleString()} gate profiles
          {context.gate_limit ? ` (cap ${context.gate_limit.toLocaleString()}; LLM_CONTEXT_GATES=0 sends all)` : ''}
          , as one CSV row per gate.
        </p>
      )}
    </Section>
  );
}
