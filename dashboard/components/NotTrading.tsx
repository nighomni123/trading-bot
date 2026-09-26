"use client";

import { useState } from "react";
import { bpsSigned, num } from "@/lib/format";

/**
 * The "Why Are We Not Trading?" widget.
 * Collapsible, persistent across modules, and deliberately the only
 * decorative affordance in the app.
 */
export type NotTradingData = {
  /** Largest gross effect the system can actually produce. */
  signalBps: number;
  signalSource: string;
  /** Round-trip cost the signal must clear. */
  costBps: number;
  costSource: string;
  /** Oracle bound: what a perfect model would earn at that geometry. */
  oracleBps: number | null;
  /** Median share of required hit-rate actually achieved. */
  attainment: number;
  blockers: { label: string; detail: string }[];
};

export default function NotTrading({ d }: { d: NotTradingData }) {
  const [open, setOpen] = useState(true);
  const gap = d.signalBps - d.costBps;

  return (
    <div className="card border-l-2 border-l-[color:var(--color-fail)]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left"
        aria-expanded={open}
      >
        <span className="badge badge-fail shrink-0">NOT TRADING</span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink">
          Signal{" "}
          <span className="mono t-warn">{bpsSigned(d.signalBps)}</span> bps vs required{" "}
          <span className="mono t-fail">{bpsSigned(d.costBps)}</span> bps —{" "}
          <span className="mono t-fail">gap {bpsSigned(gap)} bps</span>
        </span>
        <span className="eyebrow shrink-0">{open ? "collapse" : "expand"}</span>
      </button>

      {open && (
        <div className="border-t border-[color:var(--color-border-subtle)] px-4 py-3">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-4">
            <Cell
              k="Current signal"
              v={bpsSigned(d.signalBps)}
              unit="bps"
              tone="warn"
              sub={d.signalSource}
            />
            <Cell k="Break-even cost" v={bpsSigned(d.costBps)} unit="bps" tone="fail" sub={d.costSource} />
            <Cell
              k="Gap"
              v={bpsSigned(gap)}
              unit="bps"
              tone="fail"
              sub="signal − cost"
            />
            <Cell
              k="Attainment"
              v={`${num(d.attainment * 100, 1)}`}
              unit="%"
              tone="fail"
              sub="p_target / p_target required"
            />
          </div>

          {d.oracleBps != null && (
            <p className="mt-3 border-l-2 border-[color:var(--color-border)] pl-3 text-[11.5px] leading-relaxed text-[#a3aeb9]">
              The oracle bound is <span className="mono t-pass">+{bpsSigned(d.oracleBps)} bps</span> at
              this geometry — a perfect-foresight model <em>would</em> clear cost. The failure is
              therefore in <strong className="text-ink">model capacity</strong>, not in the cost
              stack: Stage 8 measures the opportunity as absent <em>before</em> costs are applied.
            </p>
          )}

          <div className="mt-3">
            <div className="eyebrow mb-1.5">Active research blockers</div>
            <ol className="space-y-1">
              {d.blockers.map((b, i) => (
                <li key={b.label} className="flex gap-2.5 text-[11px] leading-snug">
                  <span className="mono shrink-0 text-[color:var(--color-ink-faint)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <span className="text-ink">{b.label}</span>
                    <span className="text-[color:var(--color-ink-faint)]"> — {b.detail}</span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}

function Cell({
  k,
  v,
  unit,
  tone,
  sub,
}: {
  k: string;
  v: string;
  unit?: string;
  tone: "pass" | "fail" | "warn" | "info";
  sub?: string;
}) {
  return (
    <div>
      <div className="eyebrow">{k}</div>
      <div className={`mono text-[19px] t-${tone} leading-tight`}>
        {v}
        {unit && <span className="ml-0.5 text-[10px] t-faint">{unit}</span>}
      </div>
      {sub && <div className="mono text-[9.5px] leading-tight t-faint mt-0.5">{sub}</div>}
    </div>
  );
}
