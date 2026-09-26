"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { bps } from "@/lib/format";

export type NavItem = {
  href: string;
  label: string;
  stage: string;
  /** Short status shown as a dot on the right. */
  dot?: "pass" | "fail" | "warn" | "info" | "mute";
};

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Gates",
    items: [
      { href: "/", label: "Why Not Trading", stage: "OVERVIEW", dot: "fail" },
      { href: "/gate", label: "Epistemic Gate", stage: "PIPELINE", dot: "fail" },
    ],
  },
  {
    group: "Diagnostics",
    items: [
      { href: "/geometry", label: "Geometry & Oracle Grid", stage: "S10", dot: "info" },
      { href: "/microstructure", label: "Microstructure Lab", stage: "S11", dot: "fail" },
      { href: "/execution", label: "Execution Telemetry", stage: "S10b", dot: "info" },
    ],
  },
  {
    group: "Integrity",
    items: [
      { href: "/integrity", label: "Walk-Forward & Parity", stage: "S09", dot: "pass" },
      { href: "/horizon", label: "Long-Horizon Alpha", stage: "S12", dot: "warn" },
    ],
  },
  {
    group: "Runtime",
    items: [{ href: "/runtime", label: "Shadow Telemetry", stage: "S13", dot: "pass" }],
  },
];

const DOT: Record<string, string> = {
  pass: "bg-[color:var(--color-pass)]",
  fail: "bg-[color:var(--color-fail)]",
  warn: "bg-[color:var(--color-warn)]",
  info: "bg-[color:var(--color-info)]",
  violet: "bg-[color:var(--color-violet)]",
  mute: "bg-[color:var(--color-ink-faint)]",
};

export default function Sidebar({
  measuredRt,
  configuredRt,
}: {
  measuredRt: number;
  configuredRt: number;
}) {
  const path = usePathname();
  return (
    <nav className="sticky top-11 flex max-h-[calc(100vh-2.75rem)] w-[218px] shrink-0 flex-col border-r border-[color:var(--color-border-subtle)] bg-[color:var(--color-obsidian)]">
      <div className="min-h-0 flex-1 overflow-y-auto py-3">
        {NAV.map((g) => (
          <div key={g.group} className="mb-4">
            <div className="eyebrow px-3 pb-1.5">{g.group}</div>
            {g.items.map((it) => {
              const active = path === it.href;
              return (
                <Link
                  key={it.href}
                  href={it.href}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-2 px-3 py-[5px] text-[12px] border-l-2 transition-colors ${
                    active
                      ? "border-l-[color:var(--color-info)] bg-[color:var(--color-panel)] text-ink"
                      : "border-l-transparent text-[color:var(--color-ink-dim)] hover:bg-[color:var(--color-panel)] hover:text-ink"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">{it.label}</span>
                  <span
                    className={`eyebrow shrink-0 text-[9px] ${
                      active ? "text-[color:#9aa5b1]" : ""
                    }`}
                  >
                    {it.stage}
                  </span>
                  {it.dot && (
                    <span className={`h-[5px] w-[5px] shrink-0 rounded-full ${DOT[it.dot]}`} aria-hidden />
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </div>

      {/* Reference material — the rail should carry load, not whitespace. */}
      <div className="shrink-0 space-y-3.5 border-t border-[color:var(--color-border-subtle)] px-3 py-3">
        <div>
          <div className="eyebrow mb-1.5">Cost basis</div>
          <div className="mono flex items-baseline justify-between text-[10.5px]">
            <span className="t-faint">measured</span>
            <span className="t-fail">{bps(measuredRt)} bps</span>
          </div>
          <div className="mono flex items-baseline justify-between text-[10.5px]">
            <span className="t-faint">configured</span>
            <span className="t-dim">{bps(configuredRt)} bps</span>
          </div>
        </div>

        <div>
          <div className="eyebrow mb-1.5">Legend</div>
          <ul className="space-y-[3px]">
            {[
              ["pass", "Pass / feasible / gate open"],
              ["fail", "Fail / gate block / negative EV"],
              ["warn", "Below threshold / assumed / unstable"],
              ["info", "Baseline / neutral / reference"],
              ["violet", "Oracle-feasible boundary marker"],
            ].map(([k, label]) => (
              <li key={k} className="flex items-center gap-2">
                <span className={`h-[5px] w-[5px] shrink-0 rounded-full ${DOT[k]}`} aria-hidden />
                <span className="text-[10px] leading-tight text-[color:var(--color-ink-faint)]">
                  {label}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="shrink-0 border-t border-[color:var(--color-border-subtle)] px-3 pt-3 pb-5">
        <div className="eyebrow mb-1.5">Standing disclaimer</div>
        <p className="text-[10px] leading-snug text-[color:var(--color-ink-faint)]">
          No live orders. No profitability claim. Research telemetry only. A zero-trade window
          is the correct outcome of a closed economic gate and carries no information about
          edge.
        </p>
      </div>
    </nav>
  );
}
