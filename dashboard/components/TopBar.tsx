import { gitCommit } from "@/lib/artifacts";
import { Badge } from "./UI";

/**
 * Global top bar: system state, data vintage, commit, pipeline health.
 * Read-only telemetry — no controls, by design.
 */
export default function TopBar({
  vintage,
  vintageNote,
  health,
}: {
  vintage: string;
  vintageNote?: string;
  health: { label: string; tone: "pass" | "fail" | "warn" | "info" | "mute" };
}) {
  const c = gitCommit();
  return (
    <header className="sticky top-0 z-30 h-11 shrink-0 border-b border-[color:var(--color-border-subtle)] bg-[color:var(--color-obsidian)]/95 backdrop-blur">
      <div className="flex h-full items-center gap-4 px-4">
        <div className="flex items-center gap-2 shrink-0">
          <span
            className="inline-block h-[3px] w-[3px] rounded-full bg-[color:var(--color-pass)]"
            aria-hidden
          />
          <span className="text-[12px] font-semibold tracking-tight text-ink">
            EPISTEMIC QUANT
          </span>
        </div>

        <div className="h-4 w-px bg-[color:var(--color-border-subtle)]" />

        <Badge tone="warn">RESEARCH_MODE</Badge>

        {/* Data vintage */}
        <div className="flex min-w-0 items-center gap-2">
          <span className="eyebrow shrink-0">Vintage</span>
          <span className="mono truncate text-[11px] text-ink-dim">{vintage}</span>
          {vintageNote && <span className="mono text-[10px] t-faint shrink-0">{vintageNote}</span>}
        </div>

        <div className="flex-1" />

        {/* Commit */}
        <div
          className="hidden md:flex items-center gap-2"
          title={`${c.hash}\n${c.subject}\n${c.date}`}
        >
          <span className="eyebrow">Commit</span>
          <span className="mono text-[11px] text-ink-dim">{c.short}</span>
        </div>

        <div className="h-4 w-px bg-[color:var(--color-border-subtle)]" />

        {/* Pipeline health */}
        <div className="flex items-center gap-2">
          <span className="eyebrow hidden sm:inline">Pipeline</span>
          <Badge tone={health.tone}>{health.label}</Badge>
        </div>
      </div>
    </header>
  );
}
