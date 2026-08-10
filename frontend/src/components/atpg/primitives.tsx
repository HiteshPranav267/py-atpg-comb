import type { ReactNode } from 'react';

/** Shown wherever Atalanta did not report a value. Never substitute 0. */
export const UNREPORTED = 'Not reported by Atalanta';

/**
 * Render a metric that Atalanta may not have produced.
 * `null`/`undefined` becomes the explicit "not reported" marker so a missing
 * measurement can never be mistaken for a real zero.
 */
export function metric(
  value: number | string | null | undefined,
  opts: { suffix?: string; digits?: number } = {}
): string {
  if (value === null || value === undefined) return UNREPORTED;
  const { suffix = '', digits } = opts;
  const shown =
    typeof value === 'number' && digits !== undefined ? value.toFixed(digits) : String(value);
  return `${shown}${suffix}`;
}

export function Stat({
  label,
  value,
  hint,
  emphasis = false,
}: {
  label: string;
  value: number | string | null | undefined;
  hint?: string;
  emphasis?: boolean;
}) {
  const text = typeof value === 'string' ? value : metric(value);
  const missing = text === UNREPORTED;
  return (
    <div className="border border-border bg-card p-4 rounded-lg flex flex-col gap-1">
      <span className="text-xs text-muted-foreground font-medium">{label}</span>
      <span
        className={[
          'font-bold tabular-nums',
          missing ? 'text-xs text-muted-foreground font-normal leading-snug' : 'text-xl',
          !missing && emphasis ? 'text-primary' : '',
        ].join(' ')}
        title={missing ? 'Atalanta did not report this value for this run.' : undefined}
      >
        {text}
      </span>
      {hint && <span className="text-[11px] text-muted-foreground leading-snug">{hint}</span>}
    </div>
  );
}

export function Section({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="bg-card border border-border rounded-lg overflow-hidden">
      <header className="px-5 py-4 border-b border-border flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="font-semibold">{title}</h3>
          {subtitle && (
            <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{subtitle}</p>
          )}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

export function KeyValue({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="grid sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
      {rows.map(([key, value]) => (
        <div key={key} className="flex justify-between gap-4 border-b border-border/50 py-1.5">
          <dt className="text-muted-foreground">{key}</dt>
          <dd
            className={
              value === UNREPORTED
                ? 'text-muted-foreground text-xs self-center text-right'
                : 'font-mono text-right'
            }
          >
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'warn' | 'info' }) {
  const tones = {
    neutral: 'bg-secondary text-muted-foreground border-border',
    warn: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/40',
    info: 'bg-primary/10 text-primary border-primary/40',
  };
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded border ${tones[tone]}`}>{children}</span>
  );
}

/** Client-side pagination for the fault and vector tables. */
export function Paginator({
  page,
  pageCount,
  total,
  onPage,
}: {
  page: number;
  pageCount: number;
  total: number;
  onPage: (page: number) => void;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="flex items-center justify-between mt-3 text-xs text-muted-foreground">
      <span>
        Page {page + 1} of {pageCount} · {total.toLocaleString()} rows
      </span>
      <div className="flex gap-2">
        <button
          onClick={() => onPage(Math.max(0, page - 1))}
          disabled={page === 0}
          className="px-2 py-1 border border-border rounded disabled:opacity-40 hover:bg-secondary"
        >
          Previous
        </button>
        <button
          onClick={() => onPage(Math.min(pageCount - 1, page + 1))}
          disabled={page >= pageCount - 1}
          className="px-2 py-1 border border-border rounded disabled:opacity-40 hover:bg-secondary"
        >
          Next
        </button>
      </div>
    </div>
  );
}
