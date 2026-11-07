import { useEffect, useRef, useState, useMemo } from "react";
import {
  useSinglePageEntry,
  singlePagePaneLayout,
  singlePageDefaultWindow,
} from "./SinglePageEntry";
import "./SinglePageEntry.css";
import * as echarts from "echarts/core";
import type { EChartsOption } from "echarts";
import {
  BarChart,
  CandlestickChart,
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
import { chartAutomaticTrendlines } from "./chartAutomaticTrendlines";
import { candleChange } from "./candleChange";
import { candleExtremeChange } from "./candleExtremeChange";
import { bollCandleValues, chartBoll } from "./chartBoll";
import {
  chartHoverSummaryHeight,
  chartPaneLayout,
  chartTextSizes,
} from "./chartPaneLayout";
import { chartSinceReturn } from "./chartSinceReturn";
import { chartSessionDistance } from "./chartSessionDistance";
import { chartPriceBasis } from "./chartPriceBasis";
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
  barLimitMarker,
  barBrokenMarker,
  barLimitDownPriceReference,
  LIMIT_DOWN_REFERENCE_LABEL,
  LimitStatus,
  PatternStatus,
  limitCoverageText,
  limitTooltipHtml,
} from "./CandleLimitEvents";
import "./Charts.css";
import "./StockNavigation.css";
import StockLimitSummary from "./StockLimitSummary";
import BoardTags from "../features/market/BoardTags";
import StockHeaderQuote from "./StockHeaderQuote";
import StockCompactMetrics from "./StockCompactMetrics";
import {
  noonCollectionTime,
  provisionalNoonBar,
  type NoonChartPreview,
} from "./noonChartPreview";
import StockChipDistribution from "./StockChipDistribution";
import { stockPatternHeader } from "./stockPatternHeader";
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
import {
  annotationColors,
  lineLabel,
  savedLineSettings,
  levelOriginLabel,
} from "./chartAnnotations";
import type { ChartLine } from "./chartAnnotations";
import { ChartLineControls, ChartLineRail } from "./ChartLineLabels";
import {
  candlePriceAxis,
  chartLineRailWidth,
  readableChartWindow,
  visibleChartLineLabels,
} from "./chartLineLabelLayout";
import type { ChartLineLabel } from "./chartLineLabelLayout";
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
import { projectEventBaseline } from "./chartDisplay";
import { candleChartEndpoint, initialChartResponse } from "./chartEndpoint";
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
const EMPTY_LINE_IDS = new Set<string>();
function chartPalette() {
  const light = document.documentElement.dataset.theme === "light";
  return {
    text: light ? "#768298" : "#7d8ba0",
    axisText: light ? "#586980" : "#9aacc3",
    line: light ? "#e6eaf0" : "#202b3a",
    bg: light ? "#ffffff" : "#111924",
    tooltip: light ? "#ffffff" : "#192332",
    tooltipText: light ? "#34435a" : "#dce6f5",
    provisional: light ? "#875b12" : "#efba55",
    reference: light ? "#7754b4" : "#b99afa",
    up: light ? "#d85151" : "#ed6865",
    down: light ? "#119573" : "#39bd9b",
    bollUpper: light ? "#ce4147" : "#f07178",
    bollMiddle: light ? "#00868f" : "#49c7db",
    bollLower: light ? "#228447" : "#57cc83",
    waveCorrection: light ? "#7754b4" : "#b292f0",
    waveTurning: light ? "#758496" : "#a0aec1",
    trendFocus: light ? "#792bb0" : "#e6b4ff",
    trendFocusText: light ? "#fff" : "#241333",
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
  initialChart,
  showStockSummary = true,
  showDataNotes = true,
  frozenChartsByPeriod,
  entryFocus = false,
  controlledPeriod,
  onPeriodChange,
  onBoardPresentation,
  noonPreview,
  noonMode = Boolean(noonPreview),
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
  initialChart?: { endpoint: string; data: Row };
  showStockSummary?: boolean;
  showDataNotes?: boolean;
  frozenChartsByPeriod?: Partial<Record<ChartPeriod, Row>>;
  entryFocus?: boolean;
  controlledPeriod?: ChartPeriod;
  onPeriodChange?: (period: ChartPeriod) => void;
  noonPreview?: NoonChartPreview;
  noonMode?: boolean;
  onBoardPresentation?: (value: { row: Row; date: string } | null) => void;
}) {
  const { preferences, persistent, update, reset } = useChartPreferences();
  const plotRoot = useRef<HTMLDivElement>(null);
  const [{ width: plotWidth, wideViewport }, setPlotSize] = useState({
    width: 0,
    wideViewport: false,
  });
  const [railChart, setRailChart] = useState<echarts.ECharts | null>(null);
  const [lineControlsOpen, setLineControlsOpen] = useState(false);
  const [hiddenLineState, setHiddenLineState] = useState<{
    scope: string;
    ids: Set<string>;
  }>({ scope: "", ids: new Set() });
  const defaultAdapted = useRef(false);
  const userZoomed = useRef(false);
  const entryRoot = useRef<HTMLDivElement>(null);
  const entryStage = useRef<HTMLElement>(null);
  const [entryCanvasReadyKey, setEntryCanvasReadyKey] = useState("");
  const [entryLocateNonce, setEntryLocateNonce] = useState(0);
  const [fitPanesToWindow, setFitPanesToWindow] = useState(false);
  const [entryPanels, setEntryPanels] = useState([
    "volume",
    "boll",
    "kdj",
    "macd",
  ]);
  const knownIndex = [
    "000001.SH",
    "399001.SZ",
    "399006.SZ",
    "000688.SH",
  ].includes(instrumentId.toUpperCase());
  const [localPeriod, setLocalPeriod] = useState<ChartPeriod>("D"),
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
  const period = controlledPeriod ?? localPeriod;
  const [showHistoricalEvents, setShowHistoricalEvents] = useState(true);
  const isIndex =
    knownIndex ||
    instrumentKind === "index" ||
    (resolvedKind?.id === instrumentId && resolvedKind.kind === "index");
  const defaultAutomaticTrendlines = isIndex
    ? preferences.indexShowAutomaticTrendlines
    : preferences.stockShowAutomaticTrendlines;
  const [automaticTrendlineOverride, setAutomaticTrendlineOverride] = useState<
    boolean | null
  >(null);
  useEffect(() => {
    setAutomaticTrendlineOverride(null);
  }, [instrumentId, isIndex, defaultAutomaticTrendlines]);
  const showAutomaticTrendlines =
    automaticTrendlineOverride ?? defaultAutomaticTrendlines;
  const defaultShowAllTrendHistory = isIndex
    ? preferences.indexShowAllTrendHistory
    : preferences.stockShowAllTrendHistory;
  const [showAllTrendHistoryOverride, setShowAllTrendHistoryOverride] =
    useState<boolean | null>(null);
  useEffect(
    () => setShowAllTrendHistoryOverride(null),
    [instrumentId, isIndex, defaultShowAllTrendHistory],
  );
  const showAllTrendHistory =
    showAllTrendHistoryOverride ?? defaultShowAllTrendHistory;
  const [trendLayers, setTrendLayers] = useState({
    main: true,
    near: true,
    zones: true,
    events: true,
  });
  const [selectedTrendEvent, setSelectedTrendEvent] = useState<number | null>(
    null,
  );
  useEffect(() => setSelectedTrendEvent(null), [instrumentId, asOf, period]);
  const setTrendLayer = (layer: keyof typeof trendLayers, value: boolean) =>
    setTrendLayers((previous) => ({ ...previous, [layer]: value }));
  const priceBasis = effectiveChartPriceBasis(preferences, isIndex);
  const [hiddenTrendItems, setHiddenTrendItems] = useState(EMPTY_LINE_IDS);
  useEffect(
    () => setHiddenTrendItems(EMPTY_LINE_IDS),
    [instrumentId, asOf, period, priceBasis],
  );
  const toggleTrendItem = (id: string) =>
    setHiddenTrendItems((previous) => {
      const next = new Set(previous);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const cycleLabel = period === "D" ? "日" : period === "W" ? "周" : "月";
  const mas = effectiveChartMas(preferences, isIndex);
  const panels = entryFocus
    ? entryPanels.filter((key) => key !== "boll")
    : effectiveChartPanels(preferences, isIndex);
  const showBoll = entryFocus
    ? entryPanels.includes("boll")
    : effectiveChartBoll(preferences, isIndex);
  const setShowBoll = (value: boolean) => {
    if (entryFocus)
      setEntryPanels((current) =>
        value
          ? [...new Set([...current, "boll"])]
          : current.filter((key) => key !== "boll"),
      );
    else update(isIndex ? { indexShowBoll: value } : { stockShowBoll: value });
  };
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
    userZoomed.current = true;
    zoomWindow.current = { start, end };
    instance.current?.dispatchAction({ type: "dataZoom", start, end });
  }
  function zoomBy(delta: number) {
    const current = zoomWindow.current;
    const width = Math.min(
      100,
      Math.max(5, current.end - current.start + delta),
    );
    const center = (current.start + current.end) / 2;
    const start = Math.min(100 - width, Math.max(0, center - width / 2));
    zoomTo(start, start + width);
  }
  const frozenMode = frozenChartsByPeriod !== undefined;
  const chartRequestPath = frozenMode
    ? null
    : candleChartEndpoint({
        instrumentId,
        period,
        priceBasis,
        indicatorContinuity,
        asOf,
        dataEndpoint,
      });
  const entryCanvasKey =
    chartRequestPath || `frozen|${instrumentId}|${asOf || ""}|${period}`;
  const [initialRequestPath] = useState(() => chartRequestPath);
  const [initialRequestActive, setInitialRequestActive] = useState(true);
  useEffect(() => {
    if (chartRequestPath !== initialRequestPath) setInitialRequestActive(false);
  }, [chartRequestPath, initialRequestPath]);
  // Once controls select another request, returning to D performs a fresh GET.
  // The owner may refresh its initial response while that first D view is open.
  const initialData =
    !frozenMode &&
    initialRequestActive &&
    chartRequestPath === initialRequestPath
      ? initialChartResponse(
          initialChart,
          dataEndpoint,
          instrumentId,
          asOf,
          period,
        )
      : null;
  const result = useApi<Row>(initialData ? null : chartRequestPath);
  const data = frozenMode
    ? frozenChartsByPeriod[period] || null
    : initialData || result.data;
  const completedBars = useMemo<Row[]>(() => data?.bars || [], [data]);
  const noonBar = useMemo(
    () => provisionalNoonBar(noonPreview, data, period),
    [noonPreview, data, period],
  );
  const bars = useMemo<Row[]>(
    () => (noonBar ? [...completedBars, noonBar] : completedBars),
    [completedBars, noonBar],
  );
  const automaticTrendlines = useMemo(
    () => chartAutomaticTrendlines(data, instrumentId, period, asOf),
    [data, instrumentId, period, asOf],
  );
  useEffect(() => {
    const node = plotRoot.current;
    if (!node) return;
    const wideScreen = window.matchMedia("(min-width: 1600px)");
    const measure = () => {
      const width = node.getBoundingClientRect().width;
      const wideViewport = wideScreen.matches;
      setPlotSize((size) =>
        size.width === width && size.wideViewport === wideViewport
          ? size
          : { width, wideViewport },
      );
    };
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    wideScreen.addEventListener("change", measure);
    measure();
    return () => {
      observer.disconnect();
      wideScreen.removeEventListener("change", measure);
    };
  }, [!!bars.length, result.loading, result.error, frozenMode]);
  const entryHeight = useSinglePageEntry(
    entryRoot,
    entryStage,
    `${instrumentId}|${asOf || ""}|${controlledPeriod === undefined ? period : ""}|${entryLocateNonce}`,
    entryFocus,
    !result.loading &&
      (!!data || !!result.error) &&
      (!bars.length || entryCanvasReadyKey === entryCanvasKey),
  );
  const annotations = useChartAnnotations(
    instrumentId,
    data,
    !frozenMode &&
      !dataEndpoint &&
      (isIndex ||
        instrumentKind === "stock" ||
        data?.instrument?.kind === "stock"),
  );
  const annotationPick = useRef<((price: number) => void) | null>(null);
  annotationPick.current = annotations.drawing ? annotations.picked : null;
  const entryWindow = useMemo(
    () => singlePageDefaultWindow(period, completedBars, data),
    [period, completedBars, data],
  );
  const nextZoomContext = JSON.stringify([
    instrumentId,
    asOf || null,
    period,
    dataEndpoint || null,
    data?.as_of || null,
  ]);
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
  const lineScope = `${instrumentId}|${dataEndpoint || ""}|${frozenMode ? "frozen" : "live"}`;
  useEffect(() => setLineControlsOpen(false), [lineScope]);
  const hiddenLineIds =
    hiddenLineState.scope === lineScope ? hiddenLineState.ids : EMPTY_LINE_IDS;
  const allAnnotationViews = useMemo(
    () => [
      ...annotations.views.filter((view) => view.price != null),
      ...(annotations.preview?.items || [])
        .filter(
          (item: Row) =>
            !item.saved &&
            annotations.selectedCandidates.includes(item.candidate_id),
        )
        .map((item: Row) => ({
          line: {
            ...item,
            annotation_id: `preview:${item.candidate_id}`,
            instrument_id: instrumentId,
            label: item.line_type === "support" ? "候选支撑" : "候选压力",
            color: item.line_type === "support" ? "green" : "red",
          } as ChartLine,
          price: item.price,
          converted: false,
          anchor: item.anchor_date,
          reason: "尚未保存的本次候选",
        })),
    ],
    [
      annotations.views,
      annotations.preview,
      annotations.selectedCandidates,
      instrumentId,
    ],
  );
  const allLineLabels = useMemo<ChartLineLabel[]>(() => {
    const lines: ChartLineLabel[] = allAnnotationViews
      .filter((view) => Number.isFinite(view.price) && view.price! > 0)
      .map((view) => ({
        id: `annotation:${view.line.annotation_id}`,
        kind: savedLineSettings(view.line).line_type,
        group: "annotation",
        price: view.price!,
        color: annotationColors[view.line.color] || "#79a9ff",
        enabled: annotations.show,
        detail: `${lineLabel(view.line, view.price!)} · ${levelOriginLabel(view.line)} · ${view.reason}`,
      }));
    if (baselineView.price != null)
      lines.push({
        id: `baseline:${eventBaseline?.date}:${eventBaseline?.roundId || baselineLabel}`,
        kind: "event",
        group: "baseline",
        price: baselineView.price,
        color: "#c79136",
        enabled: showEventBaseline,
        detail: `${baselineLabel} · ${eventBaseline?.date} · 本图 ${num(baselineView.price, 4)} · 原始收盘 ${num(eventBaseline?.rawClose, 4)} · ${baselineView.reason}`,
      });
    historyViews.forEach((view, index) => {
      if (view.price == null || view.closePrice == null || !view.slot) return;
      lines.push({
        id: `history:${view.event.key}:${view.event.date}`,
        kind: "event",
        group: "history",
        price: view.price,
        color: ["#bd96f3", "#4cbbe3", "#e8c16b"][index % 3],
        enabled: showHistoricalEvents,
        detail: `${view.event.label} · ${view.event.date} ${eventPriceNames[view.event.priceField]} · 本图 ${num(view.price, 4)} · 原价 ${num(view.event.rawPrice, 4)} · ${view.event.match} · 绑定观察日 ${view.event.observed}${view.converted ? " · 已按本图复权锚换算，原始事件保持不变" : ""}`,
      });
    });
    return lines;
  }, [
    allAnnotationViews,
    annotations.show,
    baselineView.price,
    baselineView.reason,
    baselineLabel,
    eventBaseline,
    showEventBaseline,
    historyViews,
    showHistoricalEvents,
  ]);
  const visibleLineLabels = useMemo(
    () => visibleChartLineLabels(allLineLabels, hiddenLineIds),
    [allLineLabels, hiddenLineIds],
  );
  const visibleLineIds = new Set(visibleLineLabels.map((line) => line.id));
  const lineRailWidth = visibleLineLabels.length
    ? chartLineRailWidth(plotWidth)
    : 0;
  const plotRight = lineRailWidth ? lineRailWidth + 8 : 20;
  const readableWindow = readableChartWindow(
    entryWindow,
    period,
    bars,
    plotWidth - 62 - plotRight,
  );
  if (bars.length && zoomContext.current !== nextZoomContext) {
    zoomWindow.current = {
      start: readableWindow.start,
      end: readableWindow.end,
    };
    zoomContext.current = nextZoomContext;
    defaultAdapted.current = plotWidth > 0 && !annotations.result.loading;
    userZoomed.current = false;
  } else if (
    bars.length &&
    plotWidth > 0 &&
    !defaultAdapted.current &&
    !userZoomed.current
  ) {
    zoomWindow.current = {
      start: readableWindow.start,
      end: readableWindow.end,
    };
    defaultAdapted.current = !annotations.result.loading;
  }
  const toggleLine = (id: string, checked: boolean) => {
    const line = allLineLabels.find((item) => item.id === id);
    if (!line) return;
    setHiddenLineState((old) => {
      const ids = new Set(old.scope === lineScope ? old.ids : []);
      if (checked && !line.enabled)
        allLineLabels
          .filter((item) => item.group === line.group)
          .forEach((item) => ids.add(item.id));
      checked ? ids.delete(id) : ids.add(id);
      return { scope: lineScope, ids };
    });
    if (checked && !line.enabled) {
      if (line.group === "annotation") annotations.setShow(true);
      else if (line.group === "baseline") update({ showEventBaseline: true });
      else setShowHistoricalEvents(true);
    }
  };
  const toggleAllLines = (show: boolean) => {
    setHiddenLineState({
      scope: lineScope,
      ids: show ? new Set() : new Set(allLineLabels.map((line) => line.id)),
    });
    if (show) {
      annotations.setShow(true);
      update({ showEventBaseline: true });
      setShowHistoricalEvents(true);
    }
  };
  const latestBars = useRef(bars);
  latestBars.current = bars;
  useEffect(() => setActiveDate(null), [instrumentId, asOf, period]);
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
  const togglePanel = (key: ChartPanel) => {
    if (entryFocus)
      setEntryPanels((current) =>
        current.includes(key)
          ? current.filter((value) => value !== key)
          : [...current, key],
      );
    else
      update({
        [isIndex ? "indexPanels" : "stockPanels"]: panels.includes(key)
          ? panels.filter((value) => value !== key)
          : [...panels, key],
      });
  };
  // Hidden overlays should not scan the entire five-year response on open.
  // Re-enabling one still computes from this chart's current dated payload.
  const boll = useMemo(
    () => (showBoll ? chartBoll(data) : []),
    [data, showBoll],
  );
  const last = bars.at(-1);
  const activeBar = bars.find((b) => b.date === activeDate) || last;
  const activeBoll = boll.find((point) => point.date === activeBar?.date);
  const activeChange = activeBar?.noon_provisional
    ? {
        label: "午间涨跌幅",
        value: activeBar.change_pct,
        reason: "午间快照记录的涨跌幅",
      }
    : candleChange(activeBar, period);
  const activeExtremes = candleExtremeChange(activeBar, period);
  const activeSinceReturn = chartSinceReturn(activeBar, data);
  const activeSessionDistance = chartSessionDistance(activeBar, data);
  const actualPriceBasis = chartPriceBasis(data, isIndex);
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
  const { actualHeight, mainHeight, paneHeights, paneTops } = entryFocus
    ? singlePagePaneLayout(entryHeight, chosen, fitPanesToWindow)
    : chartPaneLayout(
        height,
        chosen.length,
        compact,
        chosen.map((key) => (key === "boll" ? 140 : 55)),
      );
  const waves = data?.waves;
  const waveAsOf = data?.as_of || asOf || last?.date || "";
  const patterns = useMemo(
    () =>
      showWaves && waveMode !== "turning"
        ? wavePatterns(
            waves,
            completedBars.map((bar) => bar.date),
            waveAsOf,
          )
        : [],
    [waves, completedBars, waveAsOf, showWaves, waveMode],
  );
  const turns = useMemo(
    () =>
      showWaves && waveMode === "turning"
        ? waveTurningPoints(
            waves,
            completedBars.map((bar) => bar.date),
            waveAsOf,
          )
        : [],
    [waves, completedBars, waveAsOf, showWaves, waveMode],
  );
  const candidates = useMemo(
    () => waveCandidates(patterns, waveMode),
    [patterns, waveMode],
  );
  const effectivePatternKey = candidates.some(
    (pattern) => pattern.pattern_id === wavePatternKey,
  )
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
  const {
    wide: widePlot,
    axis: axisFontSize,
    tooltip: tooltipFontSize,
  } = chartTextSizes(plotWidth, wideViewport);
  const option = useMemo(() => {
    const pal = chartPalette(),
      dates = bars.map((b) => b.date);
    const axisColor = widePlot ? pal.axisText : pal.text;
    const grids: any[] = [
        { left: 62, right: plotRight, top: 24, height: mainHeight },
      ],
      xaxes: any[] = [],
      yaxes: any[] = [],
      series: any[] = [];
    chosen.forEach((k, i) =>
      grids.push({
        left: 62,
        right: plotRight,
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
          color: axisColor,
          fontSize: axisFontSize,
          show: i === grids.length - 1,
          formatter: (v: string) => v.slice(5),
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisPointer: { label: { backgroundColor: "#435d7d" } },
      });
      yaxes.push({
        type: "value",
        gridIndex: i,
        scale: true,
        ...(i === 0 ? candlePriceAxis(bars, zoomWindow.current) : {}),
        splitNumber: i === 0 ? 4 : 2,
        axisLabel: {
          color: axisColor,
          fontSize: axisFontSize,
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
      barMaxWidth: 22,
    });
    if (showAutomaticTrendlines) {
      const plotTrend = (line: (typeof automaticTrendlines.lines)[number]) => ({
        name: line.label,
        type: "line",
        data: line.values,
        xAxisIndex: 0,
        yAxisIndex: 0,
        z: 6,
        silent: true,
        clip: true,
        showSymbol: false,
        animation: false,
        tooltip: { show: false },
        lineStyle: {
          color: line.color,
          width: line.scope === "near" ? 2.7 : 3.2,
          opacity: trendLayers.events && selectedTrendEvent != null ? 0.65 : 1,
          type: line.stroke,
        },
      });
      for (const line of automaticTrendlines.lines)
        if (trendLayers[line.scope] && !hiddenTrendItems.has(line.id))
          series.push(plotTrend(line));
      const zones = automaticTrendlines.zones.filter(
        (zone) => !hiddenTrendItems.has(zone.id),
      );
      if (trendLayers.zones && zones.length)
        series.push({
          name: "水平观察区",
          type: "line",
          data: [],
          xAxisIndex: 0,
          yAxisIndex: 0,
          z: 0,
          silent: true,
          tooltip: { show: false },
          markArea: {
            silent: true,
            animation: false,
            label: { show: false },
            data: zones.map((zone) => [
              {
                coord: [zone.startDate, zone.low],
                itemStyle: {
                  color: `${zone.color}40`,
                  borderColor: zone.color,
                  borderWidth: 1,
                },
              },
              { coord: [zone.endDate, zone.high] },
            ]),
          },
        });
      if (trendLayers.events)
        for (const [index, event] of automaticTrendlines.events.entries()) {
          const selected = selectedTrendEvent === index;
          series.push({
            ...plotTrend(event.line),
            name: event.label,
            data: selected || showAllTrendHistory ? event.line.values : [],
            z: selected ? 12 : 5,
            lineStyle: {
              color: selected ? pal.trendFocus : event.line.color,
              width: selected ? 4.5 : 2.4,
              opacity: selected ? 1 : 0.7,
              type: "dotted",
              shadowColor: pal.bg,
              shadowBlur: selected ? 3 : 0,
            },
            markPoint: {
              silent: true,
              symbol: "diamond",
              symbolSize: selected ? 29 : 23,
              itemStyle: {
                color: selected ? pal.trendFocus : event.line.color,
                borderColor: pal.bg,
                borderWidth: 2,
              },
              label: {
                show: true,
                formatter: String(index + 1),
                color: selected ? pal.trendFocusText : "#fff",
                fontSize: 10,
              },
              data: [{ coord: [event.date, event.price], name: event.label }],
            },
          });
        }
    }
    historyViews.forEach((view, index) => {
      if (view.price == null || view.closePrice == null || !view.slot) return;
      if (!visibleLineIds.has(`history:${view.event.key}:${view.event.date}`))
        return;
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
          lineStyle: { color, width: 1, type: "dashed" },
          label: { show: false },
          data: [
            {
              xAxis: view.slot,
              lineStyle: { opacity: 0.45, type: "dotted" },
              label: { show: false },
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
    if (visibleLineLabels.length)
      series.push({
        name: "支撑压力与事件参照",
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
          label: { show: false },
          data: visibleLineLabels.map((line) => ({
            name: line.detail,
            yAxis: line.price,
            lineStyle: {
              color: line.color,
              width: 1,
              type: "dashed",
              opacity: 0.75,
            },
            label: { show: false },
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
          data: bars.flatMap((bar) => {
            const marker = barLimitMarker(bar, kind);
            return marker
              ? [
                  {
                    value: [bar.date, up ? bar.high : bar.low],
                    name: marker.name,
                    label: { formatter: marker.text },
                  },
                ]
              : [];
          }),
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
      const broken = bars.flatMap((bar) => {
        const marker = barBrokenMarker(bar);
        return marker
          ? [
              {
                value: [bar.date, bar.high],
                name: marker.name,
                label: { formatter: marker.text },
              },
            ]
          : [];
      });
      if (broken.length)
        series.push({
          name: "炸板",
          type: "scatter",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: broken,
          symbol: "diamond",
          symbolSize: 7,
          symbolOffset: [0, -8],
          itemStyle: { color: pal.provisional, opacity: 1 },
          label: {
            show: true,
            position: "top",
            distance: 2,
            fontSize: 10,
            fontWeight: 600,
            color: pal.provisional,
          },
          labelLayout: { hideOverlap: true },
          tooltip: { show: false },
          clip: false,
          z: 8,
        });
      const priceReferences = bars.flatMap((bar) =>
        barLimitDownPriceReference(bar) ? [[bar.date, bar.low]] : [],
      );
      if (priceReferences.length)
        series.push({
          name: LIMIT_DOWN_REFERENCE_LABEL,
          type: "scatter",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: priceReferences,
          symbol: "emptyCircle",
          symbolSize: 7,
          symbolOffset: [0, 8],
          itemStyle: { color: pal.reference, opacity: 1 },
          label: {
            show: true,
            formatter: "跌?",
            position: "bottom",
            distance: 2,
            fontSize: 10,
            fontWeight: 600,
            color: pal.reference,
          },
          labelLayout: { hideOverlap: true },
          tooltip: { show: false },
          clip: false,
          z: 8,
        });
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
          data: completedBars.map(bollCandleValues),
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
        link: [{ xAxisIndex: "all" }],
        lineStyle: { color: "#61758e", type: "dashed" },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: pal.tooltip,
        borderColor: pal.line,
        textStyle: {
          color: widePlot ? pal.tooltipText : pal.text,
          fontSize: tooltipFontSize,
        },
        confine: true,
        formatter: (params: any) => {
          const entries = Array.isArray(params) ? params : [params],
            candle = entries.find((entry: any) => entry.seriesName === "K线"),
            b =
              bars.find((bar) => bar.date === entries[0]?.axisValue) ||
              bars[candle?.dataIndex ?? entries[0]?.dataIndex];
          if (!b) return "";
          if (b.noon_provisional) {
            const snapshotLabel = b.intraday_provisional ? "盘中" : "午间";
            const changeColor =
              b.change_pct > 0
                ? pal.up
                : b.change_pct < 0
                  ? pal.down
                  : pal.tooltipText;
            return (
              `<div style="color:${pal.tooltipText}"><strong>${b.date}</strong> <strong style="color:${pal.provisional}">· ${snapshotLabel}未收盘</strong>` +
              `<div>开 ${num(b.open)}　${snapshotLabel}价 <strong>${num(b.close)}</strong></div>` +
              `<div>高 ${num(b.high)}　低 ${num(b.low)}</div>` +
              `<div>${snapshotLabel}涨跌幅 <strong style="color:${changeColor}">${percent(b.change_pct)}</strong></div>` +
              limitTooltipHtml(b, period, {
                up: pal.up,
                down: pal.down,
                text: pal.tooltipText,
                warning: pal.provisional,
                reference: pal.reference,
              }) +
              "</div>"
            );
          }
          const change = candleChange(b, period);
          const extremes = candleExtremeChange(b, period);
          const since = chartSinceReturn(b, data);
          const sessionDistance = chartSessionDistance(b, data);
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
          const extremeHtml = (side: "high" | "low") => {
            const view = extremes[side];
            const color =
              view.value == null || view.value === 0
                ? pal.text
                : view.value > 0
                  ? pal.up
                  : pal.down;
            return `<span style="color:${color}" title="${escape(view.reason)}">（${view.label} ${percent(view.value)}${view.value == null ? " · 待核验" : ""}）</span>`;
          };
          return (
            `<div style="font-weight:700;margin-bottom:6px;color:${pal.text}">${b.date}${b.noon_provisional ? " · 午间未收盘" : ""}</div>` +
            `<div style="margin-bottom:6px;color:${pal.text}" title="${escape(sessionDistance.reason)}">${escape(sessionDistance.label)}</div>` +
            `<div>开 ${num(b.open)}　${b.noon_provisional ? "午间价" : "收"} ${num(b.close)}</div>` +
            `<div>高 ${num(b.high)} ${extremeHtml("high")}</div>` +
            `<div>低 ${num(b.low)} ${extremeHtml("low")}</div>` +
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
          xAxisIndex: grids.map((_, i) => i),
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
          xAxisIndex: grids.map((_, i) => i),
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
    data?.as_of,
    data?.trading_day_distance,
    automaticTrendlines,
    hiddenTrendItems,
    showAutomaticTrendlines,
    trendLayers,
    selectedTrendEvent,
    showAllTrendHistory,
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
    visibleLineLabels,
    plotRight,
    plotWidth,
    widePlot,
    axisFontSize,
    tooltipFontSize,
  ]);
  return (
    <div
      ref={entryRoot}
      data-default-window-start={
        entryFocus ? readableWindow.firstDate || undefined : undefined
      }
      data-default-window-sessions={
        entryFocus ? entryWindow.sessions : undefined
      }
      data-wide-plot={widePlot || undefined}
      className={"candle-chart" + (entryFocus ? " entry-single-chart" : "")}
    >
      {showStockSummary &&
        !frozenMode &&
        !isIndex &&
        !dataEndpoint &&
        (instrumentKind === "stock" || data?.instrument?.kind === "stock") && (
          <StockLimitSummary
            instrumentId={instrumentId}
            asOf={asOf || data?.as_of}
            showBoardTags={!entryFocus}
            onBoardData={onBoardPresentation}
          />
        )}
      <section
        ref={entryStage}
        data-chart-period={entryFocus ? period : undefined}
        className={entryFocus ? "entry-single-stage" : undefined}
        aria-label={entryFocus ? "K线与全部技术副图" : undefined}
      >
        <div className="chart-toolbar">
          {controlledPeriod === undefined && (
            <div className="segmented">
              {(["D", "W", "M"] as const).map((v) => (
                <button
                  key={v}
                  className={period === v ? "active" : ""}
                  aria-pressed={period === v}
                  onClick={() => {
                    (onPeriodChange ?? setLocalPeriod)(v);
                    if (entryFocus) setEntryLocateNonce((value) => value + 1);
                  }}
                >
                  {v === "D" ? "日 K" : v === "W" ? "周 K" : "月 K"}
                </button>
              ))}
            </div>
          )}
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
          {data && !frozenMode && !isIndex && (
            <span className="chart-index-basis" title={actualPriceBasis.reason}>
              图表价格：{actualPriceBasis.label}
            </span>
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
          {automaticTrendlines.available && (
            <div className="chart-auto-trends">
              <ChartToggle
                label="自动趋势线"
                checked={showAutomaticTrendlines}
                onChange={setAutomaticTrendlineOverride}
                title={automaticTrendlines.reason}
              />
              <details className="chart-auto-trend-layers">
                <summary className="btn small">图层</summary>
                <div className="chart-auto-trend-layer-menu">
                  <ChartToggle
                    label="主线"
                    checked={trendLayers.main}
                    onChange={(value) => setTrendLayer("main", value)}
                  />
                  <ChartToggle
                    label="近端线"
                    checked={trendLayers.near}
                    onChange={(value) => setTrendLayer("near", value)}
                  />
                  <ChartToggle
                    label="水平区"
                    checked={trendLayers.zones}
                    onChange={(value) => setTrendLayer("zones", value)}
                  />
                  <ChartToggle
                    label="穿线记录"
                    checked={trendLayers.events}
                    onChange={(value) => setTrendLayer("events", value)}
                  />
                </div>
              </details>
            </div>
          )}
          {supportsLimits && period === "D" && (
            <ChartToggle
              label="涨跌停标记"
              title="显示已核验收盘涨跌停及地天、一字日形态；同日合并标注，不按涨幅或图上价格猜测"
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
          <ChartLineControls
            lines={allLineLabels}
            visible={visibleLineLabels}
            open={lineControlsOpen}
            onOpen={setLineControlsOpen}
            onToggle={toggleLine}
            onAll={toggleAllLines}
          />
          <div className="chart-zoom">
            {entryFocus && (
              <button
                type="button"
                className="btn small"
                aria-pressed={fitPanesToWindow}
                title="压缩主图和副图，尽量在当前窗口内一起展示"
                onClick={() => setFitPanesToWindow((fit) => !fit)}
              >
                {fitPanesToWindow ? "恢复大图" : "适配一屏"}
              </button>
            )}
            <button
              type="button"
              className="btn small"
              disabled={!bars.length}
              title="放大图表右侧最近的K线"
              onClick={() => {
                const visible = period === "D" ? 35 : period === "W" ? 24 : 18;
                zoomTo(
                  Math.max(
                    0,
                    ((bars.length - visible) / Math.max(1, bars.length - 1)) *
                      100,
                  ),
                  100,
                );
              }}
            >
              聚焦近期
            </button>
            <button
              className="icon-btn"
              title="缩小"
              onClick={() => zoomBy(15)}
            >
              <Minus size={14} />
            </button>
            <button
              className="icon-btn"
              title="放大"
              onClick={() => zoomBy(-15)}
            >
              <Plus size={14} />
            </button>
            <button
              className="icon-btn"
              title="恢复默认可读范围"
              onClick={() => zoomTo(readableWindow.start, readableWindow.end)}
            >
              <RotateCcw size={13} />
            </button>
            <button
              className="icon-btn"
              title="显示全部"
              onClick={() => zoomTo(0, 100)}
            >
              <Maximize2 size={13} />
            </button>
          </div>
        </div>
        {automaticTrendlines.available && (
          <div
            className="chart-auto-trend-key"
            tabIndex={0}
            aria-label="自动趋势线图例"
          >
            <strong className="chart-auto-trend-heading">当前价位</strong>
            {showAutomaticTrendlines &&
              automaticTrendlines.lines
                .filter((line) => trendLayers[line.scope])
                .map((line) => (
                  <button
                    type="button"
                    key={line.id}
                    className="chart-auto-trend-key-item"
                    aria-pressed={!hiddenTrendItems.has(line.id)}
                    onClick={() => toggleTrendItem(line.id)}
                    title={`点击显示或隐藏 · ${line.endDate} · ${line.kind === "support" ? "观察回踩能否守住" : "观察反弹能否突破"}`}
                  >
                    <span
                      aria-hidden="true"
                      style={{
                        width: 18,
                        borderTop: `2px ${line.stroke} ${line.color}`,
                      }}
                    />
                    {line.label}
                    <strong>{num(line.endPrice)}</strong>
                  </button>
                ))}
            {showAutomaticTrendlines &&
              trendLayers.zones &&
              automaticTrendlines.zones.map((zone) => (
                <button
                  type="button"
                  key={zone.id}
                  className="chart-auto-trend-key-item"
                  aria-pressed={!hiddenTrendItems.has(zone.id)}
                  onClick={() => toggleTrendItem(zone.id)}
                  title={`点击显示或隐藏 · ${zone.touches} 次拐点反应`}
                >
                  <span
                    aria-hidden="true"
                    className="chart-auto-trend-zone-swatch"
                    style={{
                      borderColor: zone.color,
                      backgroundColor: `${zone.color}40`,
                    }}
                  />
                  {zone.label}{" "}
                  <strong>
                    {num(zone.low)}–{num(zone.high)}
                  </strong>
                </button>
              ))}
            {(!showAutomaticTrendlines ||
              (!automaticTrendlines.lines.some(
                (line) => trendLayers[line.scope],
              ) &&
                (!trendLayers.zones || !automaticTrendlines.zones.length))) && (
              <span
                className="chart-auto-trend-empty"
                title={automaticTrendlines.reason}
              >
                {!showAutomaticTrendlines
                  ? "自动趋势线已关闭"
                  : automaticTrendlines.lines.length ||
                      automaticTrendlines.zones.length
                    ? "线与水平区已隐藏"
                    : "暂无可核验线"}
              </span>
            )}
          </div>
        )}
        {(automaticTrendlines.events.length > 0 || waves) && (
          <div className="chart-analysis-controls">
            {automaticTrendlines.events.length > 0 && (
              <div
                className="chart-auto-trend-events"
                aria-label="穿线记录"
                aria-hidden={!showAutomaticTrendlines || !trendLayers.events}
              >
                <span className="chart-auto-trend-explainer">
                  <strong>穿线记录</strong>
                  <span>记录价格曾跌破支撑线或越过压力线。</span>
                  <span>编号对应图上标记，点记录可看当时的线。</span>
                </span>
                <ChartToggle
                  label="显示全部旧线"
                  checked={showAllTrendHistory}
                  onChange={(value) => {
                    setShowAllTrendHistoryOverride(value);
                    setSelectedTrendEvent(null);
                  }}
                />
                {automaticTrendlines.events.map((event, index) => (
                  <button
                    type="button"
                    className="chart-auto-trend-event"
                    key={`${event.date}:${event.line.scope}:${event.line.kind}`}
                    aria-pressed={selectedTrendEvent === index}
                    style={
                      selectedTrendEvent === index
                        ? { borderColor: palette.trendFocus }
                        : undefined
                    }
                    onClick={() =>
                      setSelectedTrendEvent(
                        selectedTrendEvent === index ? null : index,
                      )
                    }
                  >
                    <strong
                      style={
                        selectedTrendEvent === index
                          ? { color: palette.trendFocus }
                          : undefined
                      }
                    >
                      {index + 1} · {event.label}
                    </strong>
                    <span>
                      {event.date.slice(5)} · 原{event.line.label}{" "}
                      {num(event.price)}
                      {event.confirmedOn &&
                        ` · ${event.confirmedOn.slice(5)} 确认`}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {waves && (
              <div className="chart-wave-selectors">
                <label>
                  波浪结构
                  <ChartSelect
                    label="波浪结构显示范围"
                    value={showWaves ? waveMode : "off"}
                    onChange={(value) => {
                      if (value === "off") {
                        setShowWaves(false);
                      } else {
                        setWaveMode(value as WaveMode);
                        if (!showWaves) setShowWaves(true);
                      }
                    }}
                    options={[
                      { value: "off", label: "关闭" },
                      { value: "latest", label: "最新结构" },
                      { value: "turning", label: "仅转折" },
                    ]}
                  />
                </label>
                {showWaves && waveMode !== "turning" && (
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
                          label: candidates.length
                            ? "最新已识别结构"
                            : "尚未识别",
                        },
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
                {showWaves && waveMode !== "turning" && (
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
                {showWaves && (
                  <span className="chart-wave-hint">
                    {waveMode === "turning"
                      ? "已确认高低点 · 不编号"
                      : "推进实线 · ABC虚线 · 每个结构独立连线"}
                  </span>
                )}
              </div>
            )}
          </div>
        )}
        <ChartAnnotationPanel
          state={annotations}
          displayedIds={
            new Set(
              visibleLineLabels
                .filter((line) => line.group === "annotation")
                .map((line) => line.id.slice("annotation:".length)),
            )
          }
          onDisplayChange={(id, checked) =>
            toggleLine(`annotation:${id}`, checked)
          }
          onDisplayAllChange={(show) => {
            setHiddenLineState((old) => {
              const ids = new Set(old.scope === lineScope ? old.ids : []);
              allLineLabels
                .filter((line) => line.group === "annotation")
                .forEach((line) =>
                  show ? ids.delete(line.id) : ids.add(line.id),
                );
              return { scope: lineScope, ids };
            });
            annotations.setShow(show);
          }}
        />
        {noonMode && noonPreview && (
          <div
            className="ohlc-strip noon-chart-summary"
            role="region"
            aria-label={noonPreview.slot_label ? "盘中快照" : "午间快照"}
          >
            <strong>
              {noonPreview.trade_date} {noonPreview.slot_label || "午间快照"}
            </strong>
            {noonPreview.quote && (
              <span>
                {noonPreview.slot_label ? "盘中" : "午间"}
                {isIndex ? "点位" : "价"}{" "}
                <b>{num(noonPreview.quote.latest_price)}</b>
              </span>
            )}
            {noonCollectionTime(noonPreview) && (
              <span>采集完成 {noonCollectionTime(noonPreview)}</span>
            )}
            <span>
              {period !== "D"
                ? "周K、月K显示观察日前已发布的行情"
                : noonBar
                  ? "末根日 K 为快照临时柱，趋势线基于已发布日 K"
                  : noonPreview.reason ||
                    "前收或价格口径未能核对，快照报价单独显示"}
            </span>
          </div>
        )}
        <div
          className="candle-hover-summary"
          role="region"
          aria-label="当前K线行情与数据说明"
          tabIndex={0}
          style={{
            height: bars.length
              ? chartHoverSummaryHeight(plotWidth, widePlot)
              : undefined,
          }}
        >
          {activeBar && (
            <div className="ohlc-strip">
              <span>
                {activeBar.date}
                {activeBar.noon_provisional
                  ? activeBar.intraday_provisional
                    ? " · 盘中未收盘"
                    : " · 午间未收盘"
                  : ""}
              </span>
              {!activeBar.noon_provisional && (
                <span title={activeSessionDistance.reason}>
                  {activeSessionDistance.label}
                </span>
              )}
              <span>
                开 <b>{num(activeBar.open)}</b>
              </span>
              <span>
                高 <b>{num(activeBar.high)}</b>{" "}
                <small
                  className={tone(activeExtremes.high.value)}
                  title={activeExtremes.high.reason}
                >
                  （最高 {percent(activeExtremes.high.value)}
                  {activeExtremes.high.value == null ? " · 待核验" : ""}）
                </small>
              </span>
              <span>
                低 <b>{num(activeBar.low)}</b>{" "}
                <small
                  className={tone(activeExtremes.low.value)}
                  title={activeExtremes.low.reason}
                >
                  （最低 {percent(activeExtremes.low.value)}
                  {activeExtremes.low.value == null ? " · 待核验" : ""}）
                </small>
              </span>
              <span>
                {activeBar.noon_provisional
                  ? activeBar.intraday_provisional
                    ? "盘中价"
                    : "午间价"
                  : "收"}{" "}
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
              {!activeBar.noon_provisional && (
                <span title={activeSinceReturn.reason}>
                  至{activeSinceReturn.end || "观察日"}涨跌幅{" "}
                  <b className={tone(activeSinceReturn.value)}>
                    {percent(activeSinceReturn.value)}
                  </b>
                  {activeSinceReturn.value == null && (
                    <small className="muted">（待核验）</small>
                  )}
                </span>
              )}
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
              {supportsLimits && !activeBar.noon_provisional && (
                <LimitStatus bar={activeBar} period={period} />
              )}
              {supportsLimits && (
                <PatternStatus bar={activeBar} period={period} />
              )}
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
        </div>
        {!frozenMode && result.loading ? (
          <div
            style={controlledPeriod ? { minHeight: actualHeight } : undefined}
          >
            <Loading />
          </div>
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
            ref={plotRoot}
            className={`candle-plot${annotations.drawing ? " drawing-line" : ""}`}
          >
            <EChart
              key={entryFocus ? entryCanvasKey : undefined}
              option={option}
              height={actualHeight}
              onReady={(c) => {
                instance.current = c;
                setRailChart(c);
                if (entryFocus) {
                  setEntryCanvasReadyKey(entryCanvasKey);
                  if (entryStage.current) {
                    entryStage.current.dataset.zoomStart = String(
                      zoomWindow.current.start,
                    );
                    entryStage.current.dataset.zoomEnd = String(
                      zoomWindow.current.end,
                    );
                  }
                }
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
                  userZoomed.current = true;
                  const current = (c.getOption().dataZoom as any[])?.[0];
                  if (current)
                    zoomWindow.current = {
                      start: current.start ?? 0,
                      end: current.end ?? 100,
                    };
                  if (current)
                    c.setOption(
                      {
                        yAxis: [
                          candlePriceAxis(
                            latestBars.current,
                            zoomWindow.current,
                          ),
                        ],
                      },
                      { silent: true },
                    );
                  if (entryFocus && entryStage.current && current) {
                    entryStage.current.dataset.zoomStart = String(
                      zoomWindow.current.start,
                    );
                    entryStage.current.dataset.zoomEnd = String(
                      zoomWindow.current.end,
                    );
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
            <ChartLineRail
              key={lineScope}
              chart={railChart}
              lines={visibleLineLabels}
              top={24}
              height={mainHeight}
              width={lineRailWidth}
              onMore={() => setLineControlsOpen(true)}
            />
            {chosen.map((key, i) => (
              <div
                key={key}
                className={`candle-pane-value${key === "boll" ? " candle-pane-boll" : ""}`}
                style={{
                  top: paneTops[i] - 18,
                  right: plotRight,
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
            {entryWindow.label}
            {readableWindow.widthAdapted
              ? ` · 依绘图区宽度初始收窄至${readableWindow.visibleBars}根`
              : ""}{" "}
            ·{" "}
            {entryFocus
              ? "图内滚轮 / 双指缩放 · 图外滚动页面"
              : "滚轮 / 双指缩放"}
            · 拖拽平移 · 联动十字线
          </span>
        </div>
      </section>
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
                  : `${view.converted ? "同图前复权" : "同图未复权"} ${num(view.price)} 元 · ${visibleLineIds.has(`history:${view.event.key}:${view.event.date}`) ? "事件日与横线已显示" : "标记已隐藏"}`}
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
              ·{" "}
              {visibleLineLabels.some((line) => line.group === "baseline")
                ? "横线已显示"
                : "横线已隐藏"}
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
              <span className="chart-limit-reference">
                ○ 跌? · {LIMIT_DOWN_REFERENCE_LABEL}
              </span>
              <span className="chart-broken-legend">◆ 炸板</span>
              <span className="up">▼ 回封</span>
              <span>同日形态合并标注：地天 / 一字涨 / 一字跌</span>
              <span>{showLimits ? "标记已显示" : "标记已隐藏"}</span>
            </>
          ) : (
            <span>
              {cycleLabel}K仅汇总{cycleLabel}内具体交易日事件，不将整根
              {cycleLabel}K称为涨跌停。
            </span>
          )}
          <span>{limitCoverageText(data?.limit_event_coverage)}</span>
          <span>
            涨跌停按收盘池核验；炸板、回封按同日来源证据另行标记，盘中状态不代表收盘。
          </span>
          <span>
            形态只用原始日K与来源证据；无标记不等于未涨跌停或没有形态。
          </span>
        </div>
      )}
      {data && (
        <div className="source-line">
          {data.price_basis === "none"
            ? "未复权"
            : ["forward", "forward_event_affine"].includes(data.price_basis)
              ? "本地前复权"
              : data.price_basis || "价格口径待核验"}{" "}
          · {completedBars.length} 根{cycleLabel}K
          {noonBar ? " + 1 根午间临时柱" : ""} ·{" "}
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
            <p>尚未识别五浪候选。切换“仅转折”可查看已确认高低点。</p>
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
              同类图表共用显示偏好，修改即时生效并自动保存。周期和缩放范围由各张图独立设置。
            </p>
            <fieldset>
              <legend>均线</legend>
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
              <legend>副图指标</legend>
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
                可关闭全部副图。BOLL 按当前周期收盘价计算，参数为 20 · 2。
              </p>
            </fieldset>
            <fieldset>
              <legend>趋势与波浪</legend>
              <div className="chart-preferences-mas">
                <ChartToggle
                  label="默认显示自动趋势线"
                  checked={
                    isIndex
                      ? preferences.indexShowAutomaticTrendlines
                      : preferences.stockShowAutomaticTrendlines
                  }
                  onChange={(value) =>
                    update(
                      isIndex
                        ? { indexShowAutomaticTrendlines: value }
                        : { stockShowAutomaticTrendlines: value },
                    )
                  }
                />
                <ChartToggle
                  label="默认显示全部穿线旧线"
                  checked={defaultShowAllTrendHistory}
                  onChange={(value) =>
                    update(
                      isIndex
                        ? { indexShowAllTrendHistory: value }
                        : { stockShowAllTrendHistory: value },
                    )
                  }
                />
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
                      { value: "turning", label: "仅转折" },
                    ]}
                  />
                </label>
              </div>
              <p className="muted">
                在单张图的工具栏可临时切换自动趋势线。波浪结构的具体日期由各张图选择。
              </p>
            </fieldset>
            <fieldset>
              <legend>个股价格与标记</legend>
              <div className="chart-preferences-mas">
                <label>
                  个股默认价格口径
                  <ChartSelect
                    label="全局个股复权口径"
                    value={preferences.stockPriceBasis}
                    onChange={(value) =>
                      update({
                        stockPriceBasis:
                          value === "forward" ? "forward" : "none",
                      })
                    }
                    options={[
                      { value: "none", label: "未复权" },
                      { value: "forward", label: "本地前复权" },
                    ]}
                  />
                </label>
                <ChartToggle
                  label="默认显示个股日K涨跌停及日形态标记"
                  checked={showLimits}
                  onChange={(value) => update({ showLimits: value })}
                />
                <ChartToggle
                  label="默认显示跟踪图事件基准线"
                  checked={showEventBaseline}
                  color="#c79136"
                  onChange={(value) => update({ showEventBaseline: value })}
                />
              </div>
              <p className="muted">
                指数使用原始点位。个股的本地前复权证据不足时，图表显示原始价格。
              </p>
            </fieldset>
            <p className="muted">这些设置只影响图表显示。</p>
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
  noonSnapshotId,
  intradaySnapshotId,
  onClose,
  onResearch,
  eventBaseline,
  navigation,
  onStep,
}: {
  id: string;
  asOf?: string;
  noonSnapshotId?: string;
  intradaySnapshotId?: string;
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
  const [entryHeaderBoard, setEntryHeaderBoard] = useState<{
    row: Row;
    date: string;
  } | null>(null);
  const [periodSelection, setPeriodSelection] = useState<{
    id: string;
    value: ChartPeriod;
  } | null>(null);
  const drawerPeriod = periodSelection?.id === id ? periodSelection.value : "D";
  const setDrawerPeriod = (value: ChartPeriod) =>
    setPeriodSelection({ id, value });
  useEffect(() => {
    setPeriodSelection((previous) => (previous?.id === id ? previous : null));
  }, [id]);
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const capturedFocusRef = useRef(false);
  const currentRequest = useRef({ id, asOf });
  currentRequest.current = { id, asOf };
  const detail = useApi<Row>(
    `/instruments/${encodeURIComponent(id)}${asOf ? "?as_of=" + encodeURIComponent(asOf) : ""}`,
  );
  const noon = useApi<NoonChartPreview>(
    intradaySnapshotId
      ? `/market/intraday/quotes/${encodeURIComponent(intradaySnapshotId)}/${encodeURIComponent(id)}`
      : noonSnapshotId
        ? `/market/noon/${encodeURIComponent(noonSnapshotId)}/stocks/${encodeURIComponent(id)}/quote`
        : null,
  );
  const selectedSnapshot = intradaySnapshotId || noonSnapshotId;
  const snapshotLabel = intradaySnapshotId ? "盘中" : "午间";
  const noonPreview =
    noon.data &&
    noon.data.snapshot_id === selectedSnapshot &&
    (!noon.data.quote || noon.data.quote.instrument_id === id)
      ? noon.data
      : undefined;
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
  const headerSummary =
    entryHeaderBoard &&
    entryHeaderBoard.row.instrument_id === id.toUpperCase() &&
    entryHeaderBoard.date === (asOf || detailData?.data_date) &&
    entryHeaderBoard.row.snapshot_id === detailData?.snapshot_id
      ? entryHeaderBoard.row
      : null;
  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <aside
        ref={drawerRef}
        className="stock-drawer single-page-stock-frame"
        role="dialog"
        aria-modal="true"
        aria-label="个股行情详情"
      >
        <div className="entry-page-bar">
          <div className="entry-header-identity">
            <strong>
              {meta.name || position?.current.name || id}{" "}
              <CopyStockCode
                code={meta.code || position?.current.code || id.slice(0, 6)}
                instrumentId={id}
              />
            </strong>
            <div className="entry-header-board" aria-label="观察日板况">
              {headerSummary && (
                <BoardTags
                  row={headerSummary}
                  date={entryHeaderBoard!.date}
                  snapshotId={headerSummary.snapshot_id}
                  height
                  boardText
                  typeCodes={[
                    "broken_board",
                    "broken_pool_source",
                    "rebound_board",
                  ]}
                  volume={false}
                  feedback={false}
                />
              )}
              <PatternStatus bar={stockPatternHeader(detailData, asOf)} />
            </div>
            <div
              className="segmented entry-header-period"
              role="group"
              aria-label="K线周期"
            >
              {(["D", "W", "M"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={drawerPeriod === value ? "active" : ""}
                  aria-pressed={drawerPeriod === value}
                  onClick={() => setDrawerPeriod(value)}
                >
                  {value === "D" ? "日 K" : value === "W" ? "周 K" : "月 K"}
                </button>
              ))}
            </div>
          </div>
          {selectedSnapshot ? (
            <div className="entry-header-metrics">
              <div className="entry-header-metric">
                <span>{snapshotLabel}价</span>
                <b>
                  {noonPreview?.quote
                    ? `${num(noonPreview.quote.latest_price)} 元`
                    : "—"}
                </b>
              </div>
              <div className="entry-header-metric">
                <span>{snapshotLabel}涨幅</span>
                <b className={tone(noonPreview?.quote?.change_pct)}>
                  {percent(noonPreview?.quote?.change_pct)}
                </b>
              </div>
            </div>
          ) : (
            <StockCompactMetrics
              summary={headerSummary}
              detail={detailData}
              instrumentId={id}
              asOf={asOf || detailData?.data_date}
              isIndex={meta.kind === "index"}
            />
          )}
          <div className="entry-header-actions">
            <div
              className="stock-navigation-controls entry-header-navigation"
              role="group"
              aria-label="按当前结果浏览股票"
              title={
                position
                  ? "按打开时的筛选与排序浏览全部已加载结果"
                  : "从榜单、策略、跟踪、自选或成员列表打开后可按原顺序浏览"
              }
            >
              <button
                className="btn small"
                disabled={!position?.previousId || !onStep}
                onClick={() => onStep?.(-1)}
                aria-label="上一只股票"
              >
                <ChevronLeft size={15} />
                <span>上一只</span>
              </button>
              {position && (
                <output aria-live="polite" aria-atomic="true">
                  {position.position} / {position.total}
                </output>
              )}
              <button
                className="btn small"
                disabled={!position?.nextId || !onStep}
                onClick={() => onStep?.(1)}
                aria-label="下一只股票"
              >
                <span>下一只</span>
                <ChevronRight size={15} />
              </button>
            </div>
            <button
              className="link"
              aria-label="查看顶部资料"
              onClick={() =>
                drawerRef.current
                  ?.querySelector(".entry-page-scroll")
                  ?.scrollTo({ top: 0, behavior: "smooth" })
              }
            >
              <span className="entry-action-text">↑ 顶部资料</span>
              <span className="entry-action-short" aria-hidden="true">
                ↑ 资料
              </span>
            </button>
            <button
              className="link"
              aria-label="定位图表"
              onClick={() =>
                drawerRef.current
                  ?.querySelector(".entry-single-stage")
                  ?.scrollIntoView({ block: "start", behavior: "smooth" })
              }
            >
              <span className="entry-action-text">定位图表</span>
              <span className="entry-action-short" aria-hidden="true">
                图表
              </span>
            </button>
            <button
              className="icon-btn"
              aria-label="关闭当前详情"
              onClick={onClose}
            >
              <X size={19} />
            </button>
          </div>
        </div>
        <div
          className="entry-page-scroll"
          data-detail-loading={detail.loading || eventReference.loading}
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
            {selectedSnapshot && !noonPreview && (
              <p className="muted">
                {noon.loading
                  ? `正在读取${snapshotLabel}快照…`
                  : `${snapshotLabel}快照暂不可用。`}
              </p>
            )}
            <CandleChart
              noonPreview={noonPreview}
              noonMode={Boolean(selectedSnapshot)}
              key={id}
              instrumentId={id}
              instrumentKind={meta.kind}
              asOf={asOf}
              height={660}
              entryFocus
              controlledPeriod={drawerPeriod}
              onPeriodChange={setDrawerPeriod}
              onBoardPresentation={setEntryHeaderBoard}
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
            {["stock", "equity", "a_share", "A"].includes(
              meta.kind || meta.asset_type,
            ) &&
              typeof data?.as_of === "string" &&
              !!data.bars?.length && (
                <StockChipDistribution
                  instrumentId={id.toUpperCase()}
                  date={data.as_of}
                />
              )}
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
