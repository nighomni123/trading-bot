"use client";

import { NODE_TONE, type PipelineNode } from "@/lib/gate";

/**
 * Horizontal node-graph pipeline.
 * Hand-built from divs rather than an SVG graph library: six fixed nodes with
 * labels and tooltips need no layout engine.
 */
export default function PipelineGraph({ nodes }: { nodes: PipelineNode[] }) {
  return (
    <div className="flex w-full items-stretch gap-0 overflow-x-auto pb-1">
      {nodes.map((n, i) => {
        const tone = NODE_TONE[n.state];
        const isFail = n.state === "fail";
        const isLocked = n.state === "locked";
        return (
          <div key={n.id} className="flex min-w-0 flex-1 items-center">
            <div
              className="group relative min-w-0 flex-1"
              title={`${n.label}\n${n.detail}\n${n.metricLabel}: ${n.metric}`}
            >
              <div
                className={`card-tight relative overflow-visible px-2.5 py-2 ${
                  isFail ? "pulse-fail" : ""
                }`}
                style={{
                  borderColor: tone,
                  opacity: isLocked ? 0.45 : 1,
                  boxShadow: isFail ? `0 0 0 1px ${tone}40, 0 0 18px ${tone}22` : "none",
                }}
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className="h-[6px] w-[6px] shrink-0 rounded-full"
                    style={{ background: tone }}
                    aria-hidden
                  />
                  <span
                    className="mono truncate text-[9px]"
                    style={{ color: tone }}
                  >
                    {n.stage}
                  </span>
                </div>
                <div
                  className="mt-1 truncate text-[11px] font-medium leading-tight"
                  style={{ color: isLocked ? "#6e7681" : "#e6edf3" }}
                >
                  {n.label}
                </div>
                <div
                  className="mono mt-1.5 truncate text-[13px] leading-none"
                  style={{ color: tone }}
                >
                  {n.metric}
                </div>
                <div className="mono mt-0.5 truncate text-[8.5px] leading-none text-[#6e7681]">
                  {n.metricLabel}
                </div>

                {isLocked && (
                  <span
                    className="absolute inset-0 flex items-center justify-center text-[16px] text-[#6e7681]"
                    aria-hidden
                  >
                    🔒
                  </span>
                )}
              </div>

              {/* Hover detail */}
              <div className="pointer-events-none absolute left-1/2 top-full z-20 mt-1.5 hidden w-[248px] -translate-x-1/2 rounded border border-[color:var(--color-border)] bg-[#1c2128] p-2.5 text-[10.5px] leading-relaxed text-[color:var(--color-ink-dim)] shadow-[0_8px_24px_rgba(0,0,0,0.6)] group-hover:block">
                {n.detail}
                {n.artifact && (
                  <div className="mono mt-1.5 text-[9px] text-[#6e7681]">ⓘ {n.artifact}</div>
                )}
              </div>
            </div>

            {i < nodes.length - 1 && (
              <div className="flex w-5 shrink-0 items-center justify-center" aria-hidden>
                <span className="mono text-[11px] text-[#30363d]">→</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
