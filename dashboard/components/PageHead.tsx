import type { ReactNode } from "react";

/** Module page header: title, stage tag, first-principle statement. */
export default function PageHead({
  title,
  stage,
  principle,
  right,
}: {
  title: string;
  stage: string;
  principle: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="eyebrow">{stage}</span>
            <h1 className="text-[17px] font-semibold tracking-tight text-ink">{title}</h1>
          </div>
          <p className="principle mt-2 max-w-[900px]">{principle}</p>
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </div>
    </div>
  );
}
