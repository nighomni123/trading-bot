/** Formatting helpers. Every number rendered in the app goes through these. */

export const n0 = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 0 });

/** Compact for large N: 2,629,440 -> 2.63M */
export const nCompact = (v: number | null | undefined) => {
  if (v == null) return "—";
  if (Math.abs(v) < 1000) return String(v);
  if (Math.abs(v) < 1e6) return `${(v / 1e3).toFixed(1)}K`;
  return `${(v / 1e6).toFixed(2)}M`;
};

export const bps = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `${v >= 0 ? "" : "−"}${Math.abs(v).toFixed(d)}`;

/** Signed bps with explicit + for positives — polarity must be visible. */
export const bpsSigned = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`;

export const pct = (v: number | null | undefined, d = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(d)}%`;

export const num = (v: number | null | undefined, d = 3) =>
  v == null ? "—" : v.toFixed(d);

export const sigma = (v: number | null | undefined) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}σ`;

/** EV color: semantic, and deliberately dim when it fails to clear cost. */
export const evColor = (v: number | null | undefined, cost: number) => {
  if (v == null) return C_DIM;
  if (v >= cost) return C_PASS;
  if (v >= 0) return C_WARN;
  return C_FAIL;
};

const C_PASS = "#2ea043";
const C_FAIL = "#f85149";
const C_WARN = "#d29922";
const C_DIM = "#6e7681";

/** Days elapsed between an ISO date and now, integer. */
export const daysSince = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000));
};
