import { useEffect, useRef, useState, useMemo } from "react";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import {
  BarChart,
  CandlestickChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
} from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { LabelLayout } from "echarts/features";
import { CanvasRenderer } from "echarts/renderers";
import {
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minus,
  Plus,
  RotateCcw,
  Settings2,
  X,
} from "lucide-react";
import { useApi } from "../lib/api";
import type { Row } from "../lib/api";
import {
  amount,
  compactQuantity,
  num,
  percent,
  tone,
  textValue,
} from "../lib/format";
import { candleChange } from "./candleChange";
import { bollCandleValues, chartBoll } from "./chartBoll";
import { chartPaneLayout } from "./chartPaneLayout";
import { chartSinceReturn } from "./chartSinceReturn";
import { indexPriceProfile } from "./indexPriceProfile";
import IndexPriceProfileSummary from "./IndexPriceProfileSummary";
import { buildIndexProfileSeries } from "./indexPriceProfilePresentation";
import {
  curveAxisAmount,
  curvePercent,
  equityCurvePoints,
  equityCurveTooltip,
  validInitialCapital,
} from "./equityCurve";
import type { EquityCurveMode } from "./equityCurve";
import type { ChartPeriod } from "./candleChange";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Panel,
  STOCK_DRAWER_VISIBILITY_EVENT,
  Warning,
} from "./UI";
import DataNotes from "./DataNotes";
import { chartDataNotes } from "./dataNotePresentation";
import {
  confirmedBarLimits,
  LimitStatus,
  limitCoverageText,
  limitTooltipHtml,
} from "./CandleLimitEvents";
import "./Charts.css";
import "./StockNavigation.css";
import StockLimitSummary from "./StockLimitSummary";
import StockHeaderQuote from "./StockHeaderQuote";
import StockCurrentTopics from "./StockCurrentTopics";
import WatchlistButton from "../features/watchlist/WatchlistButton";
import StockEventBaseline, {
  useStockEventBaseline,
} from "./StockEventBaseline";
import CopyStockCode from "./CopyStockCode";
import { ChartSelect, ChartToggle } from "./ChartControls";
import {
  ChartAnnotationButton,
  ChartAnnotationPanel,
  useChartAnnotations,
} from "./ChartDrawingTools";
import { annotationColors, lineLabel } from "./chartAnnotations";
import {
  stockNavigationPosition,
  stockPayloadMatches,
} from "./stockNavigation";
import type { StockNavigation } from "./stockNavigation";
import {
  effectiveChartBoll,
  effectiveChartMas,
  effectiveChartPanels,
  effectiveChartPriceBasis,
  effectiveChartWaves,
  CHART_INDICATOR_ORDER,
  MA_WINDOWS,
} from "./chartPreferences";
import type { ChartPanel } from "./chartPreferences";
import { useChartPreferences } from "./useChartPreferences";
import { defaultChartWindow, projectEventBaseline } from "./chartDisplay";
import { candleChartEndpoint } from "./chartEndpoint";
import {
  chartIndicatorAvailability,
  chartIndicatorLabel,
} from "./chartIndicatorPresentation";
import type { ChartEventBaseline } from "./chartDisplay";
import {
  waveCandidates,
  wavePatterns,
  waveTurningPoints,
  selectedWavePatterns,
  wavePlotSeries,
  wavePatternWindow,
} from "./wavePresentation";
import type { WaveMode } from "./wavePresentation";
import {
  historicalChartEvents,
  projectHistoricalChartEvent,
  eventPriceNames,
} from "./historicalChartEvents";
export type { ChartEventBaseline } from "./chartDisplay";
echarts.use([
  BarChart,
  CandlestickChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  VisualMapComponent,
  LabelLayout,
  CanvasRenderer,
]);
const maColors = [
  "#e8c16b",
  "#b292f0",
  "#4cbbe3",
  "#f084bc",
  "#47cbb0",
  "#d48548",
];
function chartPalette() {
  const light = document.documentElement.dataset.theme === "light";
  return {
    text: light ? "#768298" : "#7d8ba0",
    line: light ? "#e6eaf0" : "#202b3a",
    bg: light ? "#ffffff" : "#111924",
    tooltip: light ? "#ffffff" : "#192332",
    up: light ? "#d85151" : "#ed6865",
    down: light ? "#119573" : "#39bd9b",
    bollUpper: light ? "#ce4147" : "#f07178",
    bollMiddle: light ? "#00868f" : "#49c7db",
    bollLower: light ? "#228447" : "#57cc83",
    waveCorrection: light ? "#7754b4" : "#b292f0",
    waveTurning: light ? "#758496" : "#a0aec1",
  };
}
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
  const latestOption = useRef(option);
  latestOption.current = option;
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
export function CandleChart({
  instrumentId,
  asOf,
  height = 650,
  compact = false,
  onLoad,
  instrumentKind,
  eventBaseline,
  historicalChecks,
  historicalChecksReason,
  dataEndpoint,
  showStockSummary = true,
  showDataNotes = true,
  frozenChartsByPeriod,
}: {
  instrumentId: string;
  asOf?: string;
  height?: number;
  compact?: boolean;
  onLoad?: (data: Row) => void;
  instrumentKind?: string;
  eventBaseline?: ChartEventBaseline;
  historicalChecks?: unknown;
  historicalChecksReason?: string;
  dataEndpoint?: string;
  showStockSummary?: boolean;
  showDataNotes?: boolean;
  frozenChartsByPeriod?: Partial<Record<ChartPeriod, Row>>;
}) {
  const { preferences, persistent, update, reset } = useChartPreferences();
  const knownIndex = [
    "000001.SH",
    "399001.SZ",
    "399006.SZ",
    "000688.SH",
  ].includes(instrumentId.toUpperCase());
  const [period, setPeriod] = useState<ChartPeriod>("D"),
    [indicatorContinuity, setIndicatorContinuity] = useState<
      "quotes" | "market_sessions"
    >("quotes"),
    [preferencesOpen, setPreferencesOpen] = useState(false),
    [resolvedKind, setResolvedKind] = useState<{
      id: string;
      kind: string;
    } | null>(null),
    [activeDate, setActiveDate] = useState<string | null>(null),
    [wavePatternKey, setWavePatternKey] = useState("latest"),
    [themeTick, setThemeTick] = useState(0);
  const [showHistoricalEvents, setShowHistoricalEvents] = useState(true);
  const isIndex =
    knownIndex ||
    instrumentKind === "index" ||
    (resolvedKind?.id === instrumentId && resolvedKind.kind === "index");
  const priceBasis = effectiveChartPriceBasis(preferences, isIndex);
  const cycleLabel = period === "D" ? "日" : period === "W" ? "周" : "月";
  const mas = effectiveChartMas(preferences, isIndex);
  const panels = effectiveChartPanels(preferences, isIndex);
  const showBoll = effectiveChartBoll(preferences, isIndex);
  const setShowBoll = (value: boolean) =>
    update(isIndex ? { indexShowBoll: value } : { stockShowBoll: value });
  const { showWaves, waveMode } = effectiveChartWaves(preferences, isIndex);
  const setShowWaves = (value: boolean) =>
    update(isIndex ? { indexShowWaves: value } : { stockShowWaves: value });
  const setWaveMode = (value: WaveMode) => {
    update(isIndex ? { indexWaveMode: value } : { stockWaveMode: value });
    setWavePatternKey("latest");
  };
  const { showLimits, showEventBaseline } = preferences;
  const setPriceBasis = (value: string) => {
    if (!isIndex)
      update({ stockPriceBasis: value === "forward" ? "forward" : "none" });
  };
  const instance = useRef<echarts.ECharts | null>(null);
  const zoomWindow = useRef({ start: 0, end: 100 });
  const zoomContext = useRef("");
  function zoomTo(start: number, end: number) {
    zoomWindow.current = { start, end };
    instance.current?.dispatchAction({ type: "dataZoom", start, end });
  }
  function zoomBy(delta: number) {
    const current = zoomWindow.current;
    const currentWidth = current.end - current.start;
    const profileZoom = profileShownRef.current;
    const step = profileZoom
      ? Math.sign(delta) * Math.min(Math.abs(delta), currentWidth * 0.25)
      : delta;
    const minimum = profileZoom
      ? Math.min(5, 800 / Math.max(1, latestBars.current.length - 1))
      : 5;
    const width = Math.min(100, Math.max(minimum, currentWidth + step));
    const center = (current.start + current.end) / 2;
    const start = Math.min(100 - width, Math.max(0, center - width / 2));
    zoomTo(start, start + width);
  }
  const frozenMode = frozenChartsByPeriod !== undefined;
  const result = useApi<Row>(
    frozenMode
      ? null
      : candleChartEndpoint({
          instrumentId,
          period,
          priceBasis,
          indicatorContinuity,
          asOf,
          dataEndpoint,
        }),
  );
  const data = frozenMode ? frozenChartsByPeriod[period] || null : result.data;
  const bars: Row[] = data?.bars || [];
  const profileEnabled =
    isIndex &&
    !frozenMode &&
    !dataEndpoint &&
    preferences.indexShowPriceProfile;
  const profile = useMemo(
    () =>
      profileEnabled && period === "D" && data
        ? indexPriceProfile(bars, {
            asOf: data.as_of,
            lookback: preferences.indexPriceProfileLookback,
            bins: 32,
          })
        : null,
    [profileEnabled, period, data, preferences.indexPriceProfileLookback],
  );
  const profileShown = !!profile?.rows.length;
  const profileShownRef = useRef(profileShown);
  profileShownRef.current = profileShown;
  const profileAxisData = useRef({ profile, bars, mas });
  profileAxisData.current = { profile, bars, mas };
  function profilePriceBounds() {
    const { profile, bars, mas } = profileAxisData.current;
    if (!profile?.rows.length) return null;
    const first = Math.max(
      0,
      Math.floor((zoomWindow.current.start / 100) * (bars.length - 1)),
    );
    const end = Math.min(
      bars.length,
      Math.ceil((zoomWindow.current.end / 100) * (bars.length - 1)) + 1,
    );
    const visibleValues = bars
      .slice(first, end)
      .flatMap((bar) => [
        bar.low,
        bar.high,
        ...mas.map(
          (window) => bar["ma" + window] ?? bar.indicators?.["ma" + window],
        ),
      ])
      .filter(
        (value): value is number =>
          typeof value === "number" && Number.isFinite(value) && value > 0,
      );
    const low = Math.min(profile.rows[0].low, ...visibleValues);
    const high = Math.max(profile.rows.at(-1)!.high, ...visibleValues);
    const padding = Math.max((high - low) * 0.06, high * 0.001);
    return { min: low - padding, max: high + padding };
  }
  function locateProfile() {
    const first = bars.findIndex((bar) => bar.date === profile?.startDate);
    if (first >= 0) zoomTo((first / Math.max(1, bars.length - 1)) * 100, 100);
  }
  const annotations = useChartAnnotations(
    instrumentId,
    data,
    !frozenMode &&
      !isIndex &&
      !dataEndpoint &&
      (instrumentKind === "stock" || data?.instrument?.kind === "stock"),
  );
  const annotationPick = useRef<((price: number) => void) | null>(null);
  annotationPick.current = annotations.drawing ? annotations.picked : null;
  const nextZoomContext = JSON.stringify([
    instrumentId,
    asOf || null,
    period,
    dataEndpoint || null,
    data?.as_of || null,
  ]);
  if (bars.length && zoomContext.current !== nextZoomContext) {
    zoomWindow.current = defaultChartWindow(period, bars.length);
    zoomContext.current = nextZoomContext;
  }
  const baselineView = projectEventBaseline(
    isIndex ? undefined : eventBaseline,
    data,
  );
  const baselinePrice = showEventBaseline ? baselineView.price : null;
  const baselineLabel = eventBaseline?.label || "入池事件价";
  const historyEvents = useMemo(
    () => historicalChartEvents(isIndex ? undefined : historicalChecks),
    [historicalChecks, isIndex],
  );
  const historyViews = useMemo(
    () =>
      historyEvents.map((event) => projectHistoricalChartEvent(event, data)),
    [historyEvents, data],
  );
  const latestBars = useRef(bars);
  latestBars.current = bars;
  useEffect(() => setActiveDate(null), [instrumentId, asOf, period]);
  useEffect(() => {
    if (profileShown) locateProfile();
  }, [profileShown, profile?.startDate, profile?.endDate, instrumentId]);
  useEffect(() => setWavePatternKey("latest"), [instrumentId, asOf, period]);
  useEffect(() => {
    setWavePatternKey("latest");
  }, [instrumentId, isIndex, waveMode]);
  useEffect(() => {
    if (data) {
      if (data.instrument?.kind)
        setResolvedKind({ id: instrumentId, kind: data.instrument.kind });
      onLoad?.(data);
    }
  }, [data]);
  useEffect(() => {
    const f = () => setThemeTick((x) => x + 1);
    window.addEventListener("workbench-theme", f);
    return () => window.removeEventListener("workbench-theme", f);
  }, []);
  const toggleMA = (n: number) =>
    update({
      [isIndex ? "indexMas" : "stockMas"]: mas.includes(n)
        ? mas.filter((x) => x !== n)
        : [...mas, n],
    });
  const togglePanel = (key: ChartPanel) =>
    update({
      [isIndex ? "indexPanels" : "stockPanels"]: panels.includes(key)
        ? panels.filter((value) => value !== key)
        : [...panels, key],
    });
  const boll = useMemo(() => chartBoll(data), [data]);
  const last = bars.at(-1);
  const activeBar = bars.find((b) => b.date === activeDate) || last;
  const activeBoll = boll.find((point) => point.date === activeBar?.date);
  const activeChange = candleChange(activeBar, period);
  const activeSinceReturn = chartSinceReturn(activeBar, data);
  const activeMAState = chartIndicatorAvailability(
    activeBar,
    mas.map((window) => "ma" + window),
    period,
  );
  const valueAt = (field: string) =>
    activeBar?.[field] ?? activeBar?.indicators?.[field] ?? null;
  const broad = bars.some((x) => x.advancers != null || x.up_count != null);
  const chosen = CHART_INDICATOR_ORDER.filter((key) =>
    key === "boll"
      ? showBoll
      : panels.includes(key) && (key !== "breadth" || broad),
  );
  const palette = chartPalette();
  const { actualHeight, mainHeight, paneHeights, paneTops } = chartPaneLayout(
    height,
    chosen.length,
    compact,
    chosen.map((key) => (key === "boll" ? 140 : 55)),
  );
  const waves = data?.waves;
  const waveAsOf = data?.as_of || asOf || last?.date || "";
  const patterns = useMemo(
    () =>
      wavePatterns(
        waves,
        bars.map((bar) => bar.date),
        waveAsOf,
      ),
    [waves, bars, waveAsOf],
  );
  const turns = useMemo(
    () =>
      waveTurningPoints(
        waves,
        bars.map((bar) => bar.date),
        waveAsOf,
      ),
    [waves, bars, waveAsOf],
  );
  const candidates = useMemo(
    () => waveCandidates(patterns, waveMode),
    [patterns, waveMode],
  );
  const effectivePatternKey =
    candidates.some((pattern) => pattern.pattern_id === wavePatternKey) ||
    (wavePatternKey === "all" && waveMode !== "latest")
      ? wavePatternKey
      : "latest";
  const selectedPatterns = useMemo(
    () => selectedWavePatterns(candidates, effectivePatternKey, waveMode),
    [candidates, effectivePatternKey, waveMode],
  );
  const waveWindow = useMemo(
    () =>
      selectedPatterns.length === 1
        ? wavePatternWindow(
            selectedPatterns[0],
            bars.map((bar) => bar.date),
          )
        : null,
    [selectedPatterns, bars],
  );
  const supportsLimits =
    !frozenMode &&
    data?.instrument?.kind === "stock" &&
    data?.limit_event_coverage?.applicable !== false;
  const forwardOption = data?.price_basis_options?.find(
    (item: Row) => item.value === "forward",
  );
  const option = useMemo(() => {
    const pal = chartPalette(),
      dates = bars.map((b) => b.date);
    const chartRight = profileShown ? "25%" : 20;
    const grids: any[] = [
        { left: 62, right: chartRight, top: 24, height: mainHeight },
      ],
      xaxes: any[] = [],
      yaxes: any[] = [],
      series: any[] = [];
    chosen.forEach((k, i) =>
      grids.push({
        left: 62,
        right: chartRight,
        top: paneTops[i],
        height: paneHeights[i],
      }),
    );
    grids.forEach((_, i) => {
      xaxes.push({
        type: "category",
        data: dates,
        gridIndex: i,
        boundaryGap: true,
        axisLine: { lineStyle: { color: pal.line } },
        axisLabel: {
          color: pal.text,
          fontSize: 10,
          show: i === grids.length - 1,
          formatter: (v: string) => v.slice(5),
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: { label: { backgroundColor: "#435d7d" } },
      });
      yaxes.push({
        id: i === 0 ? "candle-main-price" : `candle-pane-${i}`,
        type: "value",
        gridIndex: i,
        scale: true,
        splitNumber: i === 0 ? 4 : 2,
        axisLabel: {
          color: pal.text,
          fontSize: 10,
          formatter: (v: number) =>
            Math.abs(v) >= 1e8
              ? (v / 1e8).toFixed(1) + "亿"
              : Math.abs(v) >= 1e4
                ? (v / 1e4).toFixed(0) + "万"
                : num(v, Math.abs(v) > 50 ? 0 : 2),
        },
        splitLine: { lineStyle: { color: pal.line, type: "dashed" } },
        axisLine: { show: false },
      });
    });
    // The distribution uses a count axis; only the original date axes zoom.
    const dateAxisIndices = grids.map((_, i) => i);
    if (profileShown && profile) {
      const bounds = profilePriceBounds()!;
      Object.assign(yaxes[0], bounds);
      const ix = grids.length;
      grids.push({ left: "79%", right: 12, top: 24, height: mainHeight });
      xaxes.push({
        type: "value",
        gridIndex: ix,
        min: 0,
        max: Math.max(1, ...profile.rows.map((row) => row.count)) * 1.12,
        splitNumber: 2,
        minInterval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: pal.text, fontSize: 9 },
        splitLine: { show: false },
        axisPointer: { show: false },
      });
      yaxes.push({
        id: "index-profile-price",
        type: "value",
        gridIndex: ix,
        ...bounds,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: { show: false },
      });
      series.push(
        buildIndexProfileSeries(profile, {
          axisIndex: ix,
          palette: {
            center: "#e8c16b",
            valueArea: "#70a6f8",
            outside: "#3b5476",
            boundary: pal.line,
            text: pal.text,
          },
        }),
      );
      series.push({
        name: "分布参照",
        type: "line",
        data: [],
        xAxisIndex: 0,
        yAxisIndex: 0,
        silent: true,
        tooltip: { show: false },
        markArea: {
          silent: true,
          itemStyle: { color: "rgba(112,166,248,.045)" },
          data: [[{ yAxis: profile.val }, { yAxis: profile.vah }]],
        },
        markLine: {
          silent: true,
          symbol: "none",
          animation: false,
          lineStyle: { type: "dashed", width: 1 },
          label: {
            show: true,
            position: "insideEndTop",
            fontSize: 10,
            backgroundColor: pal.bg,
            padding: [2, 4],
          },
          data: [
            {
              yAxis: profile.center,
              lineStyle: { color: "#e8c16b" },
              label: {
                color: "#e8c16b",
                formatter: `中心 ${num(profile.center)}`,
              },
            },
            {
              yAxis: profile.vah,
              lineStyle: { color: "#79b4e9" },
              label: {
                color: "#79b4e9",
                formatter: `上沿 ${num(profile.vah)}`,
              },
            },
            {
              yAxis: profile.val,
              lineStyle: { color: "#a795d9" },
              label: {
                color: "#a795d9",
                formatter: `下沿 ${num(profile.val)}`,
              },
            },
          ].filter(
            (guide, index, guides) =>
              guides.findIndex(
                (candidate) => candidate.yAxis === guide.yAxis,
              ) === index,
          ),
        },
      });
    }
    series.push({
      name: "K线",
      type: "candlestick",
      data: bars.map((b) => [b.open, b.close, b.low, b.high]),
      xAxisIndex: 0,
      yAxisIndex: 0,
      itemStyle: {
        color: "#ed6865",
        color0: "#39bd9b",
        borderColor: "#ed6865",
        borderColor0: "#39bd9b",
      },
      barMaxWidth: 12,
      ...(baselinePrice != null
        ? {
            markLine: {
              symbol: "none",
              silent: true,
              lineStyle: { color: "#c79136", width: 1.4, type: "dashed" },
              label: {
                show: true,
                formatter: `${baselineLabel} ${eventBaseline?.date} · ${num(baselinePrice)}`,
                position: "insideEndTop",
                color: "#c79136",
                backgroundColor: pal.bg,
                padding: [3, 5],
                fontSize: 10,
              },
              data: [{ yAxis: baselinePrice, name: baselineLabel }],
            },
          }
        : {}),
    });
    if (showHistoricalEvents)
      historyViews.forEach((view, index) => {
        if (view.price == null || view.closePrice == null || !view.slot) return;
        const color = ["#bd96f3", "#4cbbe3", "#e8c16b"][index % 3];
        const event = view.event;
        series.push({
          name: `${event.label} ${event.date}`,
          type: "line",
          data: [],
          xAxisIndex: 0,
          yAxisIndex: 0,
          silent: true,
          tooltip: { show: false },
          markLine: {
            silent: true,
            symbol: "none",
            animation: false,
            lineStyle: { color, width: 1.3, type: "dashed" },
            label: {
              show: true,
              color,
              backgroundColor: pal.bg,
              padding: [2, 4],
              fontSize: 10,
            },
            data: [
              {
                yAxis: view.price,
                label: {
                  formatter: `${event.label} ${event.date} ${eventPriceNames[event.priceField]} · ${num(view.price)}`,
                  position: "insideEndTop",
                },
              },
              {
                xAxis: view.slot,
                lineStyle: { opacity: 0.45, type: "dotted" },
                label: {
                  formatter: `事件 ${event.date}`,
                  position: "insideStartTop",
                },
              },
            ],
          },
          markPoint: {
            silent: true,
            symbol: "pin",
            symbolSize: 24,
            itemStyle: { color },
            label: { show: true, formatter: "事", color: pal.bg, fontSize: 9 },
            data: [{ coord: [view.slot, view.closePrice], name: event.label }],
          },
        });
      });
    if (annotations.visible.length)
      series.push({
        name: "我的水平线",
        type: "line",
        data: [],
        xAxisIndex: 0,
        yAxisIndex: 0,
        silent: true,
        tooltip: { show: false },
        markLine: {
          silent: true,
          symbol: "none",
          animation: false,
          label: {
            show: true,
            position: "insideEndTop",
            fontSize: 10,
            backgroundColor: pal.bg,
            padding: [2, 4],
          },
          data: annotations.visible.map(({ line, price }) => ({
            name: line.label || "水平线",
            yAxis: price!,
            lineStyle: {
              color: annotationColors[line.color],
              width: 1.4,
              type: "dashed",
            },
            label: {
              formatter: lineLabel(line, price!),
              color: annotationColors[line.color],
            },
          })),
        },
      });
    if (supportsLimits && showLimits && period === "D") {
      for (const kind of ["limit_up", "limit_down"] as const) {
        const up = kind === "limit_up";
        series.push({
          name: up ? "收盘涨停" : "收盘跌停",
          type: "scatter",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: bars
            .filter((bar) =>
              confirmedBarLimits(bar).some((event) => event.kind === kind),
            )
            .map((bar) => ({
              value: [bar.date, up ? bar.high : bar.low],
              name: up ? "收盘涨停" : "收盘跌停",
            })),
          symbol: "triangle",
          symbolRotate: up ? 180 : 0,
          symbolSize: 5,
          symbolOffset: [0, up ? -7 : 7],
          itemStyle: { color: up ? pal.up : pal.down, opacity: 1 },
          label: {
            show: true,
            formatter: up ? "涨" : "跌",
            position: up ? "top" : "bottom",
            distance: 1,
            fontSize: 9,
            color: up ? pal.up : pal.down,
          },
          labelLayout: { hideOverlap: true },
          tooltip: { show: false },
          emphasis: { scale: 1.5 },
          clip: false,
          z: 8,
        });
      }
    }
    mas.forEach((n) => {
      series.push({
        name: "MA" + n,
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: bars.map((b) => b["ma" + n] ?? b.indicators?.["ma" + n] ?? null),
        symbol: "none",
        connectNulls: false,
        lineStyle: {
          width: 1.15,
          color: maColors[[5, 10, 20, 30, 60, 250].indexOf(n)],
        },
        itemStyle: { color: maColors[[5, 10, 20, 30, 60, 250].indexOf(n)] },
      });
    });
    if (showWaves)
      series.push(
        ...wavePlotSeries(selectedPatterns, turns, waveMode, {
          up: pal.up,
          down: pal.down,
          correction: pal.waveCorrection,
          turning: pal.waveTurning,
        }),
      );
    chosen.forEach((key, i) => {
      const ix = i + 1;
      const line = (name: string, field: string, color: string) =>
        series.push({
          name,
          type: "line",
          xAxisIndex: ix,
          yAxisIndex: ix,
          data: bars.map((b) => b[field] ?? b.indicators?.[field] ?? null),
          symbol: "none",
          lineStyle: { width: 1.25, color },
          itemStyle: { color },
        });
      if (key === "boll") {
        series.push({
          name: "BOLL K线",
          type: "candlestick",
          xAxisIndex: ix,
          yAxisIndex: ix,
          data: bars.map(bollCandleValues),
          itemStyle: {
            color: "transparent",
            color0: pal.down,
            borderColor: pal.up,
            borderColor0: pal.down,
            borderWidth: 1,
          },
          barMaxWidth: 9,
          z: 2,
        });
        (
          [
            ["upper", "BOLL 上轨", pal.bollUpper],
            ["middle", "BOLL 中轨", pal.bollMiddle],
            ["lower", "BOLL 下轨", pal.bollLower],
          ] as const
        ).forEach(([field, name, color]) => {
          series.push({
            name,
            type: "line",
            xAxisIndex: ix,
            yAxisIndex: ix,
            data: boll.map((point) => point[field]),
            symbol: "none",
            connectNulls: false,
            lineStyle: { color, width: field === "middle" ? 1 : 1.25 },
            itemStyle: { color },
            z: 3,
          });
        });
      }
      if (key === "volume")
        series.push({
          name: "成交量",
          type: "bar",
          xAxisIndex: ix,
          yAxisIndex: ix,
          data: bars.map((b) => ({
            value: b.volume,
            itemStyle: { color: b.close >= b.open ? "#ed6865" : "#39bd9b" },
          })),
          barMaxWidth: 10,
        });
      if (key === "macd") {
        series.push({
          name: "MACD",
          type: "bar",
          xAxisIndex: ix,
          yAxisIndex: ix,
          data: bars.map((b) => ({
            value: b.macd_hist,
            itemStyle: { color: b.macd_hist >= 0 ? "#ed6865" : "#39bd9b" },
          })),
          barMaxWidth: 9,
        });
        line("DIF", "macd_dif", "#e8c16b");
        line("DEA", "macd_dea", "#b292f0");
      }
      if (key === "kdj") {
        line("K", "kdj_k", "#e8c16b");
        line("D", "kdj_d", "#4cbbe3");
      }
      if (key === "breadth") {
        line("上涨家数", "advancers", "#ed6865");
        series[series.length - 1].markLine = {
          symbol: "none",
          silent: true,
          label: {
            show: true,
            color: pal.text,
            fontSize: 9,
            position: "insideEndTop",
          },
          lineStyle: { type: "dashed", color: "#536078" },
          data: [
            { yAxis: 500, name: "普跌参考" },
            { yAxis: 3000, name: "普涨参考" },
          ],
        };
      }
    });
    return {
      animation: false,
      backgroundColor: "transparent",
      grid: grids,
      axisPointer: {
        link: [{ xAxisIndex: dateAxisIndices }],
        lineStyle: { color: "#61758e", type: "dashed" },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: pal.tooltip,
        borderColor: pal.line,
        textStyle: { color: pal.text, fontSize: 11 },
        confine: true,
        formatter: (params: any) => {
          const entries = Array.isArray(params) ? params : [params],
            candle = entries.find((entry: any) => entry.seriesName === "K线"),
            b =
              bars.find((bar) => bar.date === entries[0]?.axisValue) ||
              bars[candle?.dataIndex ?? entries[0]?.dataIndex];
          if (!b) return "";
          const change = candleChange(b, period);
          const since = chartSinceReturn(b, data);
          // Axis filtering may reindex individual series after zooming. Use
          // the selected date's source values for every price overlay.
          const point = boll.find((point) => point.date === b.date);
          const priceIndicators = [
            ...mas.map((window) => ({
              name: "MA" + window,
              value: b["ma" + window] ?? b.indicators?.["ma" + window] ?? null,
              color:
                maColors[MA_WINDOWS.findIndex((value) => value === window)],
            })),
            ...(showBoll
              ? [
                  {
                    name: "BOLL副图 上轨",
                    value: point?.upper,
                    color: pal.bollUpper,
                  },
                  {
                    name: "BOLL副图 中轨",
                    value: point?.middle,
                    color: pal.bollMiddle,
                  },
                  {
                    name: "BOLL副图 下轨",
                    value: point?.lower,
                    color: pal.bollLower,
                  },
                ]
              : []),
          ];
          const color =
            change.value == null
              ? pal.text
              : change.value > 0
                ? pal.up
                : change.value < 0
                  ? pal.down
                  : pal.text;
          const escape = (value: string) =>
            value.replace(
              /[&<>"']/g,
              (char) =>
                ({
                  "&": "&amp;",
                  "<": "&lt;",
                  ">": "&gt;",
                  '"': "&quot;",
                  "'": "&#39;",
                })[char] || char,
            );
          return (
            `<div style="font-weight:700;margin-bottom:6px;color:${pal.text}">${b.date}</div><div>开 ${num(b.open)}　高 ${num(b.high)}</div><div>收 ${num(b.close)}　低 ${num(b.low)}</div>` +
            `<div style="color:${color}" title="${escape(change.reason)}">${change.label}　${percent(change.value)}</div>` +
            (change.value == null
              ? `<div style="max-width:260px;white-space:normal;font-size:10px">${escape(change.reason)}</div>`
              : "") +
            `<div title="${escape(since.reason)}">至${since.end || "观察日"}涨跌幅　<span style="color:${since.value == null || since.value === 0 ? pal.text : since.value > 0 ? pal.up : pal.down}">${percent(since.value)}</span></div>` +
            priceIndicators
              .filter(
                (indicator) =>
                  typeof indicator.value === "number" &&
                  Number.isFinite(indicator.value),
              )
              .map(
                (indicator) =>
                  `<div><span style="display:inline-block;margin-right:4px;border-radius:50%;width:8px;height:8px;background-color:${indicator.color}"></span>${indicator.name}　${num(indicator.value)}</div>`,
              )
              .join("") +
            (supportsLimits ? limitTooltipHtml(b, period, pal) : "")
          );
        },
      },
      xAxis: xaxes,
      yAxis: yaxes,
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: dateAxisIndices,
          start: zoomWindow.current.start,
          end: zoomWindow.current.end,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          preventDefaultMouseMove: true,
          filterMode: "filter",
        },
        {
          type: "slider",
          xAxisIndex: dateAxisIndices,
          bottom: 3,
          height: 17,
          borderColor: "transparent",
          backgroundColor: pal.line,
          fillerColor: "rgba(79,142,247,.15)",
          handleStyle: { color: "#5b90dc" },
          textStyle: { color: pal.text },
          showDetail: false,
          start: zoomWindow.current.start,
          end: zoomWindow.current.end,
          filterMode: "filter",
        },
      ],
      series,
    } as EChartsOption;
  }, [
    bars,
    mas.join(","),
    boll,
    showBoll,
    chosen.join(","),
    actualHeight,
    mainHeight,
    paneHeights.join(","),
    paneTops.join(","),
    themeTick,
    waves,
    showWaves,
    waveMode,
    effectivePatternKey,
    selectedPatterns,
    turns,
    supportsLimits,
    showLimits,
    period,
    baselinePrice,
    baselineLabel,
    eventBaseline?.date,
    historyViews,
    showHistoricalEvents,
    annotations.visible,
    profile,
    profileShown,
  ]);
  return (
    <div className="candle-chart">
      {showStockSummary &&
        !frozenMode &&
        !isIndex &&
        !dataEndpoint &&
        (instrumentKind === "stock" || data?.instrument?.kind === "stock") && (
          <StockLimitSummary
            instrumentId={instrumentId}
            asOf={asOf || data?.as_of}
          />
        )}
      <div className="chart-toolbar">
        <div className="segmented">
          {(["D", "W", "M"] as const).map((v) => (
            <button
              key={v}
              className={period === v ? "active" : ""}
              aria-pressed={period === v}
              onClick={() => setPeriod(v)}
            >
              {v === "D" ? "日 K" : v === "W" ? "周 K" : "月 K"}
            </button>
          ))}
        </div>
        {frozenMode ? (
          <span
            className="chart-index-basis"
            title="报告内留存行情；不读取当前行情，不随个股复权偏好重算"
          >
            报告冻结图 · 未复权
          </span>
        ) : isIndex ? (
          <span
            className="chart-index-basis"
            title="指数显示原始点位，不使用或修改个股默认复权口径"
          >
            指数原始点位
          </span>
        ) : (
          <ChartSelect
            label="K线复权口径"
            value={priceBasis}
            onChange={setPriceBasis}
            options={[
              { value: "none", label: "未复权", description: "来源原始价格" },
              {
                value: "forward",
                label: "本地前复权",
                description:
                  forwardOption?.available === false
                    ? "公司行动条件不足，暂不可用"
                    : "以当前观察日为锚换算历史价格",
                disabled: forwardOption?.available === false,
              },
            ]}
          />
        )}
        {period === "D" && !dataEndpoint && !frozenMode ? (
          <ChartSelect
            label="图表指标连续口径"
            value={indicatorContinuity}
            title="仅切换图表MA/MACD/KDJ/BOLL；不填停牌或缺失K线，策略与回测仍按严格市场交易日"
            onChange={(value) =>
              setIndicatorContinuity(value as "quotes" | "market_sessions")
            }
            options={[
              {
                value: "quotes",
                label: "指标：报价序列",
                description: "按有效报价连续计算，缺失日不补K线",
              },
              {
                value: "market_sessions",
                label: "指标：严格交易日",
                description: "报价缺口中断指标预热；策略保持此口径",
              },
            ]}
          />
        ) : (
          <span
            className="chart-index-basis"
            title={
              period === "D"
                ? "板块日K指标采用严格市场交易日口径"
                : "周/月指标保留完整周期与报价覆盖约束"
            }
          >
            指标：{period === "D" ? "严格交易日" : "完整周期"}
          </span>
        )}
        <div className="ma-controls">
          {MA_WINDOWS.map((n, i) => (
            <ChartToggle
              key={n}
              label={`MA${n}`}
              color={maColors[i]}
              checked={mas.includes(n)}
              onChange={() => toggleMA(n)}
              title={
                last?.["ma" + n] == null
                  ? `MA${n} 需要${n}根连续有效${cycleLabel}K，当前预热不足`
                  : `MA${n} ${num(last["ma" + n])}`
              }
            />
          ))}
        </div>
        {waves && (
          <ChartToggle
            label="波浪结构"
            title={waves.notice}
            checked={showWaves}
            onChange={setShowWaves}
            color="#b292f0"
          />
        )}
        {isIndex && !frozenMode && !dataEndpoint && (
          <ChartToggle
            label="价格分布"
            title="四度空间的日线区间观察：按报价日统计经过的点位档，非盘中TPO或真实筹码"
            checked={preferences.indexShowPriceProfile}
            onChange={(value) => update({ indexShowPriceProfile: value })}
            color="#e8c16b"
          />
        )}
        {supportsLimits && period === "D" && (
          <ChartToggle
            label="涨跌停标记"
            title="仅标记经来源池与原始收盘价核验的收盘涨跌停，不含盘中触板或炸板"
            checked={showLimits}
            onChange={(value) => update({ showLimits: value })}
          />
        )}
        <button
          className="btn small chart-preferences-button"
          title="指数与个股的均线、波浪显示分别保存；修改后自动保存"
          onClick={() => setPreferencesOpen(true)}
        >
          <Settings2 size={13} />
          图表偏好
        </button>
        <ChartAnnotationButton state={annotations} />
        {!isIndex && eventBaseline && (
          <ChartToggle
            label="事件基准线"
            title={baselineView.reason}
            color="#c79136"
            checked={showEventBaseline}
            onChange={(value) => update({ showEventBaseline: value })}
          />
        )}
        {!!historyEvents.length && (
          <ChartToggle
            label="历史事件标记"
            title="显示本次选股或所选跟踪轮次实际绑定的历史事件日与事件价；不重新选择事件"
            checked={showHistoricalEvents}
            color="#bd96f3"
            onChange={setShowHistoricalEvents}
          />
        )}
        <div className="chart-zoom">
          <button className="icon-btn" title="缩小" onClick={() => zoomBy(15)}>
            <Minus size={14} />
          </button>
          <button className="icon-btn" title="放大" onClick={() => zoomBy(-15)}>
            <Plus size={14} />
          </button>
          <button
            className="icon-btn"
            title="显示全部"
            onClick={() => zoomTo(0, 100)}
          >
            <RotateCcw size={13} />
          </button>
        </div>
      </div>
      <ChartAnnotationPanel state={annotations} />
      {profileEnabled && period !== "D" && (
        <div className="source-line">
          价格分布使用日线区间；切回日 K 查看，不以周/月高低点替代日线。
          <button className="btn small" onClick={() => setPeriod("D")}>
            查看日线分布
          </button>
        </div>
      )}
      {profile && (
        <IndexPriceProfileSummary
          profile={profile}
          lookback={preferences.indexPriceProfileLookback}
          onLookbackChange={(value) =>
            update({ indexPriceProfileLookback: value })
          }
          onLocate={locateProfile}
        />
      )}
      {waves && showWaves && (
        <div className="chart-wave-selectors">
          <label>
            显示
            <ChartSelect
              label="波浪结构显示范围"
              value={waveMode}
              onChange={(value) => {
                setWaveMode(value as WaveMode);
              }}
              options={[
                { value: "latest", label: "最新结构" },
                { value: "up", label: "↑ 上涨候选" },
                { value: "down", label: "↓ 下跌候选" },
                { value: "turning", label: "仅转折" },
              ]}
            />
          </label>
          {waveMode !== "turning" && (
            <label>
              结构日期
              <ChartSelect
                label="选择波浪结构日期"
                value={effectivePatternKey}
                onChange={setWavePatternKey}
                disabled={!candidates.length}
                options={[
                  {
                    value: "latest",
                    label: candidates.length ? "最新已识别结构" : "尚未识别",
                  },
                  ...(waveMode !== "latest" && candidates.length > 1
                    ? [
                        {
                          value: "all",
                          label: `全部${waveMode === "up" ? "上涨" : "下跌"}候选（${candidates.length}）`,
                        },
                      ]
                    : []),
                  ...candidates.map((pattern) => ({
                    value: pattern.pattern_id,
                    label: `${pattern.direction === "up" ? "↑" : "↓"} ${pattern.start_date} → ${pattern.end_date}`,
                    description: pattern.has_correction
                      ? "含 ABC 修正"
                      : "五浪候选",
                  })),
                ]}
              />
            </label>
          )}
          {waveMode !== "turning" && (
            <button
              className="btn small"
              disabled={!waveWindow}
              onClick={() => {
                if (waveWindow) zoomTo(waveWindow.start, waveWindow.end);
              }}
              title={
                waveWindow
                  ? "缩放到选中结构起止，并保留两端边距"
                  : "选择一个已识别结构后可定位"
              }
            >
              <Maximize2 size={12} />
              定位结构
            </button>
          )}
          <span className="chart-wave-hint">
            {waveMode === "turning"
              ? "已确认高低点 · 不编号"
              : "推进实线 · ABC虚线 · 每个结构独立连线"}
          </span>
        </div>
      )}
      {activeBar && (
        <div className="ohlc-strip">
          <span>{activeBar.date}</span>
          <span>
            开 <b>{num(activeBar.open)}</b>
          </span>
          <span>
            高 <b>{num(activeBar.high)}</b>
          </span>
          <span>
            低 <b>{num(activeBar.low)}</b>
          </span>
          <span>
            收{" "}
            <b className={tone(activeBar.close - activeBar.open)}>
              {num(activeBar.close)}
            </b>
          </span>
          <span>
            成交额 <b>{amount(activeBar.amount)}</b>
          </span>
          <span title={activeChange.reason}>
            {activeChange.label}{" "}
            <b className={tone(activeChange.value)}>
              {percent(activeChange.value)}
            </b>
            {activeChange.value == null && (
              <small className="muted">（待核验）</small>
            )}
          </span>
          {data?.price_basis === "forward_event_affine" &&
            activeBar.raw_close != null && (
              <span>
                原始收盘 <b>{num(activeBar.raw_close)}</b>
              </span>
            )}
          <span title={activeSinceReturn.reason}>
            至{activeSinceReturn.end || "观察日"}涨跌幅{" "}
            <b className={tone(activeSinceReturn.value)}>
              {percent(activeSinceReturn.value)}
            </b>
            {activeSinceReturn.value == null && (
              <small className="muted">（待核验）</small>
            )}
          </span>
          <span className="muted">
            {period !== "D" && activeBar.complete === false
              ? (period === "M"
                  ? activeBar.monthly_quote_coverage_complete
                  : activeBar.weekly_quote_coverage_complete) === false
                ? `${cycleLabel}内报价不完整`
                : (period === "M"
                      ? activeBar.month_closed
                      : activeBar.week_closed) === false
                  ? `本${cycleLabel}形成中`
                  : `${cycleLabel}K未完整`
              : ""}
          </span>
          {supportsLimits && <LimitStatus bar={activeBar} period={period} />}
        </div>
      )}
      {data?.indicator_continuity_notice && (
        <div className="source-line chart-continuity-note">
          {data.indicator_continuity_notice}
        </div>
      )}
      {activeMAState.missing.length > 0 && (
        <div className="source-line" title={activeMAState.reason}>
          {activeBar?.date} ·{" "}
          {activeMAState.missing.map(chartIndicatorLabel).join("、")}{" "}
          {activeMAState.label} · {activeMAState.reason}
        </div>
      )}
      {!frozenMode && result.loading ? (
        <Loading />
      ) : !frozenMode && result.error ? (
        <ErrorBox message={result.error} retry={result.reload} />
      ) : !bars.length ? (
        <Empty
          title={frozenMode ? "报告中没有留存该周期K线" : "该区间暂无K线"}
          description={
            frozenMode
              ? "本图仅读取报告冻结数据，不回退到当前行情；所需周期与预热不足时保留缺口。"
              : "请先初始化行情，或选择有覆盖的日期。"
          }
        />
      ) : (
        <div
          className={`candle-plot${annotations.drawing ? " drawing-line" : ""}`}
        >
          {profileShown && (
            <span className="index-profile-plot-label">触及报价日数</span>
          )}
          <EChart
            option={option}
            height={actualHeight}
            onReady={(c) => {
              instance.current = c;
              c.getZr().on("click", (event: any) => {
                const pick = annotationPick.current;
                const point = [event.offsetX, event.offsetY];
                if (!pick || !c.containPixel({ gridIndex: 0 }, point)) return;
                const value = c.convertFromPixel(
                  { gridIndex: 0 },
                  point,
                ) as number[];
                if (Array.isArray(value)) pick(value[1]);
              });
              c.on("datazoom", () => {
                const current = (c.getOption().dataZoom as any[])?.[0];
                if (current)
                  zoomWindow.current = {
                    start: current.start ?? 0,
                    end: current.end ?? 100,
                  };
                if (profileShownRef.current) {
                  const bounds = profilePriceBounds();
                  // Merge only prices while dragging; rebuilding date axes
                  // would interrupt the slider's in-progress interaction.
                  if (bounds)
                    c.setOption({
                      yAxis: [
                        { id: "candle-main-price", ...bounds },
                        { id: "index-profile-price", ...bounds },
                      ],
                    });
                }
              });
              c.on("updateAxisPointer", (event: any) => {
                const axis = event.axesInfo?.find(
                  (item: any) => item.axisDim === "x",
                );
                if (!axis) return;
                const record =
                  typeof axis.value === "number"
                    ? latestBars.current[axis.value]
                    : latestBars.current.find(
                        (bar) => bar.date === axis.value,
                      ) || latestBars.current[Number(axis.value)];
                if (record) setActiveDate(record.date);
              });
            }}
          />
          {chosen.map((key, i) => (
            <div
              key={key}
              className={`candle-pane-value${key === "boll" ? " candle-pane-boll" : ""}`}
              style={{
                top: paneTops[i] - 18,
              }}
              aria-label={key + "副图当前值"}
            >
              <strong>
                {
                  (
                    {
                      boll: "BOLL 20 · 2",
                      volume: "成交量",
                      macd: "MACD 12 · 26 · 9",
                      kdj: "KDJ 9 · 3 · 3",
                      breadth:
                        period === "D"
                          ? "沪深样本上涨家数"
                          : "周期末沪深样本上涨家数",
                    } as Record<string, string>
                  )[key]
                }
              </strong>
              {key === "boll" && (
                <>
                  <span className="candle-boll-close">
                    收 {num(activeBar?.close)}
                  </span>
                  <span style={{ color: palette.bollUpper }}>
                    上轨 {num(activeBoll?.upper)}
                  </span>
                  <span style={{ color: palette.bollMiddle }}>
                    中轨 {num(activeBoll?.middle)}
                  </span>
                  <span style={{ color: palette.bollLower }}>
                    下轨 {num(activeBoll?.lower)}
                  </span>
                  {activeBoll?.middle == null && (
                    <span
                      className="candle-pane-reason"
                      title={activeBoll?.reason}
                    >
                      {activeBoll?.reason || "BOLL预热依据待核验"}
                    </span>
                  )}
                </>
              )}
              {key === "volume" && (
                <>
                  <span>
                    量{" "}
                    <b>
                      {compactQuantity(
                        valueAt("volume"),
                        data?.volume_unit || "股",
                      )}
                    </b>
                  </span>
                  <span>
                    额 <b>{amount(valueAt("amount"))}</b>
                  </span>
                </>
              )}
              {key === "macd" && (
                <>
                  <span style={{ color: "#e8c16b" }}>
                    DIF {num(valueAt("macd_dif"))}
                  </span>
                  <span style={{ color: "#b292f0" }}>
                    DEA {num(valueAt("macd_dea"))}
                  </span>
                  <span className={tone(valueAt("macd_hist"))}>
                    MACD {num(valueAt("macd_hist"))}
                  </span>
                </>
              )}
              {key === "kdj" && (
                <>
                  <span style={{ color: "#e8c16b" }}>
                    K {num(valueAt("kdj_k"))}
                  </span>
                  <span style={{ color: "#4cbbe3" }}>
                    D {num(valueAt("kdj_d"))}
                  </span>
                </>
              )}
              {key === "breadth" && (
                <span className="up">
                  上涨 {num(valueAt("advancers"), 0)} 家
                </span>
              )}
              {(key === "macd" || key === "kdj") &&
                (() => {
                  const state = chartIndicatorAvailability(
                    activeBar,
                    key === "macd"
                      ? ["macd_dif", "macd_dea", "macd_hist"]
                      : ["kdj_k", "kdj_d"],
                    period,
                  );
                  return state.missing.length > 0 ? (
                    <span className="muted" title={state.reason}>
                      {state.label}
                    </span>
                  ) : null;
                })()}
              <time>{activeBar?.date}</time>
            </div>
          ))}
        </div>
      )}
      <div className="chart-bottom">
        <div className="indicator-controls">
          {CHART_INDICATOR_ORDER.filter(
            (key) => key !== "breadth" || broad,
          ).map((key) => (
            <ChartToggle
              key={key}
              label={
                {
                  volume: "成交量",
                  boll: "BOLL",
                  kdj: "KDJ",
                  macd: "MACD",
                  breadth: "上涨家数",
                }[key]
              }
              title={
                key === "boll"
                  ? "独立副图K线与布林带 · 20周期 · 2倍标准差"
                  : undefined
              }
              color={key === "boll" ? palette.bollMiddle : undefined}
              checked={key === "boll" ? showBoll : panels.includes(key)}
              onChange={() =>
                key === "boll" ? setShowBoll(!showBoll) : togglePanel(key)
              }
            />
          ))}
        </div>
        <span>
          {profileShown
            ? "分布范围固定 · "
            : period === "D"
              ? "默认最近100根 · "
              : "默认全范围 · "}
          滚轮 / 双指缩放 · 拖拽平移 · 联动十字线
        </span>
      </div>
      {(!!historyViews.length || historicalChecksReason) && (
        <div className="chart-history-events" aria-label="策略历史事件参照">
          <strong>历史事件 · 实际规则绑定</strong>
          {historicalChecksReason && (
            <span className="unavailable">{historicalChecksReason}</span>
          )}
          {historyViews.map((view) => (
            <div
              key={view.event.key}
              className={view.price == null ? "unavailable" : ""}
            >
              <span>
                {view.event.label} · {view.event.match} · {view.event.date}
              </span>
              <span>
                冻结原始{eventPriceNames[view.event.priceField]}{" "}
                {num(view.event.rawPrice)} 元
              </span>
              <span>
                {view.price == null
                  ? view.reason
                  : `${view.converted ? "同图前复权" : "同图未复权"} ${num(view.price)} 元 · ${showHistoricalEvents ? "事件日与横线已显示" : "标记已隐藏"}`}
              </span>
              {view.price != null && (
                <button
                  className="btn small"
                  onClick={() => {
                    const index = bars.findIndex(
                      (bar) => bar.date === view.slot,
                    );
                    if (index >= 0) {
                      zoomTo(
                        Math.max(
                          0,
                          ((index - 5) / Math.max(1, bars.length - 1)) * 100,
                        ),
                        100,
                      );
                      setActiveDate(view.slot);
                    }
                  }}
                >
                  定位事件
                </button>
              )}
            </div>
          ))}
          <small>
            历史条件的事件价与入池/自选基准分别保存；仅作规则参照，非成交价。
            {period !== "D"
              ? "周/月K标在事件所属周期，具体事件日期仍按日K。"
              : ""}
          </small>
        </div>
      )}
      {!isIndex && eventBaseline && (
        <div
          className={`chart-baseline-note${baselineView.price == null ? " unavailable" : ""}`}
          title={baselineView.reason}
        >
          <strong>{baselineLabel}</strong>
          <span>
            {eventBaseline.date} · 冻结原始收盘 {num(eventBaseline.rawClose)} 元
          </span>
          {baselineView.price != null ? (
            <span>
              {baselineView.converted
                ? `同图前复权 ${num(baselineView.price)} 元 · 锚定日 ${baselineView.anchor}`
                : "同图未复权价"}{" "}
              · {showEventBaseline ? "横线已显示" : "横线已隐藏"}
            </span>
          ) : (
            <span>{baselineView.reason}</span>
          )}
          <span>入池参照，非成交买价</span>
        </div>
      )}
      {supportsLimits && (
        <div className="chart-limit-legend">
          {period === "D" ? (
            <>
              <span className="up">▼ 收盘涨停</span>
              <span className="down">▲ 收盘跌停</span>
              <span>{showLimits ? "标记已显示" : "标记已隐藏"}</span>
            </>
          ) : (
            <span>
              {cycleLabel}K仅汇总{cycleLabel}内具体交易日事件，不将整根
              {cycleLabel}K称为涨跌停。
            </span>
          )}
          <span>{limitCoverageText(data?.limit_event_coverage)}</span>
          <span>收盘池口径，不含触板 / 炸板。</span>
          <span>仅标已核验事件；无标记不等于未涨跌停。</span>
        </div>
      )}
      {data && (
        <div className="source-line">
          {data.price_basis === "none"
            ? "未复权"
            : ["forward", "forward_event_affine"].includes(data.price_basis)
              ? "本地前复权"
              : data.price_basis || "价格口径待核验"}{" "}
          · {bars.length} 根{cycleLabel}K ·{" "}
          {broad
            ? (data.breadth_scope || "上涨家数：沪深已留存可比样本") + " · "
            : ""}
          量：{data.volume_unit || "以源字段为准"} · 额：人民币元 ·{" "}
          {asOf ? "截止 " + asOf : "当前已发布行情"}
        </div>
      )}
      {period === "M" && bars.length > 0 && bars.length < 35 && (
        <div className="source-line">
          当前仅展示已留存的 {bars.length} 根月K；MACD / KDJ
          需要35根连续可比月K，未满足时保持未知，均线按各自月数预热。此处不代表初始化历史已完整。
        </div>
      )}
      {data?.adjustment && priceBasis === "forward" && (
        <div className="source-line">
          前复权模型 {data.adjustment.version || "公司行动仿射换算"} · 锚定日{" "}
          {data.adjustment.anchor || data.as_of || asOf || "待核验"} ·
          {data.price_basis === "none"
            ? "未通过复权校验，本图保留未复权价格"
            : "仅使用已核验公司行动，策略与回测口径独立"}
        </div>
      )}
      {!isIndex &&
        priceBasis === "none" &&
        forwardOption?.available === false && (
          <div className="source-line">
            本地前复权：{forwardOption.reason || "复权材料尚未通过校验"}
          </div>
        )}
      {waves && showWaves && (
        <div className="chart-wave-summary">
          {waveMode === "turning" ? (
            <p>
              {turns.length
                ? `仅展示${turns.length}个已确认高低点，不为普通转折添加浪号。`
                : "当前区间暂无已确认转折。"}
            </p>
          ) : selectedPatterns.length ? (
            <div className="chart-wave-patterns">
              {selectedPatterns.map((pattern) => (
                <div
                  key={pattern.pattern_id}
                  className={
                    "chart-wave-pattern direction-" + pattern.direction
                  }
                >
                  <strong>
                    {pattern.direction === "up" ? "↑" : "↓"} {pattern.label}
                    {pattern.has_correction ? " + ABC候选" : ""}
                  </strong>
                  <span>
                    {pattern.start_date} → {pattern.end_date}
                  </span>
                  <span>确认于 {pattern.confirmed_at}</span>
                </div>
              ))}
            </div>
          ) : (
            <p>
              尚未识别
              {waveMode === "up" ? "上涨" : waveMode === "down" ? "下跌" : ""}
              五浪候选；可切换“仅转折”查看已确认高低点。
            </p>
          )}
          <details>
            <summary>候选口径与规则</summary>
            <div>
              <p>
                {waves.notice ||
                  "仅检验转折价格骨架，非确定艾略特数浪或买卖点。"}
              </p>
              <p>
                转折需后续 {waves.confirmation_bars ?? 2} 根{cycleLabel}
                K确认，未确认末端不连线。向上和向下推进均使用1—5，ABC仅接在对应五浪之后。
              </p>
              {selectedPatterns.map((pattern) => (
                <p key={pattern.pattern_id}>
                  <strong>
                    {pattern.start_date} ·{" "}
                    {pattern.direction === "up" ? "↑" : "↓"}
                  </strong>{" "}
                  {pattern.rules.join("；")}。{pattern.scope}
                </p>
              ))}
            </div>
          </details>
        </div>
      )}
      {!isIndex &&
        priceBasis === "none" &&
        forwardOption?.available &&
        data?.indicator_gaps?.some((item: Row) =>
          /公司行动|除权/.test(item.reason || ""),
        ) && (
          <div className="chart-gap-repair">
            <span>
              当前未复权，公司行动后指标需重新预热。已核验事件可用本地前复权研究；无报价区间仍独立披露，不补造K线。
            </span>
            <button
              className="btn small"
              onClick={() => setPriceBasis("forward")}
            >
              切换本地前复权
            </button>
          </div>
        )}
      {data && data.indicator_gaps?.length > 0 && (
        <details className="chart-gap-explanation">
          <summary>指标断层说明 · {data.indicator_gaps.length} 段</summary>
          <ul>
            {data.indicator_gaps.map((item: Row, index: number) => (
              <li key={index}>
                {item.start_date}
                {item.end_date && item.end_date !== item.start_date
                  ? " ～ " + item.end_date
                  : ""}
                ：{item.reason}{" "}
                {item.affected_fields?.map(chartIndicatorLabel).filter(Boolean)
                  .length
                  ? "（" +
                    item.affected_fields
                      .map(chartIndicatorLabel)
                      .filter(Boolean)
                      .join("、") +
                    "）"
                  : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
      {data && data.quote_gaps?.length > 0 && (
        <details className="chart-gap-explanation">
          <summary>
            无报价区间 ·{" "}
            {data.quote_gaps.reduce(
              (sum: number, gap: Row) => sum + gap.missing_market_sessions,
              0,
            )}{" "}
            个市场交易日 · 停牌或缺数需独立核验
          </summary>
          <ul>
            {data.quote_gaps.map((gap: Row, index: number) => (
              <li key={index}>
                {gap.start_date} ～ {gap.end_date}：
                {gap.missing_market_sessions} 个市场交易日无报价；前一报价{" "}
                {gap.previous_quote_date}，下次报价 {gap.resume_quote_date}
                。未填造K线。
              </li>
            ))}
          </ul>
        </details>
      )}
      {showDataNotes && <DataNotes items={chartDataNotes(data?.warnings)} />}
      {preferencesOpen && (
        <Modal
          title={`图表偏好 · ${isIndex ? "指数" : "个股"}`}
          onClose={() => setPreferencesOpen(false)}
        >
          <div className="modal-body chart-preferences-form">
            <p className="muted">
              均线、指标显隐、波浪结构及显示范围按指数和个股分别保存，同类型图表同步。修改
              {isIndex ? "指数" : "个股"}的上述设置不会影响
              {isIndex ? "个股" : "指数"}
              ；切换、重开和刷新沿用。日 / 周 /
              月周期和缩放范围由各张图独立设置。
            </p>
            <fieldset>
              <legend>{isIndex ? "指数" : "个股"}默认均线</legend>
              <div className="chart-preferences-mas">
                {MA_WINDOWS.map((n, i) => (
                  <ChartToggle
                    key={n}
                    label={`MA${n}`}
                    color={maColors[i]}
                    checked={mas.includes(n)}
                    onChange={() => toggleMA(n)}
                  />
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend>{isIndex ? "指数" : "个股"}默认指标</legend>
              <div className="chart-preferences-mas">
                {CHART_INDICATOR_ORDER.filter(
                  (key) => key !== "breadth" || isIndex,
                ).map((key) => (
                  <ChartToggle
                    key={key}
                    label={
                      {
                        volume: "成交量",
                        boll: "BOLL 布林带",
                        kdj: "KDJ",
                        macd: "MACD",
                        breadth: "上涨家数",
                      }[key]
                    }
                    color={key === "boll" ? palette.bollMiddle : undefined}
                    checked={key === "boll" ? showBoll : panels.includes(key)}
                    onChange={() =>
                      key === "boll" ? setShowBoll(!showBoll) : togglePanel(key)
                    }
                  />
                ))}
              </div>
              <p className="muted">
                副图按成交量、BOLL、KDJ、MACD排列，各占一张副图。BOLL包含当前周期同口径蜡烛K线与上中下轨，
                按当前周期收盘价计算，参数20 ·
                2；图下开关与这里同步，也可关闭全部副图。
              </p>
            </fieldset>
            <fieldset>
              <legend>{isIndex ? "指数" : "个股"}默认波浪结构</legend>
              <ChartToggle
                label="默认显示波浪结构"
                checked={showWaves}
                onChange={setShowWaves}
                color="#b292f0"
              />
              <label>
                默认显示范围
                <ChartSelect
                  label="默认波浪结构显示范围"
                  value={waveMode}
                  onChange={(value) => setWaveMode(value as WaveMode)}
                  options={[
                    { value: "latest", label: "最新结构" },
                    { value: "up", label: "↑ 上涨候选" },
                    { value: "down", label: "↓ 下跌候选" },
                    { value: "turning", label: "仅转折" },
                  ]}
                />
              </label>
              <p className="muted">
                工具栏修改也会自动保存；具体结构日期由每张图独立选择。
              </p>
            </fieldset>
            <p className="muted">
              首次升级时，旧共享均线保留为指数设置；个股默认使用MA5、10、20、30，也可独立开启60、250。
            </p>
            <label>
              个股默认价格口径
              <ChartSelect
                label="全局个股复权口径"
                value={preferences.stockPriceBasis}
                onChange={(value) =>
                  update({
                    stockPriceBasis: value === "forward" ? "forward" : "none",
                  })
                }
                options={[
                  { value: "none", label: "未复权" },
                  { value: "forward", label: "本地前复权" },
                ]}
              />
            </label>
            <p className="muted">
              指数始终显示原始点位，不覆盖个股偏好。本地前复权仍需每只股票通过事件核验；资料不足时保留原始价并提示。
            </p>
            <ChartToggle
              label="默认显示个股日K收盘涨跌停标记"
              checked={showLimits}
              onChange={(value) => update({ showLimits: value })}
            />
            <ChartToggle
              label="默认显示跟踪图事件基准线"
              checked={showEventBaseline}
              color="#c79136"
              onChange={(value) => update({ showEventBaseline: value })}
            />
            <p className="muted">
              只保存显示偏好，不改变策略、跟踪或回测的计算口径，不保存标的、持仓或密钥。
            </p>
            {!persistent && (
              <Warning>
                浏览器未允许保存偏好，当前窗口内仍已同步；刷新后可能恢复默认。
              </Warning>
            )}
          </div>
          <div className="modal-footer">
            <button className="btn" onClick={reset}>
              恢复全部默认偏好
            </button>
            <button
              className="btn primary"
              onClick={() => setPreferencesOpen(false)}
            >
              完成
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
export function StockDrawer({
  id,
  asOf,
  onClose,
  onResearch,
  eventBaseline,
  navigation,
  onStep,
}: {
  id: string;
  asOf?: string;
  onClose: () => void;
  onResearch?: (id: string) => void;
  eventBaseline?: ChartEventBaseline;
  navigation?: StockNavigation;
  onStep?: (direction: -1 | 1) => void;
}) {
  const [loaded, setLoaded] = useState<{
    id: string;
    asOf?: string;
    data: Row;
  } | null>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const capturedFocusRef = useRef(false);
  const currentRequest = useRef({ id, asOf });
  currentRequest.current = { id, asOf };
  const detail = useApi<Row>(
    `/instruments/${encodeURIComponent(id)}${asOf ? "?as_of=" + encodeURIComponent(asOf) : ""}`,
  );
  const data =
    loaded?.id === id &&
    loaded.asOf === asOf &&
    stockPayloadMatches(loaded.data, id, asOf)
      ? loaded.data
      : null;
  const detailData = stockPayloadMatches(detail.data, id, asOf)
    ? detail.data
    : null;
  const meta = data?.instrument || detailData?.instrument || detailData || {};
  const position = stockNavigationPosition(navigation, id);
  const explicitBaseline =
    position?.current.eventBaseline ?? (position ? undefined : eventBaseline);
  const eventReference = useStockEventBaseline(
    id,
    asOf,
    ["stock", "equity", "a_share", "A"].includes(meta.kind || meta.asset_type),
    explicitBaseline,
    position?.current.historicalChecks,
    position ? navigation?.scopeKey : undefined,
  );
  useEffect(() => {
    if (!capturedFocusRef.current) {
      capturedFocusRef.current = true;
      returnFocusRef.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    }
    closeButtonRef.current?.focus({ preventScroll: true });
    window.dispatchEvent(
      new CustomEvent(STOCK_DRAWER_VISIBILITY_EVENT, {
        detail: { open: true },
      }),
    );
    return () => {
      window.dispatchEvent(
        new CustomEvent(STOCK_DRAWER_VISIBILITY_EVENT, {
          detail: { open: false },
        }),
      );
      requestAnimationFrame(() => {
        // A replacement drawer (or StrictMode's effect replay) owns focus now.
        if (document.querySelector(".stock-drawer")) return;
        const opener = returnFocusRef.current;
        if (
          opener?.isConnected &&
          opener !== document.body &&
          opener.getClientRects().length
        ) {
          opener.focus({ preventScroll: true });
        } else {
          const main = document.querySelector<HTMLElement>("main");
          if (!main) return;
          const previousTabIndex = main.getAttribute("tabindex");
          main.tabIndex = -1;
          main.focus({ preventScroll: true });
          if (previousTabIndex === null) main.removeAttribute("tabindex");
          else main.setAttribute("tabindex", previousTabIndex);
        }
      });
    };
  }, []);
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // A chart preferences dialog inside this drawer handles its own Escape.
        if (drawerRef.current?.querySelector('.modal[aria-modal="true"]'))
          return;
        e.preventDefault();
        e.stopImmediatePropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", f);
    return () => document.removeEventListener("keydown", f);
  }, [onClose]);
  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <aside
        ref={drawerRef}
        className="stock-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="个股行情详情"
      >
        <div
          className={
            "drawer-heading" +
            (position && onStep ? " with-stock-navigation" : "")
          }
        >
          <div>
            <div className="eyebrow">个股行情详情</div>
            <div className="stock-title-row">
              <h2>
                {meta.name || position?.current.name || id}{" "}
                <CopyStockCode
                  code={meta.code || position?.current.code || id.slice(0, 6)}
                  instrumentId={id}
                />
              </h2>
              <StockHeaderQuote
                data={detailData}
                instrumentId={id}
                asOf={asOf}
              />
            </div>
            {position && (
              <div className="stock-navigation-scope">
                <span>{navigation?.label}</span>
                <span>
                  {navigation?.source === "tracking"
                    ? "跟踪观察日"
                    : navigation?.source === "watchlist"
                      ? "自选观察日"
                      : navigation?.source === "market"
                        ? "盘面观察日"
                        : navigation?.source === "members"
                          ? "成员报价日"
                          : "选股观察日"}{" "}
                  {asOf || "未指定"}
                </span>
              </div>
            )}
            <StockCurrentTopics data={detailData} instrumentId={id} />
            <div className="tags">
              {(detailData?.risk_labels || []).map((t: any, i: number) => (
                <Badge key={i} tone="warn">
                  {textValue(t)}
                </Badge>
              ))}
            </div>
          </div>
          <div className="stock-heading-actions">
            {position && onStep && (
              <div
                className="stock-navigation-controls"
                role="group"
                aria-label="按当前选股结果浏览"
                title="按打开时的筛选和排序顺序浏览全部已加载结果，跨本地分页"
              >
                <button
                  className="btn small"
                  disabled={!position.previousId}
                  onClick={() => onStep(-1)}
                  aria-label="上一只股票"
                >
                  <ChevronLeft size={15} />
                  上一只
                </button>
                <output aria-live="polite" aria-atomic="true">
                  {position.position} / {position.total}
                </output>
                <button
                  className="btn small"
                  disabled={!position.nextId}
                  onClick={() => onStep(1)}
                  aria-label="下一只股票"
                >
                  下一只
                  <ChevronRight size={15} />
                </button>
              </div>
            )}
            <div className="button-group">
              {["stock", "equity", "a_share", "A"].includes(
                meta.kind || meta.asset_type,
              ) && (
                <WatchlistButton
                  instrumentId={id}
                  trackingRoundId={eventReference.sourceBaseline?.roundId}
                  sourceKind={
                    eventReference.sourceBaseline?.roundId
                      ? "tracking"
                      : "stock"
                  }
                  observedDate={asOf || detailData?.data_date}
                />
              )}
              {onResearch && (
                <button className="btn" onClick={() => onResearch(id)}>
                  个股研究
                </button>
              )}
              <button
                ref={closeButtonRef}
                className="icon-btn"
                aria-label="关闭个股详情"
                onClick={onClose}
              >
                <X size={22} />
              </button>
            </div>
          </div>
        </div>
        <div className="drawer-body">
          <StockEventBaseline state={eventReference} />
          <CandleChart
            key={id}
            instrumentId={id}
            instrumentKind={meta.kind}
            asOf={asOf}
            height={660}
            onLoad={(next) => {
              if (
                currentRequest.current.id === id &&
                currentRequest.current.asOf === asOf &&
                stockPayloadMatches(next, id, asOf)
              )
                setLoaded({ id, asOf, data: next });
            }}
            eventBaseline={eventReference.baseline}
            historicalChecks={eventReference.historicalChecks}
            historicalChecksReason={eventReference.historicalChecksReason}
            showDataNotes={false}
          />
          <Panel title="资料与风险">
            <div className="detail-list">
              <p>
                数据截止{" "}
                <strong>
                  {data?.as_of || asOf || data?.bars?.at(-1)?.date || "—"}
                </strong>
              </p>
              <p>
                最新换手率{" "}
                <strong>
                  {percent(
                    detailData?.turnover_rate ??
                      data?.bars?.at(-1)?.turnover_rate,
                  )}
                </strong>
              </p>
              <p>
                真实盘口时序 <Badge tone="warn">未提供</Badge>
              </p>
            </div>
            <DataNotes
              items={chartDataNotes(data?.warnings, detailData?.warnings)}
            />
            <p className="footnote">
              缺失字段保持未知。行情详情不自动触发公告研究。
            </p>
          </Panel>
        </div>
      </aside>
    </div>
  );
}
export function EquityChart({
  rows,
  initialCapital,
}: {
  rows: Row[];
  initialCapital?: unknown;
}) {
  const [mode, setMode] = useState<EquityCurveMode>("return");
  const [, refreshTheme] = useState(0);
  const zoomWindow = useRef({ start: 0, end: 100 });
  useEffect(() => {
    const changed = () => refreshTheme((value) => value + 1);
    window.addEventListener("workbench-theme", changed);
    return () => window.removeEventListener("workbench-theme", changed);
  }, []);
  const pal = chartPalette();
  const capitalKnown = validInitialCapital(initialCapital);
  const displayedMode = mode === "return" && !capitalKnown ? "equity" : mode;
  const points = equityCurvePoints(rows, initialCapital);
  const primaryValues = points.map((point) =>
    displayedMode === "return" ? point.cumulativeReturn : point.equity,
  );
  const usable = primaryValues.some((value) => value !== null);
  const base =
    displayedMode === "return" ? 0 : capitalKnown ? initialCapital : undefined;
  const option: EChartsOption = {
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
        data: points.map((point) => point.date),
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: pal.line } },
      },
      {
        type: "category",
        gridIndex: 1,
        data: points.map((point) => point.date),
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
