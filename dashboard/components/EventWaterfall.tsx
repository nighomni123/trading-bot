"use client";

import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import Chart from "./Chart";
import { C, axis, FONT_MONO } from "@/lib/theme";

export type Series = {
  /** "EVENT_FLOW_REVERSAL" */
  event: string;
  label: string;
  n: number;
  /** horizon label -> diff in bps */
  points: { horizon: string; bps: number; t: number; n: number }[];
};

/**
 * Event-study waterfall with the cost threshold drawn at ±breakeven.
 * Any bar that does not cross the threshold is dimmed — the visual claim is
 * that statistical significance without economic significance is untradeable.
 */
export default function EventWaterfall({
  series,
  costBps,
  height = 300,
}: {
  series: Series[];
  costBps: number;
  height?: number;
}) {
  const categories = useMemo(
    () => series.flatMap((s) => s.points.map((p) => `${s.label} ${p.horizon}`)),
    [series]
  );

  const option = useMemo<EChartsOption>(() => {
    const withCost = costBps / 2;
    return {
      grid: { left: 54, right: 16, top: 14, bottom: 58 },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (p: any) => {
          // ECharts 6 passes a single params object on axis trigger; older
          // shapes pass an array. Normalise rather than branch at the call site.
          const first = Array.isArray(p) ? p[0] : p;
          const all = series
            .flatMap((e) => e.points.map((pt) => ({ e, pt })))
            .find((x) => `${x.e.label} ${x.pt.horizon}` === first?.axisValue);
          if (!all) return "";
          return [
            `<b>${all.e.label}</b> @ ${all.pt.horizon}`,
            `conditional  ${all.pt.bps >= 0 ? "+" : "−"}${Math.abs(all.pt.bps).toFixed(2)} bps`,
            `t = ${all.pt.t.toFixed(2)}   n = ${all.pt.n.toLocaleString("en-US")}`,
            `vs cost ${withCost.toFixed(2)} bps/side → ${Math.abs(all.pt.bps) > withCost ? "CLEARS" : "does not clear"}`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: categories,
        ...axis,
        splitLine: { show: false },
        axisLabel: {
          color: C.faint,
          fontSize: 10,
          fontFamily: FONT_MONO,
          rotate: 62,
          // hideOverlap alone does not thin rotated category labels; an explicit
          // interval does. 48 bars every 3rd label = 16 readable ticks.
          interval: 3,
        },
      },
      yAxis: {
        type: "value",
        ...axis,
        name: "bps",
        nameTextStyle: { color: C.faint, fontSize: 10, fontFamily: FONT_MONO },
        axisLabel: { ...axis.axisLabel, formatter: (v: number) => v.toFixed(1) },
      },
      series: [
        {
          type: "bar",
          data: series.flatMap((s) =>
            s.points.map((p) => ({
              value: p.bps,
              // Every Stage 11 row is direction=long, so a tradeable effect must be
              // POSITIVE and cover cost. A large negative return is a loss, not a
              // short opportunity — colouring it green would be actively wrong.
              itemStyle: {
                color: p.bps > withCost ? C.pass : p.bps > 0 ? C.warn : C.fail,
                opacity: p.bps > withCost ? 1 : p.bps > 0 ? 0.75 : 0.45,
              },
            }))
          ),
          barMaxWidth: 13,
          markLine: {
            silent: true,
            symbol: "none",
            data: [
              {
                yAxis: withCost,
                lineStyle: { color: C.fail, width: 1, type: "dashed", opacity: 0.85 },
                label: {
                  show: true,
                  position: "insideEndTop",
                  color: C.fail,
                  fontSize: 9.5,
                  fontFamily: FONT_MONO,
                  formatter: `breakeven +${withCost.toFixed(2)}`,
                },
              },
              {
                yAxis: -withCost,
                lineStyle: { color: C.fail, width: 1, type: "dashed", opacity: 0.85 },
                label: {
                  show: true,
                  position: "insideEndBottom",
                  color: C.fail,
                  fontSize: 9.5,
                  fontFamily: FONT_MONO,
                  formatter: `breakeven −${withCost.toFixed(2)}`,
                },
              },
            ],
          },
        },
      ],
    };
  }, [series, costBps, categories]);

  return <Chart option={option} height={height} />;
}
