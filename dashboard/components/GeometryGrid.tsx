"use client";

import { useMemo, useState } from "react";
import { C } from "@/lib/theme";
import { bps, bpsSigned, num, pct, nCompact } from "@/lib/format";
import { KV, Badge } from "./UI";

export type GCell = {
  geometry: string; target_atr: number; stop_atr: number; horizon: number;
  n: number; target_bps: number; stop_bps: number;
  p_target: number; p_stop: number;
  max_achievable_gross_bps: number; max_gross_minus_cost_bps: number; oracle_viable: boolean;
};
export type Realized = { gross_bps: number; net_bps: number; sample_size: number };

const HORIZONS = [15, 30, 60, 120, 240, 480];
const GEOMS = [
  "0.5:0.5", "0.5:1", "0.5:2", "1:0.5", "1:1", "1:2", "2:0.5",
  "2:1", "2:2", "3:0.5", "3:1", "3:2", "5:0.5", "5:1", "5:2",
];

/** Diverging anchors on the app's own semantic hues: red = negative, blue = positive. */
const NEG = [0xf8, 0x51, 0x49];
const MID = [0x8b, 0x94, 0x9e];
const POS = [0x58, 0xa6, 0xff];

/** Render a colour triple as CSS. Interpolating a bare array yields "139,148,158",
 *  which is not a valid colour and invalidates the whole gradient declaration. */
function css(c: number[]): string {
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function lerp(a: number[], b: number[], t: number): string {
  return css(a.map((v, i) => Math.round(v + (b[i] - v) * t)));
}

/**
 * The oracle grid is a 15x6 colour matrix, which is a plain CSS grid. It was an
 * ECharts heatmap + visualMap, but visualMap silently failed to paint the
 * positive half of the scale (measured: 1,873 blue pixels against 133,906 red).
 * Rendering the cells directly makes every cell provably present and the
 * colourbar provably identical to the fills.
 */
export default function GeometryGrid({
  cells,
  costBps,
  realized,
}: {
  cells: GCell[];
  costBps: number;
  realized: Realized[];
}) {
  const [sel, setSel] = useState<GCell | null>(
    () => cells.find((c) => c.geometry === "5:0.5@15m") ?? cells[0] ?? null
  );

  const byKey = useMemo(() => {
    const m = new Map<string, GCell>();
    for (const c of cells) m.set(`${c.geometry.split("@")[0]}|${c.horizon}`, c);
    return m;
  }, [cells]);

  const maxAbs = useMemo(
    () => Math.max(1e-6, ...cells.map((c) => Math.abs(c.max_gross_minus_cost_bps))),
    [cells]
  );

  const color = (v: number) =>
    v >= 0 ? lerp(MID, POS, Math.min(1, v / maxAbs)) : lerp(MID, NEG, Math.min(1, -v / maxAbs));

  const nViable = cells.filter((c) => c.oracle_viable).length;
  const oracleGross = byKey.get("2:1|15")?.max_achievable_gross_bps ?? null;
  const medianRealized = useMemo(() => {
    if (!realized.length) return null;
    const v = realized.map((r) => r.gross_bps).sort((a, b) => a - b);
    return v[Math.floor(v.length / 2)];
  }, [realized]);

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_262px]">
      <div className="min-w-0">
        <div
          className="mono grid gap-[2px] pl-[52px]"
          style={{ gridTemplateColumns: `repeat(${HORIZONS.length}, minmax(0,1fr))` }}
        >
          {HORIZONS.map((h) => (
            <div key={h} className="pb-1 text-center text-[9.5px] t-faint">
              {h}m
            </div>
          ))}
        </div>

        <div className="flex gap-1.5">
          <div className="mono w-[48px] shrink-0 text-right text-[9.5px] t-faint">
            {GEOMS.map((g) => (
              <div key={g} className="flex items-center justify-end" style={{ height: 22 }}>
                {g}
              </div>
            ))}
          </div>
          <div
            className="grid min-w-0 flex-1 gap-[2px]"
            style={{ gridTemplateColumns: `repeat(${HORIZONS.length}, minmax(0,1fr))` }}
          >
            {GEOMS.flatMap((g) =>
              HORIZONS.map((h) => {
                const c = byKey.get(`${g}|${h}`);
                if (!c) return <div key={`${g}-${h}`} style={{ height: 22 }} />;
                const isSel = sel === c;
                return (
                  <button
                    key={`${g}-${h}`}
                    onClick={() => setSel(c)}
                    title={`${c.geometry}\nnet EV (oracle) ${bpsSigned(c.max_gross_minus_cost_bps)} bps\nmax gross ${bpsSigned(c.max_achievable_gross_bps)} bps\np_target ${num(c.p_target, 4)}  p_stop ${num(c.p_stop, 4)}\nn = ${c.n.toLocaleString("en-US")}`}
                    className="relative rounded-[2px]"
                    style={{
                      height: 22,
                      background: color(c.max_gross_minus_cost_bps),
                      boxShadow: c.oracle_viable
                        ? "inset 0 0 0 1.5px #bc8cff"
                        : isSel
                          ? "inset 0 0 0 1.5px #e6edf3"
                          : "inset 0 0 0 1px rgba(13,17,23,0.55)",
                      zIndex: isSel ? 2 : 1,
                    }}
                    aria-label={`${c.geometry}, net EV ${bpsSigned(c.max_gross_minus_cost_bps)} bps`}
                  />
                );
              })
            )}
          </div>
        </div>

        {/* Colourbar built from the same anchors as the cells, so it cannot disagree. */}
        <div className="mt-2.5 flex items-center gap-2 pl-[52px]">
          <span className="mono text-[9.5px] t-faint">−{bps(maxAbs)}</span>
          <div
            className="h-[12px] min-w-0 flex-1 rounded-sm"
            style={{
              background: `linear-gradient(to right, ${lerp(MID, NEG, 1)}, ${lerp(MID, NEG, 0.5)}, ${css(MID)}, ${lerp(MID, POS, 0.5)}, ${lerp(MID, POS, 1)})`,
              boxShadow: "inset 0 0 0 1px rgba(13,17,23,0.6)",
            }}
          />
          <span className="mono text-[9.5px] t-faint">+{bps(maxAbs)} bps net EV</span>
        </div>

        <div className="mono mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[9.5px] text-[color:#7f8b98]">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-[9px] w-[9px] rounded-[2px]"
              style={{ background: "#3a4250", boxShadow: "inset 0 0 0 1.5px #bc8cff" }}
              aria-hidden
            />
            oracle-feasible ({nViable}/{cells.length})
          </span>
          <span>
            x = time horizon · y = target : stop (ATR) · measured cost {bps(costBps)} bps
          </span>
        </div>

        <RealizedStrip
          realized={realized}
          oracleGross={oracleGross}
          medianRealized={medianRealized}
        />
      </div>

      <aside className="card-tight self-start px-3 py-2.5">
        {sel ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <span className="mono min-w-0 flex-1 truncate text-[12px] text-ink">
                {sel.geometry}
              </span>
              <span className="shrink-0">
                <Badge tone={sel.oracle_viable ? "pass" : "fail"}>
                  {sel.oracle_viable ? "ORACLE OK" : "IMPOSSIBLE"}
                </Badge>
              </span>
            </div>
            <p className="mono mt-0.5 text-[9.5px] t-faint">
              {sel.target_atr} ATR target : {sel.stop_atr} ATR stop @ {sel.horizon}m
            </p>

            <div className="rule mt-2 pt-1.5">
              <KV k="p_target" v={num(sel.p_target, 4)} tone="info" />
              <KV k="p_stop" v={num(sel.p_stop, 4)} tone="info" />
              <KV k="p_timeout" v={num(1 - sel.p_target - sel.p_stop, 4)} tone="info" />
              <KV k="target" v={`${bps(sel.target_bps)} bps`} />
              <KV k="stop" v={`${bps(sel.stop_bps)} bps`} />
            </div>

            <div className="rule mt-1.5 pt-1.5">
              <KV
                k="max gross (oracle)"
                v={`${bpsSigned(sel.max_achievable_gross_bps)} bps`}
                tone={sel.max_achievable_gross_bps > 0 ? "pass" : "fail"}
              />
              <KV k="cost" v={`−${bps(costBps)} bps`} tone="fail" />
              <KV
                k="net EV (oracle)"
                v={`${bpsSigned(sel.max_gross_minus_cost_bps)} bps`}
                tone={sel.max_gross_minus_cost_bps > 0 ? "pass" : "fail"}
              />
            </div>

            <div className="rule mt-1.5 pt-1.5">
              <KV k="samples" v={nCompact(sel.n)} />
              <KV k="event share" v={pct(sel.p_target + sel.p_stop, 1)} />
            </div>
          </>
        ) : (
          <p className="text-[11px] t-faint">Click a cell to inspect its geometry.</p>
        )}
      </aside>
    </div>
  );
}

/**
 * The 30 Stage 8 realized candidates all belong to one grid cell, so plotting
 * them on the categorical grid produced an illegible smear. A strip of the
 * actual values against the oracle carries the same claim honestly.
 */
function RealizedStrip({
  realized,
  oracleGross,
  medianRealized,
}: {
  realized: Realized[];
  oracleGross: number | null;
  medianRealized: number | null;
}) {
  const [lo, hi] = useMemo(() => {
    const vals = realized.map((r) => r.gross_bps);
    if (oracleGross != null) vals.push(oracleGross);
    const m = Math.max(1e-6, ...vals.map(Math.abs));
    return [-m * 1.15, m * 1.15];
  }, [realized, oracleGross]);

  const toPct = (v: number) => ((v - lo) / (hi - lo)) * 100;

  return (
    <div className="mt-3">
      <div className="eyebrow mb-1">
        S08 realized gross per candidate — {realized.length} candidates, all at 2:1@15m
      </div>
      <div className="relative h-[76px] border-y border-[color:var(--color-border-subtle)]">
        {oracleGross != null && (
          <div
            className="absolute inset-x-0 border-t border-dashed"
            style={{ bottom: `${toPct(oracleGross)}%`, borderColor: C.pass }}
          >
            <span className="mono absolute right-0 top-[-13px] text-[9px] t-pass">
              oracle {bpsSigned(oracleGross)} bps
            </span>
          </div>
        )}
        <div
          className="absolute inset-x-0 border-t"
          style={{ bottom: `${toPct(0)}%`, borderColor: C.border }}
        />
        {realized.map((r, i) => {
          const above = r.gross_bps >= 0;
          return (
            <div
              key={i}
              className="absolute w-[3px] rounded-[1px]"
              style={{
                left: `${((i + 0.5) / realized.length) * 100}%`,
                bottom: `${Math.min(toPct(r.gross_bps), toPct(0))}%`,
                height: `${Math.max(Math.abs(toPct(r.gross_bps) - toPct(0)), 1)}%`,
                background: above ? C.warn : C.fail,
                opacity: above ? 0.85 : 0.45,
              }}
              title={`candidate ${i + 1}: ${bpsSigned(r.gross_bps)} bps gross`}
            />
          );
        })}
      </div>
      <div className="mono mt-1 text-[9.5px] leading-snug text-[#a3aeb9]">
        Median realized{" "}
        <span className="text-ink">{bpsSigned(medianRealized)} bps</span> against an oracle of{" "}
        <span className="t-pass">{oracleGross != null ? bpsSigned(oracleGross) : "—"}</span> at the
        same geometry — the model reached{" "}
        <span className="t-fail">
          {medianRealized != null && oracleGross != null && medianRealized !== 0
            ? bps(oracleGross / medianRealized, 0)
            : "—"}
        </span>{" "}
        times short of the achievable bar.
      </div>
    </div>
  );
}
