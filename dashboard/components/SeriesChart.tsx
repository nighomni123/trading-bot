"use client";

import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import Chart from "./Chart";
import { C, axis, FONT_MONO } from "@/lib/theme";

export type S = {
  name: string;
  /** Scalars for line/bar; [x, y] pairs for scatter. */
  data: (number | null)[] | (number | null)[][];
  color?: string;
  dashed?: boolean;
  type?: "line" | "bar" | "scatter";
  /** ± standard error per point; drawn as a confidence ribbon. */
  se?: (number | null)[];
  /** Horizontal reference rules. */
  markLines?: { y: number; label: string; color?: string }[];
  bandFill?: number;
};

/**
 * Axis/tooltip formatters must be serialisable across the server/client
 * boundary, so formats are named rather than passed as functions.
 */
const FORMATS: Record<string, (v: number) => string> = {
  raw: (v) => String(v),
  int: (v) => v.toFixed(0),
  dec2: (v) => v.toFixed(2),
  dec3: (v) => v.toFixed(3),
  dec4: (v) => v.toFixed(4),
  bps1: (v) => v.toFixed(1),
  bps2: (v) => v.toFixed(2),
  auc: (v) => v.toFixed(3),
};

const resolve = (key?: string) => (key ? FORMATS[key] ?? FORMATS.raw : undefined);

/**
 * Generic multi-series chart with optional standard-error ribbons and
 * reference lines. One component serves every module so the visual language
 * cannot drift between them.
 */
export default function SeriesChart({
  categories,
  series,
  height = 280,
  yName,
  yFormat,
  yType = "value",
  showLegend = true,
  min,
  max,
}: {
  categories: string[];
  series: S[];
  height?: number;
  yName?: string;
  yFormat?: keyof typeof FORMATS | string;
  yType?: "value" | "log";
  showLegend?: boolean;
  min?: number;
  max?: number;
}) {
  const option = useMemo<EChartsOption>(() => {
    const out: any[] = [];

    // Ribbons first so they sit behind the lines. ECharts has no native error
    // bar; a transparent lower bound stacked under a (hi - lo) area is the
    // standard ribbon idiom.
    series.forEach((s, si) => {
      if (!s.se || s.data.some(Array.isArray)) return;
      const scalar = s.data as (number | null)[];
      const lo = scalar.map((v, i) =>
        v == null || s.se![i] == null ? null : v - s.se![i]!
      );
      const span = scalar.map((v, i) =>
        v == null || s.se![i] == null ? null : s.se![i]! * 2
      );
      const id = `band-${si}`;
      out.push({
        name: `${s.name} band`,
        type: "line",
        stack: id,
        data: lo,
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 },
        silent: true,
        tooltip: { show: false },
        legendHoverLink: false,
        z: 1,
      });
      out.push({
        name: `${s.name} ±se`,
        type: "line",
        stack: id,
        data: span,
        symbol: "none",
        lineStyle: { opacity: 0 },
        areaStyle: {
          color: s.color ?? C.info,
          opacity: s.bandFill ?? 0.13,
        },
        silent: true,
        tooltip: { show: false },
        legendHoverLink: false,
        z: 1,
      });
    });

    series.forEach((s) => {
      const entry: any = {
        name: s.name,
        type: s.type ?? "line",
        data: s.data,
        smooth: false,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { width: 1.5, type: s.dashed ? "dashed" : "solid", color: s.color },
        itemStyle: { color: s.color },
        z: 3,
      };
      if (s.markLines?.length) {
        entry.markLine = {
          silent: true,
          symbol: "none",
          data: s.markLines.map((m) => ({
            yAxis: m.y,
            lineStyle: {
              color: m.color ?? C.faint,
              width: 1,
              type: "dashed" as const,
              opacity: 0.85,
            },
            label: {
              show: true,
              position: "insideEndTop" as const,
              color: m.color ?? C.faint,
              fontSize: 9.5,
              fontFamily: FONT_MONO,
              formatter: m.label,
            },
          })),
        };
      }
      out.push(entry);
    });

    return {
      grid: { left: 62, right: 20, top: showLegend ? 38 : 14, bottom: 34 },
      legend: showLegend
        ? {
            // Top-right: the y-axis name is rendered at the top of the y axis,
            // so the legend must not share that corner.
            top: 0,
            right: 0,
            itemGap: 14,
            textStyle: { color: C.dim, fontSize: 10, fontFamily: FONT_MONO },
            itemWidth: 10,
            itemHeight: 2,
            data: series.map((s) => s.name),
          }
        : { show: false },
      tooltip: {
        trigger: "axis" as const,
        axisPointer: { type: "line" as const },
        formatter: (p: any) => {
          const f = resolve(yFormat);
          const arr = (Array.isArray(p) ? p : [p]).filter(Boolean);
          const axisLabel = arr[0]?.axisValue;
          const rows = series.map((s) => {
            const i = arr[0]?.dataIndex ?? 0;
            const v = Array.isArray(s.data[i]) ? (s.data[i] as number[])[1] : s.data[i];
            if (v == null) return "";
            let val = f ? f(v) : String(v);
            if (s.se?.[i] != null) {
              val += ` <span style="color:#6e7681">±${f ? f(s.se[i]!) : s.se[i]}</span>`;
            }
            return `<span style="color:${s.color ?? C.info}">■</span> ${s.name}  <b>${val}</b>`;
          });
          return [`<b>${axisLabel}</b>`, ...rows].join("<br/>");
        },
      },
      xAxis: {
        type: "category" as const,
        data: categories,
        ...axis,
        splitLine: { show: false },
      },
      yAxis: {
        type: yType,
        ...axis,
        min,
        max,
        name: yName,
        // Rotated down the left edge so it cannot collide with the legend.
        nameLocation: "middle" as const,
        nameRotate: 90,
        nameGap: 44,
        nameTextStyle: { color: C.faint, fontSize: 10, fontFamily: FONT_MONO },
        axisLabel: { ...axis.axisLabel, formatter: resolve(yFormat) },
      },
      series: out,
    };
  }, [categories, series, yName, yFormat, yType, showLegend, min, max]);

  return <Chart option={option} height={height} />;
}
