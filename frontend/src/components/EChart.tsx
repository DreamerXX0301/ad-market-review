import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([CanvasRenderer]);

export function EChart({
  option,
  height = 300,
  className = "",
  onReady,
}: {
  option: EChartsOption;
  height?: number;
  className?: string;
  onReady?: (chart: echarts.ECharts) => void;
}) {
  const dom = useRef<HTMLDivElement>(null),
    chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!dom.current) return;
    chart.current = echarts.init(dom.current, undefined, {
      renderer: "canvas",
    });
    onReady?.(chart.current);
    const observer = new ResizeObserver(() => chart.current?.resize());
    observer.observe(dom.current);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);
  return (
    <div ref={dom} className={className} style={{ height, width: "100%" }} />
  );
}
