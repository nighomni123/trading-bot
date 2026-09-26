import { gitCommit, artifactMtime } from "@/lib/artifacts";

/**
 * Artifact traceability footer.
 * Every chart carries the exact artifact path, its mtime, and the active
 * commit. Non-negotiable: a number without provenance is a rumour.
 */
export default function ArtifactTrace({
  artifact,
  generated,
  className = "",
}: {
  /** Repo-relative path, e.g. docs/geometry-oracle-grid-2026-09-25.json */
  artifact: string;
  /** generated_at field from the artifact itself, if present. */
  generated?: string | null;
  className?: string;
}) {
  const c = gitCommit();
  const mtime = artifactMtime(artifact);
  const stamp = generated ?? mtime;

  return (
    <div
      className={`mono flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9.5px] text-[color:#7f8b98] pt-1.5 ${className}`}
    >
      <span className="text-ink-dim" title="Source artifact">
        ⓘ {artifact}
      </span>
      {stamp && (
        <span title="Artifact generated_at / mtime">
          {generated ? "gen" : "mtime"} {stamp.replace("T", " ").replace(/Z?$/, "")}
        </span>
      )}
      <span title={`${c.hash}\n${c.subject}`}>commit {c.short}</span>
    </div>
  );
}
