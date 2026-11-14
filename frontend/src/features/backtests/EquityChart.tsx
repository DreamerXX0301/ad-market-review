import { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import { LineChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { EChart } from "../../components/EChart";
import { chartPalette } from "../../components/chartPalette";
import {
  curveAxisAmount,
  curvePercent,
  equityCurvePoints,
  equityCurveTooltip,
  validInitialCapital,
} from "../../components/equityCurve";
import type { EquityCurveMode } from "../../components/equityCurve";
import { Empty } from "../../components/UI";
import type { Row } from "../../lib/api";

echarts.use([
  LineChart,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
]);

function EquityChart({
  rows,
  initialCapital,
}: {
  rows: Row[];
  initialCapital?: unknown;
}) {
  const [mode, setMode] = useState<EquityCurveMode>("return");
  const [themeVersion, refreshTheme] = useState(0);
  const zoomWindow = useRef({ start: 0, end: 100 });
  useEffect(() => {
    const changed = () => refreshTheme((value) => value + 1);
    window.addEventListener("workbench-theme", changed);
    return () => window.removeEventListener("workbench-theme", changed);
  }, []);
  const capitalKnown = validInitialCapital(initialCapital);
  const displayedMode = mode === "return" && !capitalKnown ? "equity" : mode;
  const points = useMemo(
    () => equityCurvePoints(rows, initialCapital),
    [rows, initialCapital],
  );
  const primaryValues = useMemo(
    () =>
      points.map((point) =>
        displayedMode === "return" ? point.cumulativeReturn : point.equity,
      ),
    [points, displayedMode],
  );
  const usable = primaryValues.some((value) => value !== null);
  const base =
    displayedMode === "return" ? 0 : capitalKnown ? initialCapital : undefined;
  const option = useMemo<EChartsOption>(() => {
    const pal = chartPalette();
    const dates = points.map((point) => point.date);
    return {
      animation: false,
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: pal.tooltip,
        borderColor: pal.line,
        textStyle: { color: pal.text, fontSize: 12 },
        formatter: (params: unknown) =>
          equityCurveTooltip(params, points, displayedMode),
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 76, right: 28, top: 22, height: 215 },
        { left: 76, right: 28, top: 275, height: 75 },
      ],
      dataZoom: [
        {
          id: "equity-inside",
          type: "inside",
          xAxisIndex: [0, 1],
          filterMode: "none",
          ...zoomWindow.current,
        },
        {
          id: "equity-slider",
          type: "slider",
          xAxisIndex: [0, 1],
          filterMode: "none",
          bottom: 8,
          height: 17,
          showDetail: false,
          borderColor: pal.line,
          textStyle: { color: pal.text },
          ...zoomWindow.current,
        },
      ],
      xAxis: [
        {
          type: "category",
          data: dates,
          axisLabel: { show: false },
          axisTick: { show: false },
          axisLine: { lineStyle: { color: pal.line } },
        },
        {
          type: "category",
          gridIndex: 1,
          data: dates,
          axisLabel: { color: pal.text, fontSize: 10 },
          axisTick: { show: false },
          axisLine: { lineStyle: { color: pal.line } },
        },
      ],
      yAxis: [
        {
          type: "value",
          scale: true,
          min:
            base === undefined
              ? undefined
              : (value: { min: number }) => Math.min(value.min, base),
          max:
            base === undefined
              ? undefined
              : (value: { max: number }) => Math.max(value.max, base),
          axisLabel: {
            color: pal.text,
            formatter: (value: number) =>
              displayedMode === "return"
                ? curvePercent(value)
                : curveAxisAmount(value),
          },
          splitLine: { lineStyle: { color: pal.line } },
        },
        {
          type: "value",
          gridIndex: 1,
          max: 0,
          axisLabel: {
            color: pal.text,
            formatter: (value: number) => curvePercent(value),
          },
          splitLine: { lineStyle: { color: pal.line } },
        },
      ],
      series: [
        {
          id: "equity-primary",
          type: "line",
          name: displayedMode === "return" ? "累计收益率" : "账户权益",
          data: primaryValues,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: "#6b9df9", width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(91,146,244,.3)" },
              { offset: 1, color: "rgba(91,146,244,.015)" },
            ]),
          },
          markLine:
            base === undefined
              ? undefined
              : {
                  symbol: "none",
                  data: [{ yAxis: base }],
                  label: { show: false },
                  lineStyle: { color: pal.text, type: "dashed" },
                },
        },
        {
          id: "equity-drawdown",
          type: "line",
          name: "回撤",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: points.map((point) => point.drawdown),
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: "#d9a167", width: 1 },
          areaStyle: { color: "rgba(217,161,103,.2)" },
        },
      ],
    };
  }, [points, primaryValues, displayedMode, base, themeVersion]);
  return (
    <div className="equity-curve">
      <div className="equity-curve-toolbar">
        <div className="segmented" role="group" aria-label="回测曲线显示方式">
          <button
            type="button"
            className={displayedMode === "return" ? "active" : ""}
            aria-pressed={displayedMode === "return"}
            disabled={!capitalKnown}
            title={
              !capitalKnown
                ? "本次报告缺少有效初始权益，不能计算收益率"
                : undefined
            }
            onClick={() => setMode("return")}
          >
            累计收益率
          </button>
          <button
            type="button"
            className={displayedMode === "equity" ? "active" : ""}
            aria-pressed={displayedMode === "equity"}
            onClick={() => setMode("equity")}
          >
            账户权益
          </button>
        </div>
        <small>
          {capitalKnown
            ? "以本次回测初始权益为基准，费用已计入账户权益"
            : "本次报告缺少有效初始权益，仅展示账户权益"}
        </small>
      </div>
      {usable ? (
        <EChart
          option={option}
          height={410}
          onReady={(chart) => {
            chart.on("datazoom", () => {
              const current = (chart.getOption().dataZoom as any[])?.[0];
              if (current)
                zoomWindow.current = {
                  start: current.start ?? 0,
                  end: current.end ?? 100,
                };
            });
          }}
        />
      ) : (
        <Empty
          title={rows.length ? "暂无可核验权益曲线" : "暂无权益曲线"}
          description="缺失或无效权益保持未知，不补成零收益。"
        />
      )}
    </div>
  );
}

export default EquityChart;
