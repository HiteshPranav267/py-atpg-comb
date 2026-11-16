import { Scale } from 'lucide-react';
import type { AtpgReport, ThreeWay, ThreeWayRow } from '@/types';
import { Section } from './primitives';

const COLUMNS: { key: 'standard' | 'targeted_no_llm' | 'targeted_llm'; label: string }[] = [
  { key: 'standard', label: 'Standard ATPG' },
  { key: 'targeted_no_llm', label: 'Targeted, no LLM' },
  { key: 'targeted_llm', label: 'Targeted, LLM' },
];

function num(v: number | null | undefined, digits = 0): string {
  return v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

const ROWS: { label: string; value: (r: ThreeWayRow) => string }[] = [
  { label: 'Gates targeted', value: (r) => (r.gates_targeted == null ? 'all' : num(r.gates_targeted)) },
  { label: 'Patterns', value: (r) => num(r.patterns) + (r.pattern_reduction_pct ? ` (−${r.pattern_reduction_pct}%)` : '') },
  { label: 'Tester cycles', value: (r) => num(r.tester_cycles) },
  { label: 'Generation time (s)', value: (r) => num(r.generation_s, 2) },
  { label: 'Whole-circuit coverage (%)', value: (r) => num(r.simulated_coverage, 2) },
  {
    label: 'Trojan faults detected',
    value: (r) => (r.trojan?.trojan_faults ? `${r.trojan.detected} / ${r.trojan.trojan_faults}` : '—'),
  },
  {
    label: 'Patterns to detect them all',
    value: (r) => (r.trojan?.trojan_faults ? num(r.trojan.patterns_to_detect_all) : '—'),
  },
];

/** Standard ATPG next to the two targeted test sets, at the same budget. */
export function ThreeWayPanel({ report }: { report: AtpgReport }) {
  const tw = report.three_way as ThreeWay | undefined;
  if (!tw || !tw.runs || Object.keys(tw.runs).length === 0) return null;
  const cols = COLUMNS.filter((c) => tw.runs[c.key]);
  return (
    <Section
      title="Standard vs targeted ATPG"
      subtitle={`The same netlist three ways. The two targeted lists test the same number of gates; only how they were chosen differs. Tester cycles = patterns × (${tw.scan_cells.toLocaleString()} scan cells + 1).`}
      actions={<Scale className="w-4 h-4 text-muted-foreground" />}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              <th className="text-left font-medium py-1.5 pr-4" />
              {cols.map((c) => (
                <th key={c.key} className="text-right font-medium py-1.5 px-2">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => (
              <tr key={row.label} className="border-t border-border">
                <td className="py-1.5 pr-4 text-muted-foreground">{row.label}</td>
                {cols.map((c) => (
                  <td key={c.key} className="py-1.5 px-2 text-right font-mono">
                    {row.value(tw.runs[c.key]!)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-3 text-xs text-muted-foreground leading-relaxed list-disc pl-4">
        {tw.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </Section>
  );
}
