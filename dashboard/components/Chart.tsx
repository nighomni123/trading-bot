"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import { echartsTheme } from "@/lib/theme";

type Props = {
  option: EChartsOption;
  height?: number;
  className?: string;
  /** Fires on click; receives the ECharts params object. */
  onEvent?: (params: any) => void;
  /** Replaces the theme entirely (used for custom backgrounds). */
  notMerge?: boolean;
};

/**
 * Minimal ECharts React wrapper.
 * ponytail: hand-rolled rather than echarts-for-react — this is the whole
 * integration surface, and the extra dep buys nothing here.
 */
export default function Chart({ option, height = 280, className, onEvent, notMerge }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    inst.current = chart;

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);

    return () => {
      ro.disconnect();
      chart.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = inst.current;
    if (!chart) return;
    chart.setOption({ ...echartsTheme, ...option }, { notMerge: notMerge ?? true });
    // Resize after layout settles (sidebar/grid changes).
    const t = setTimeout(() => chart.resize(), 60);
    return () => clearTimeout(t);
  }, [option, notMerge]);

  useEffect(() => {
    const chart = inst.current;
    if (!chart || !onEvent) return;
    const fn = (p: any) => handler.current?.(p);
    chart.on("click", fn);
    return () => {
      chart.off("click", fn);
    };
  }, [onEvent]);

  return (
    <div
      ref={ref}
      className={className}
      style={{ width: "100%", height }}
      role="img"
    />
  );
}
