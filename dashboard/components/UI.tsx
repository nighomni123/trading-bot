import type { ReactNode } from "react";

/* ---------- Card ---------- */
export function Card({
  title,
  eyebrow,
  right,
  children,
  className = "",
  bodyClass = "p-4",
}: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
}) {
  return (
    <section className={`card flex flex-col ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 px-4 pt-3 pb-2">
          <div className="min-w-0">
            {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
            {title && (
              <h2 className="text-[13px] font-semibold text-ink leading-tight">{title}</h2>
            )}
          </div>
          {right && <div className="shrink-0 flex items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={`flex flex-1 min-h-0 flex-col ${bodyClass}`}>{children}</div>
    </section>
  );
}

/* ---------- Stat: label + big mono value + optional delta ---------- */
export function Stat({
  label,
  value,
  unit,
  tone = "dim",
  sub,
  size = "md",
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  tone?: "pass" | "fail" | "warn" | "info" | "dim" | "ink";
  sub?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  // An absent measurement must never wear a semantic color — a red em-dash
  // reads as a failing value rather than a missing one.
  const missing = value === "—" || value === "—" || value == null;
  const toneClass = missing
    ? "t-faint"
    : { pass: "t-pass", fail: "t-fail", warn: "t-warn", info: "t-info", dim: "t-dim", ink: "text-ink" }[
        tone
      ];
  const sizeClass = { sm: "text-[15px]", md: "text-[20px]", lg: "text-[28px]" }[size];

  return (
    <div className="min-w-0">
      <div className="eyebrow truncate">{label}</div>
      <div className={`mono ${sizeClass} ${toneClass} leading-tight mt-0.5 flex items-baseline gap-1`}>
        <span className="truncate">{value}</span>
        {unit && <span className="text-[10px] t-faint">{unit}</span>}
      </div>
      {sub && <div className="text-[10px] t-faint mt-0.5 mono truncate">{sub}</div>}
    </div>
  );
}

/* ---------- Key/value row: dense data display ---------- */
export function KV({
  k,
  v,
  tone,
  title,
}: {
  k: string;
  v: ReactNode;
  tone?: "pass" | "fail" | "warn" | "info" | "dim";
  title?: string;
}) {
  const toneClass = tone ? `t-${tone}` : "text-ink";
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px]" title={title}>
      <span className="min-w-0 flex-1 truncate text-[11px] t-faint">{k}</span>
      <span className={`mono shrink-0 whitespace-nowrap text-[11px] ${toneClass} text-right`}>{v}</span>
    </div>
  );
}

/* ---------- Badge ---------- */
export function Badge({
  tone = "mute",
  children,
  className = "",
}: {
  tone?: "pass" | "fail" | "warn" | "info" | "mute";
  children: ReactNode;
  className?: string;
}) {
  return <span className={`badge badge-${tone} ${className}`}>{children}</span>;
}

/* ---------- First-principle line ---------- */
export function Principle({ children }: { children: ReactNode }) {
  return <p className="principle">{children}</p>;
}
