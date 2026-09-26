/**
 * ECharts base theme — institutional dark. Semantic colors only.
 * Registered once on the client; every chart inherits it.
 */
import type { EChartsOption } from "echarts";

export const C = {
  pass: "#2ea043",
  fail: "#f85149",
  warn: "#d29922",
  info: "#58a6ff",
  violet: "#bc8cff",
  ink: "#e6edf3",
  dim: "#8b949e",
  faint: "#6e7681",
  border: "#30363d",
  subtle: "#21262d",
  panel: "#161b22",
  bg: "#0d1117",
} as const;

export const FONT_MONO =
  '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace';

export const echartsTheme: EChartsOption = {
  backgroundColor: "transparent",
  color: [C.info, C.pass, C.warn, C.fail, C.violet, C.dim],
  textStyle: {
    fontFamily: '"Inter", ui-sans-serif, sans-serif',
    color: C.dim,
    fontSize: 11,
  },
  animationDuration: 260,
  animationEasing: "cubicOut",
  grid: { borderColor: C.subtle, containLabel: true },
  legend: {
    textStyle: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO },
    itemWidth: 10,
    itemHeight: 2,
    inactiveColor: C.faint,
  },
  tooltip: {
    backgroundColor: "#1c2128",
    borderColor: C.border,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: {
      color: C.ink,
      fontSize: 11,
      fontFamily: FONT_MONO,
    },
    extraCssText: "box-shadow: 0 8px 24px rgba(0,0,0,0.6); border-radius: 4px;",
  },
  axisPointer: {
    lineStyle: { color: C.border, width: 1 },
    label: { backgroundColor: "#1c2128", color: C.dim, fontFamily: FONT_MONO },
  },
};

/** Shared axis styling — hairline, no dashes, dim labels. */
export const axis = {
  axisLine: { show: true, lineStyle: { color: C.subtle, width: 1 } },
  axisTick: { show: false },
  axisLabel: { color: C.faint, fontSize: 10, fontFamily: FONT_MONO },
  splitLine: { show: true, lineStyle: { color: C.subtle, width: 1, type: "solid" as const } },
};

/**
 * Diverging Red-White-Blue scale for EV heatmaps.
 * Symmetric around 0 so equal magnitude reads equal color regardless of sign.
 */
export const divergingEV = [
  [0, "#1f4e79"],
  [0.25, "#2f6fa8"],
  [0.5, "#7aa6c9"],
  [0.5, "#e8e8e8"],
  [0.5, "#e0958f"],
  [0.75, "#c0392b"],
  [1, "#7d1a12"],
];

/** Cost threshold rule — the single most important reference line in the app. */
export const COST_MARK_LINE = {
  silent: true,
  symbol: "none" as const,
  lineStyle: { color: C.fail, width: 1, type: "dashed" as const, opacity: 0.75 },
  label: {
    show: true,
    position: "insideEndTop" as const,
    color: C.fail,
    fontSize: 10,
    fontFamily: FONT_MONO,
    formatter: "BREAKEVEN {c} bps",
  },
  data: [{ yAxis: 0 }],
};
