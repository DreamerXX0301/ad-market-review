import { useEffect, useRef, useState } from "react";
import {
  Activity,
  BellRing,
  CheckCheck,
  RefreshCw,
  Save,
  Search,
} from "lucide-react";
import { api, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Panel,
  Spinner,
  Stat,
  Table,
  Warning,
} from "../../components/UI";
import { num, percent, tone } from "../../lib/format";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import IntradayMonitorHeat from "./IntradayMonitorHeat";
import type { HeatGroup } from "./monitorHeat";
import "./IntradayMonitor.css";

const PATH = "/intraday-monitor";
const originLabels: Record<string, string> = {
  tracking: "策略跟踪",
  watchlist: "自选",
  manual_lines: "手动画线",
  gainers_30d: "30日涨幅榜前1000",
};
const scopeKeys = Object.keys(originLabels);
const boardLabels: Record<string, string> = {
  main: "主板",
  chinext: "创业板",
  star: "科创板",
  beijing: "北交所",
};
const monitorBoard = (row: Row) =>
  row.board === "bse" ? "beijing" : row.board;
const metricLabels: Record<string, string> = {
  change_pct: "今日涨幅（盘中）",
  change_30d_pct: "近30交易日涨幅",
  return_ytd_pct: "年内涨幅",
  limit_up_30d: "近30交易日涨停次数",
  limit_up_1y: "近一年涨停次数",
  limit_up_ytd: "年内涨停次数",
  sessions_since_limit_up: "距最近涨停交易日",
  last_limit_up_date: "最近确认涨停日期",
};
export type MonitorListFilters = {
  topicQuery: string;
  topicId?: string;
  topicName?: string;
  topicScope?: { topic_id: string; name: string }[];
  board: string;
  metric: string;
  min: string;
  max: string;
  unknown: string;
};
const emptyFilters: MonitorListFilters = {
  topicQuery: "",
  topicId: "",
  topicName: "",
  topicScope: [],
  board: "all",
  metric: "change_pct",
  min: "",
  max: "",
  unknown: "range",
};
const statusLabels: Record<string, string> = {
  disabled: "未开启",
  waiting: "等待采样",
  ready: "监控就绪",
  delayed: "采样延迟",
  blocked: "暂不可采样",
};
const batchLabels: Record<string, string> = {
  queued: "排队中",
  running: "采集中",
  succeeded: "已完成",
  completed: "已完成",
  partial: "部分完成",
  failed: "未完成",
  skipped: "已跳过",
};

export function monitorSettingsValue(value?: Row | null): Row | null {
  return value &&
    typeof value.enabled === "boolean" &&
    Number.isInteger(value.revision) &&
    value.revision >= 0 &&
    value.interval_minutes === 10 &&
    value.scopes &&
    scopeKeys.every((key) => typeof value.scopes[key] === "boolean") &&
    typeof value.manual_enabled === "boolean" &&
    typeof value.automatic_enabled === "boolean"
    ? { ...value, scopes: { ...value.scopes } }
    : null;
}

export function confirmedMonitorSettings(
  request: Row,
  response?: Row | null,
): Row | null {
  const value = monitorSettingsValue(response);
  return value &&
    value.revision >= request.expected_revision &&
    ["enabled", "manual_enabled", "automatic_enabled"].every(
      (key) => value[key] === request[key],
    ) &&
    scopeKeys.every((key) => value.scopes[key] === request.scopes[key])
    ? value
    : null;
}

export function monitorTime(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "—";
}

export function filterMonitorEvents(
  rows: Row[],
  query: string,
  phase: string,
  filters: Partial<MonitorListFilters> = {},
  topicDate?: string,
): Row[] {
  return filterMonitorRows(rows, query, filters, topicDate).filter(
    (row) => phase === "all" || row.phase === phase,
  );
}

function validDay(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const stamp = Date.parse(`${value}T00:00:00Z`);
  return (
    Number.isFinite(stamp) &&
    new Date(stamp).toISOString().slice(0, 10) === value
  );
}

export function currentMonitorTopics(row: Row, topicDate?: string): Row[] {
  return validDay(topicDate) &&
    row.topic_date === topicDate &&
    Array.isArray(row.current_topics)
    ? row.current_topics.filter(
        (topic: Row) =>
          topic &&
          typeof topic.name === "string" &&
          typeof topic.topic_id === "string",
      )
    : [];
}

export function monitorFilterIssue(
  filters: Partial<MonitorListFilters>,
): string {
  if (filters.unknown === "only") return "";
  const lower = filters.min?.trim() || "",
    upper = filters.max?.trim() || "";
  if (!lower && !upper) return "";
  const date = filters.metric === "last_limit_up_date";
  if (
    [lower, upper].some(
      (value) =>
        value && (date ? !validDay(value) : !Number.isFinite(Number(value))),
    )
  )
    return date ? "请输入有效的涨停日期范围。" : "请输入有效的指标范围。";
  if (lower && upper && (date ? lower > upper : Number(lower) > Number(upper)))
    return "最小值不能高于最大值。";
  return "";
}

export function filterMonitorRows(
  rows: Row[],
  query: string,
  supplied: Partial<MonitorListFilters> = {},
  topicDate?: string,
): Row[] {
  const filters = { ...emptyFilters, ...supplied };
  if (
    monitorFilterIssue(filters) ||
    !Object.hasOwn(metricLabels, filters.metric)
  )
    return [];
  const text = query.normalize("NFKC").trim().toLowerCase();
  const phonetic = text
    .replaceAll("u:", "v")
    .replaceAll("ü", "v")
    .replace(/[\s'’\-]/g, "");
  const usePhonetic = /^[a-z]{2,80}$/.test(phonetic);
  const topicText = filters.topicQuery.trim().toLowerCase();
  const lower = filters.min.trim(),
    upper = filters.max.trim();
  const bounded = !!(lower || upper);
  return rows.filter((row) => {
    const board = monitorBoard(row);
    const wantedBoard = filters.board === "bse" ? "beijing" : filters.board;
    if (
      wantedBoard === "unknown"
        ? Object.hasOwn(boardLabels, board)
        : wantedBoard !== "all" && board !== wantedBoard
    )
      return false;
    if (
      filters.topicId &&
      !currentMonitorTopics(row, topicDate).some(
        (topic) => topic.topic_id === filters.topicId,
      )
    )
      return false;
    if (
      filters.topicScope?.some(
        (selected) =>
          !currentMonitorTopics(row, topicDate).some(
            (topic) => topic.topic_id === selected.topic_id,
          ),
      )
    )
      return false;
    if (
      topicText &&
      !currentMonitorTopics(row, topicDate).some((topic) =>
        `${topic.name} ${topic.topic_id}`.toLowerCase().includes(topicText),
      )
    )
      return false;
    const value = row[filters.metric];
    const date = filters.metric === "last_limit_up_date";
    const known = date
      ? validDay(value)
      : typeof value === "number" && Number.isFinite(value);
    if (filters.unknown === "only" && known) return false;
    if (
      !known &&
      filters.unknown !== "only" &&
      bounded &&
      filters.unknown !== "include"
    )
      return false;
    if (
      known &&
      filters.unknown !== "only" &&
      ((lower && (date ? value < lower : value < Number(lower))) ||
        (upper && (date ? value > upper : value > Number(upper))))
    )
      return false;
    const searchableText = [
      row.name,
      row.code,
      row.instrument_id,
      row.title,
      ...(Array.isArray(row.sources)
        ? row.sources.map((source: Row) => source.label)
        : []),
    ]
      .join(" ")
      .normalize("NFKC")
      .toLowerCase();
    return (
      searchableText.includes(text) ||
      (usePhonetic &&
        [row.search_pinyin, row.search_initials].some(
          (key) => typeof key === "string" && key.startsWith(phonetic),
        ))
    );
  });
}

function CurrentTopics({
  row,
  topicDate,
  onDetail,
}: {
  row: Row;
  topicDate?: string;
  onDetail: () => void;
}) {
  const topics = currentMonitorTopics(row, topicDate);
  return (
    <div className="intraday-monitor-topics">
      <div
        className="tags"
        title={topics.map((topic) => topic.name).join("、")}
      >
        {topics.slice(0, 2).map((topic) => (
          <Badge key={topic.topic_id}>{topic.name}</Badge>
        ))}
      </div>
      <div className="intraday-monitor-meta-line">
        <small>
          {topics.length
            ? `归属 ${validDay(row.topic_date) ? row.topic_date : "—"}`
            : "当前归属待核验"}
        </small>
        {topics.length > 0 && (
          <button className="intraday-monitor-detail-link" onClick={onDetail}>
            全部 {topics.length} 项
          </button>
        )}
      </div>
    </div>
  );
}

function MetricFacts({ row, onDetail }: { row: Row; onDetail: () => void }) {
  return (
    <div className="intraday-monitor-metrics">
      <span>
        <span className="muted">30日</span>{" "}
        <b className={tone(row.change_30d_pct)}>
          {percent(row.change_30d_pct)}
        </b>
        <span className="intraday-monitor-separator">·</span>
        <span className="muted">年内</span>{" "}
        <b className={tone(row.return_ytd_pct)}>
          {percent(row.return_ytd_pct)}
        </b>
      </span>
      <span className="intraday-monitor-meta-line">
        最近涨停{" "}
        <b
          title={
            validDay(row.last_limit_up_date)
              ? row.last_limit_up_date
              : "最近涨停日期未知"
          }
        >
          {validDay(row.last_limit_up_date)
            ? row.last_limit_up_date.slice(5)
            : "—"}
        </b>
        <span className="intraday-monitor-separator">·</span>
        年内 {num(row.limit_up_ytd, 0)} 次
        <button className="intraday-monitor-detail-link" onClick={onDetail}>
          详情
        </button>
      </span>
    </div>
  );
}

function sourceName(source: Row) {
  return source.source_kind === "manual" ||
    source.source_kind === "manual_line" ||
    source.source_kind === "manual_lines"
    ? "手动画线"
    : ["automatic", "automatic_zone", "automatic_level"].includes(
          source.source_kind,
        )
      ? "默认价位"
      : "价位依据";
}

function Evidence({ event }: { event: Row }) {
  const sources: Row[] = Array.isArray(event.sources) ? event.sources : [];
  return (
    <div className="intraday-monitor-evidence">
      <p>
        <strong>{event.title || "盘中提醒"}</strong> ·{" "}
        {event.phase === "holding" ? "再次采样仍满足" : "首次观察"}
      </p>
      <p>
        采样价 {num(event.latest_price, 4)} · 盘中涨幅{" "}
        {percent(event.change_pct)}
      </p>
      <p>
        首次观察 {monitorTime(event.first_seen_at_ms)} · 再次采样{" "}
        {monitorTime(event.confirmed_at_ms)} · 最近观察{" "}
        {monitorTime(event.last_seen_at_ms)}
      </p>
      {sources.length ? (
        sources.map((source, index) => (
          <section key={`${source.rule_key || "source"}-${index}`}>
            <strong>
              <Badge>{sourceName(source)}</Badge>{" "}
              {source.label || "价位名称待核验"}
            </strong>
            <span>
              参考区间 {num(source.lower, 4)} ～ {num(source.upper, 4)}
            </span>
            <span>观察时间 {monitorTime(source.observed_at_ms)}</span>
            {source.confirmed_at_ms && (
              <span>再次满足时间 {monitorTime(source.confirmed_at_ms)}</span>
            )}
            <span>
              来源批次时间 {monitorTime(source.source_time_ms)} ·
              单股成交时间未知
            </span>
            {typeof source.evidence?.reason === "string" && (
              <p>{source.evidence.reason}</p>
            )}
          </section>
        ))
      ) : (
        <p>本条提醒的价位依据尚未完整返回，请重新读取。</p>
      )}
    </div>
  );
}

type MonitorDetail = {
  kind: "topics" | "metrics" | "evidence" | "state";
  row: Row;
};
const detailLabels = {
  topics: "当前行业与概念",
  metrics: "收益与涨停统计",
  evidence: "提醒依据与时间",
  state: "采样状态与说明",
};
function DetailContent({
  detail,
  topicDate,
}: {
  detail: MonitorDetail;
  topicDate?: string;
}) {
  const row = detail.row;
  if (detail.kind === "evidence") return <Evidence event={row} />;
  if (detail.kind === "topics")
    return (
      <>
        <p>
          当前归属日 {validDay(row.topic_date) ? row.topic_date : "待核验"}
          ，不代表提醒发生时的历史归属。
        </p>
        <div className="tags">
          {currentMonitorTopics(row, topicDate).map((topic) => (
            <Badge key={topic.topic_id}>{topic.name}</Badge>
          ))}
        </div>
        {!currentMonitorTopics(row, topicDate).length && (
          <p>当前归属待核验。</p>
        )}
      </>
    );
  if (detail.kind === "state")
    return (
      <>
        <p>{row.reason || "来源没有返回更多状态说明。"}</p>
        <p>最近采样 {monitorTime(row.last_observed_at_ms)}</p>
        <p>来源批次时间 {monitorTime(row.source_time_ms)} · 单股成交时间未知</p>
      </>
    );
  const explanations = Object.values(
    row.reasons || row.metrics_reasons || {},
  ).filter((reason): reason is string => typeof reason === "string");
  return (
    <>
      <p>
        近30交易日 {percent(row.change_30d_pct)} · 年内涨幅{" "}
        {percent(row.return_ytd_pct)}
      </p>
      <p>
        最近涨停{" "}
        {validDay(row.last_limit_up_date) ? row.last_limit_up_date : "—"} · 距今{" "}
        {num(row.sessions_since_limit_up, 0)} 个交易日
      </p>
      <p>
        涨停次数：近30交易日 {num(row.limit_up_30d, 0)} 次 · 近一年{" "}
        {num(row.limit_up_1y, 0)} 次 · 年内 {num(row.limit_up_ytd, 0)} 次
      </p>
      <p>指标日 {validDay(row.metrics_date) ? row.metrics_date : "待核验"}</p>
      {row.limit_up_1y_complete === false && (
        <p>
          近一年仅确认 {num(row.limit_up_1y_observed, 0)}{" "}
          次，来源未覆盖完整窗口，不能作为全年总数。
        </p>
      )}
      {row.coverage_unknown && <p>部分指标覆盖待核验，缺值保留未知。</p>}
      {explanations.map((reason, index) => (
        <p key={index}>{reason}</p>
      ))}
    </>
  );
}

function TableTime({ value }: { value: unknown }) {
  const full = monitorTime(value);
  const short =
    typeof value === "number" && Number.isFinite(value) && value > 0
      ? new Date(value).toLocaleTimeString("zh-CN", {
          timeZone: "Asia/Shanghai",
          hour12: false,
        })
      : "—";
  return (
    <span className="intraday-monitor-table-time" title={full}>
      {short}
    </span>
  );
}

function initialMonitorView() {
  const value =
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location?.search || "").get("monitor_view");
  return value && ["events", "stocks", "heat", "settings"].includes(value)
    ? value
    : "events";
}

export default function IntradayMonitor({
  onStock,
}: {
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  const active = usePageActive();
  const overview = useApi<Row>(PATH);
  const data = overview.data;
  const day =
    typeof data?.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(data.date)
      ? data.date
      : "";
  const events = useApi<Row>(
    day ? `${PATH}/events?date=${day}&limit=200` : null,
  );
  const [view, setView] = useState(initialMonitorView);
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState("all");
  const [filters, setFilters] = useState<MonitorListFilters>(() => ({
    ...emptyFilters,
  }));
  const [draft, setDraft] = useState<Row | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<MonitorDetail | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!dirty) setDraft(monitorSettingsValue(data?.settings));
  }, [data?.settings, dirty]);
  const reloadOverview = overview.reload;
  const reloadEvents = events.reload;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      reloadOverview();
      reloadEvents();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [active, reloadOverview, reloadEvents]);

  const settings = monitorSettingsValue(data?.settings);
  const settingsChanged = !!(
    draft &&
    settings &&
    draft.revision !== settings.revision
  );
  const eventScopeMatches = !!day && events.data?.date === day;
  const allEvents: Row[] =
    eventScopeMatches && Array.isArray(events.data?.items)
      ? events.data.items
      : [];
  const stocks: Row[] = Array.isArray(data?.stocks) ? data.stocks : [];
  const chartAsOf =
    typeof data?.chart_as_of === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(data.chart_as_of)
      ? data.chart_as_of
      : undefined;
  const visibleEvents = filterMonitorEvents(
    allEvents,
    query,
    phase,
    filters,
    chartAsOf,
  );
  const visibleStocks = filterMonitorRows(stocks, query, filters, chartAsOf);
  const filterIssue = monitorFilterIssue(filters);
  const topicSuggestions = [
    ...new Set(
      (view === "events" ? allEvents : stocks).flatMap((row) =>
        currentMonitorTopics(row, chartAsOf).map((topic) => topic.name),
      ),
    ),
  ]
    .sort()
    .slice(0, 200);
  const openStock = (id: string, orderedRows: Row[]) =>
    onStock(
      id,
      createStockNavigation(
        orderedRows.map(({ instrument_id, name, code }) => ({
          instrument_id,
          name,
          code,
        })),
        {
          source: "market",
          scopeKey: JSON.stringify({
            page: "intraday-monitor",
            day,
            view,
            ids: orderedRows.map((row) => row.event_id || row.instrument_id),
          }),
          label: "盘中监控 · 当前列表 · 已发布日K",
          asOf: chartAsOf,
        },
      ),
    );
  const refresh = () => {
    overview.reload();
    events.reload();
  };
  const change = (key: string, value: boolean) => {
    if (!draft) return;
    setDraft({ ...draft, [key]: value });
    setDirty(true);
    setNotice("");
  };
  async function saveSettings() {
    if (
      !active ||
      !draft ||
      !settings ||
      settingsChanged ||
      inFlight.current ||
      !dirty
    )
      return;
    const request = {
      expected_revision: draft.revision,
      enabled: draft.enabled,
      scopes: { ...draft.scopes },
      manual_enabled: draft.manual_enabled,
      automatic_enabled: draft.automatic_enabled,
    };
    inFlight.current = true;
    setBusy("settings");
    setError("");
    setNotice("");
    try {
      const response = await api<Row>(`${PATH}/settings`, {
        method: "PUT",
        body: JSON.stringify(request),
      });
      if (!mounted.current) return;
      const next = confirmedMonitorSettings(request, response);
      if (!next) throw new Error("尚未确认完整监控设置，请重新读取后核对。");
      overview.setData(data ? { ...data, settings: next } : null);
      setDraft(next);
      setDirty(false);
      setNotice(
        next.enabled
          ? "设置已保存，唯一工作进程将在有效盘中时段检查。"
          : "设置已保存，后续盘中采样已关闭。",
      );
      window.dispatchEvent(new Event("intraday-monitor-changed"));
      overview.reload();
    } catch (problem) {
      if (mounted.current) {
        setError(
          problem instanceof Error ? problem.message : "设置保存未完成。",
        );
        overview.reload();
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy("");
    }
  }
  async function markRead() {
    const identifiers = visibleEvents
      .filter((event) => event.unread === true)
      .map((event) => event.event_id)
      .filter((id) => typeof id === "string")
      .slice(0, 200);
    if (!active || !identifiers.length || inFlight.current) return;
    inFlight.current = true;
    setBusy("read");
    setError("");
    setNotice("");
    try {
      const response = await api<Row>(`${PATH}/events/read`, {
        method: "POST",
        body: JSON.stringify({ event_ids: identifiers }),
      });
      if (!Number.isInteger(response?.updated) || response.updated < 0)
        throw new Error("已读状态尚未确认，请重新读取。");
      if (mounted.current) {
        refresh();
        window.dispatchEvent(new Event("intraday-monitor-changed"));
        setNotice(`已更新 ${response.updated} 条提醒的已读状态。`);
      }
    } catch (problem) {
      if (mounted.current)
        setError(
          problem instanceof Error ? problem.message : "已读状态更新未完成。",
        );
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy("");
    }
  }
  const batch = data?.last_batch;
  const metadataColumns = [
    {
      key: "board",
      title: "板型",
      render: (_: unknown, row: Row) => (
        <Badge>{boardLabels[monitorBoard(row)] || "待核验"}</Badge>
      ),
    },
    {
      key: "current_topics",
      title: "当前行业 / 概念",
      sortable: false,
      render: (_: unknown, row: Row) => (
        <CurrentTopics
          row={row}
          topicDate={chartAsOf}
          onDetail={() => setDetail({ kind: "topics", row })}
        />
      ),
    },
  ];
  const factsColumn = {
    key: "change_30d_pct",
    title: "30日 / 年内 / 涨停",
    render: (_: unknown, row: Row) => (
      <MetricFacts
        row={row}
        onDetail={() => setDetail({ kind: "metrics", row })}
      />
    ),
  };
  const openHeatTopic = (group: HeatGroup) => {
    const scope = [...(filters.topicScope || [])];
    if (
      filters.topicId &&
      filters.topicId !== group.topic_id &&
      !scope.some((topic) => topic.topic_id === filters.topicId)
    )
      scope.push({
        topic_id: filters.topicId,
        name: filters.topicName || filters.topicId,
      });
    setFilters({
      ...filters,
      topicId: group.topic_id,
      topicName: group.name,
      topicScope: scope.filter((topic) => topic.topic_id !== group.topic_id),
    });
    setView("stocks");
  };
  return (
    <div className="page-stack intraday-monitor-page">
      <div className="intraday-monitor-intro">
        <span className="intraday-monitor-icon">
          <Activity size={25} />
        </span>
        <div>
          <h2>
            盘中监控{" "}
            <Badge>{statusLabels[data?.status?.state] || "状态待读取"}</Badge>
          </h2>
          <p>
            跟踪、自选、手动画线与涨幅榜范围合并去重，每 10 分钟检查盘中变化。
          </p>
        </div>
        <span className="muted">北京时间 {day || "待读取"}</span>
      </div>
      {overview.error && (
        <ErrorBox message={overview.error} retry={overview.reload} />
      )}
      {error && <ErrorBox message={error} retry={refresh} />}
      {notice && (
        <p className="intraday-monitor-notice" role="status">
          {notice}
        </p>
      )}
      {overview.loading && !data ? (
        <Loading />
      ) : (
        <>
          <div className="intraday-monitor-stats">
            <Stat
              label="去重监控股票"
              value={num(data?.counts?.stocks, 0)}
              sub={`当前可检查 ${num(data?.counts?.eligible, 0)} · 暂不可检查 ${num(data?.counts?.excluded, 0)} · 上限2000只`}
            />
            <Stat
              label="最近采样"
              value={
                batch ? batchLabels[batch.status] || "状态待核验" : "尚未采样"
              }
              sub={
                batch
                  ? monitorTime(batch.completed_at_ms || batch.started_at_ms)
                  : "开启后在有效时段等待首轮"
              }
            />
            <Stat
              label="最近有效报价"
              value={
                batch
                  ? `${num(batch.valid_count, 0)} / ${num(batch.requested_count, 0)}`
                  : "—"
              }
              sub={
                batch
                  ? `${num(batch.unknown_count, 0)} 只待核验`
                  : "未采样不代表没有触发"
              }
            />
            <Stat
              label="今日未读提醒"
              value={num(
                eventScopeMatches
                  ? events.data?.unread_count
                  : data?.events?.unread_count,
                0,
              )}
              sub="同股同类提醒合并展示多项依据"
            />
          </div>
          <Warning>
            {data?.status?.reason || "监控状态待读取。"}
            {data?.status?.worker_alive === false && " 工作进程当前不在线。"}
          </Warning>
          <Panel
            className="intraday-monitor-panel"
            title="关注股票的盘中反馈"
            subtitle="手动画线与默认价位分别记录依据和观察时间"
            actions={
              <button
                type="button"
                className="btn small"
                onClick={refresh}
                disabled={!!busy}
              >
                <RefreshCw size={14} />
                刷新已保存结果
              </button>
            }
          >
            <div
              className="intraday-monitor-tabs"
              role="group"
              aria-label="盘中监控视图"
            >
              {[
                ["events", "今日提醒"],
                ["stocks", "监控股票"],
                ["heat", "池内热点"],
                ["settings", "采集设置"],
              ].map(([key, title]) => (
                <button
                  type="button"
                  key={key}
                  className={view === key ? "selected" : ""}
                  aria-pressed={view === key}
                  onClick={() => {
                    setView(key);
                    if (
                      key !== "heat" &&
                      !(view === "heat" && key === "stocks")
                    )
                      setQuery("");
                  }}
                >
                  {title}
                </button>
              ))}
            </div>
            {view !== "settings" && (
              <div className="intraday-monitor-toolbar">
                <label className="intraday-monitor-search">
                  <Search size={15} />
                  <input
                    type="search"
                    aria-label="搜索监控股票或提醒"
                    placeholder={
                      view === "events"
                        ? "名称 / 代码 / 拼音 / 首字母 / 提醒依据"
                        : "名称 / 代码 / 拼音 / 首字母"
                    }
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </label>
                <label className="intraday-monitor-search">
                  <Search size={15} />
                  <input
                    type="search"
                    aria-label="搜索行业或概念"
                    placeholder="行业 / 概念名称或ID"
                    list="intraday-monitor-topic-options"
                    value={filters.topicQuery}
                    onChange={(event) =>
                      setFilters({
                        ...filters,
                        topicQuery: event.target.value,
                        topicId: "",
                        topicName: "",
                        topicScope: [],
                      })
                    }
                  />
                  <datalist id="intraday-monitor-topic-options">
                    {topicSuggestions.map((name) => (
                      <option key={name} value={name} />
                    ))}
                  </datalist>
                </label>
                {filters.topicId && (
                  <span className="intraday-monitor-topic-selection">
                    限定分组：
                    {[
                      ...(filters.topicScope || []).map((topic) => topic.name),
                      filters.topicName || filters.topicId,
                    ].join("、")}
                    <button
                      type="button"
                      className="btn small"
                      onClick={() =>
                        setFilters({
                          ...filters,
                          topicId: "",
                          topicName: "",
                          topicScope: [],
                        })
                      }
                    >
                      解除分组
                    </button>
                  </span>
                )}
                {view === "events" && (
                  <>
                    <select
                      aria-label="筛选盘中提醒阶段"
                      value={phase}
                      onChange={(event) => setPhase(event.target.value)}
                    >
                      <option value="all">全部阶段</option>
                      <option value="observed">首次观察</option>
                      <option value="holding">再次采样仍满足</option>
                    </select>
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => void markRead()}
                      disabled={
                        !!busy ||
                        !visibleEvents.some((event) => event.unread === true)
                      }
                    >
                      <CheckCheck size={14} />
                      {busy === "read" ? "正在更新…" : "当前结果标为已读"}
                    </button>
                  </>
                )}
              </div>
            )}
            {view !== "settings" && (
              <div className="intraday-monitor-filter-area">
                <div className="intraday-monitor-filter-controls">
                  <label>
                    板型
                    <select
                      aria-label="筛选股票板型"
                      value={filters.board}
                      onChange={(event) =>
                        setFilters({ ...filters, board: event.target.value })
                      }
                    >
                      <option value="all">全部板型</option>
                      {Object.entries(boardLabels).map(([key, title]) => (
                        <option key={key} value={key}>
                          {title}
                        </option>
                      ))}
                      <option value="unknown">板型待核验</option>
                    </select>
                  </label>
                  <label>
                    指标
                    <select
                      aria-label="筛选指标"
                      value={filters.metric}
                      onChange={(event) =>
                        setFilters({
                          ...filters,
                          metric: event.target.value,
                          min: "",
                          max: "",
                        })
                      }
                    >
                      {Object.entries(metricLabels).map(([key, title]) => (
                        <option key={key} value={key}>
                          {title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {filters.metric === "last_limit_up_date"
                      ? "起始日期"
                      : "最小值"}
                    <input
                      aria-label="指标最小值"
                      type={
                        filters.metric === "last_limit_up_date"
                          ? "date"
                          : "number"
                      }
                      step={filters.metric.includes("pct") ? "any" : "1"}
                      placeholder="不限"
                      value={filters.min}
                      disabled={filters.unknown === "only"}
                      onChange={(event) =>
                        setFilters({ ...filters, min: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    {filters.metric === "last_limit_up_date"
                      ? "截至日期"
                      : "最大值"}
                    <input
                      aria-label="指标最大值"
                      type={
                        filters.metric === "last_limit_up_date"
                          ? "date"
                          : "number"
                      }
                      step={filters.metric.includes("pct") ? "any" : "1"}
                      placeholder="不限"
                      value={filters.max}
                      disabled={filters.unknown === "only"}
                      onChange={(event) =>
                        setFilters({ ...filters, max: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    缺值处理
                    <select
                      aria-label="筛选未知指标"
                      value={filters.unknown}
                      onChange={(event) =>
                        setFilters({ ...filters, unknown: event.target.value })
                      }
                    >
                      <option value="range">按区间（未设则不限）</option>
                      <option value="include">区间内及未知</option>
                      <option value="only">只看未知</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    className="btn small"
                    onClick={() => {
                      setFilters({ ...emptyFilters });
                      setQuery("");
                      setPhase("all");
                    }}
                  >
                    重置筛选
                  </button>
                </div>
                {filterIssue && (
                  <p className="intraday-monitor-filter-error" role="alert">
                    {filterIssue}
                  </p>
                )}
                <p>
                  行业 / 概念与历史指标取当前已发布行情日{" "}
                  {chartAsOf || "待核验"}
                  ，不是提醒触发时的历史归属。盘中涨幅取采样值；“—”保持未知，不按0筛选。筛选只作用于已加载列表。
                </p>
              </div>
            )}
            {view === "events" && (
              <>
                {events.error && (
                  <ErrorBox message={events.error} retry={events.reload} />
                )}
                {!!events.data && !eventScopeMatches && (
                  <ErrorBox
                    message="提醒日期与当前监控日期不一致，暂不展示，请重新读取。"
                    retry={events.reload}
                  />
                )}
                <p className="intraday-monitor-caption">
                  当前匹配 {visibleEvents.length} 条 · 已加载 {allEvents.length}{" "}
                  / {num(eventScopeMatches ? events.data?.total : undefined, 0)}{" "}
                  条。
                  {chartAsOf
                    ? `点击股票名查看截至 ${chartAsOf} 的已发布日K，盘中报价另列。`
                    : "正式日K日期尚未就绪。"}
                </p>
                {events.loading && !events.data ? (
                  <Loading />
                ) : (
                  <div className="intraday-monitor-compact-table intraday-monitor-events-table">
                    <Table
                      rows={visibleEvents}
                      rowKey="event_id"
                      pageSize={20}
                      onStockNavigate={chartAsOf ? openStock : undefined}
                      columns={[
                        { key: "name", title: "股票" },
                        ...metadataColumns,
                        {
                          key: "title",
                          title: "盘中提醒",
                          render: (_, event) => (
                            <div className="intraday-monitor-event-title">
                              <strong title={event.title || "提醒内容待核验"}>
                                {event.title || "提醒内容待核验"}
                              </strong>
                              <span className="intraday-monitor-meta-line">
                                <Badge tone={event.unread ? "warn" : "neutral"}>
                                  {event.unread ? "未读" : "已读"}
                                </Badge>{" "}
                                <Badge>
                                  {event.phase === "holding"
                                    ? "再次采样仍满足"
                                    : "首次观察"}
                                </Badge>
                              </span>
                            </div>
                          ),
                        },
                        {
                          key: "latest_price",
                          title: "采样价",
                          render: (value) => num(value, 4),
                        },
                        {
                          key: "change_pct",
                          title: "盘中涨幅",
                          render: (value) => (
                            <span className={tone(value)}>
                              {percent(value)}
                            </span>
                          ),
                        },
                        {
                          key: "first_seen_at_ms",
                          title: "首次观察",
                          render: (value) => <TableTime value={value} />,
                        },
                        factsColumn,
                        {
                          key: "confirmed_at_ms",
                          title: "再次满足",
                          render: (value) => <TableTime value={value} />,
                        },
                        {
                          key: "sources",
                          title: "价位依据",
                          sortable: false,
                          render: (_, event) => (
                            <button
                              className="intraday-monitor-detail-link"
                              onClick={() =>
                                setDetail({ kind: "evidence", row: event })
                              }
                            >
                              查看{" "}
                              {Array.isArray(event.sources)
                                ? event.sources.length
                                : "—"}{" "}
                              项依据
                            </button>
                          ),
                        },
                      ]}
                      emptyTitle={
                        settings?.enabled
                          ? "今天还没有已保存的盘中提醒"
                          : "盘中监控尚未开启"
                      }
                      emptyDescription={
                        settings?.enabled
                          ? "请结合最近采样时间与覆盖数量判断；没有提醒不代表盘中从未触碰价位。"
                          : "在采集设置中选择范围并保存开启，才会在有效盘中时段采样。"
                      }
                    />
                  </div>
                )}
              </>
            )}
            {view === "stocks" && (
              <>
                <p className="intraday-monitor-caption">
                  当前匹配 {visibleStocks.length} 只 · 已加载 {stocks.length} /{" "}
                  {num(data?.counts?.stocks, 0)}{" "}
                  只；同一股票只采样一次，多个策略与价位共用报价。
                </p>
                <div className="intraday-monitor-compact-table intraday-monitor-stocks-table">
                  <Table
                    rows={visibleStocks}
                    rowKey="instrument_id"
                    pageSize={20}
                    onStockNavigate={chartAsOf ? openStock : undefined}
                    columns={[
                      { key: "name", title: "股票" },
                      ...metadataColumns,
                      {
                        key: "origins",
                        title: "关注来源",
                        sortable: false,
                        render: (_, row) => (
                          <div className="tags intraday-monitor-origins">
                            {(Array.isArray(row.origins)
                              ? row.origins
                              : []
                            ).map((origin: string) => (
                              <Badge key={origin}>
                                {originLabels[origin] || "来源待核验"}
                              </Badge>
                            ))}
                          </div>
                        ),
                      },
                      { key: "strategy_count", title: "跟踪策略数" },
                      { key: "level_count", title: "有效价位数" },
                      {
                        key: "latest_price",
                        title: "最近采样价",
                        render: (value) => num(value, 4),
                      },
                      {
                        key: "change_pct",
                        title: "盘中涨幅",
                        render: (value) => (
                          <span className={tone(value)}>{percent(value)}</span>
                        ),
                      },
                      {
                        key: "last_observed_at_ms",
                        title: "采样时间",
                        render: (value) => <TableTime value={value} />,
                      },
                      factsColumn,
                      {
                        key: "state",
                        title: "采样状态",
                        render: (_, row) => (
                          <div className="intraday-monitor-stock-state">
                            <Badge
                              tone={
                                row.state === "unknown" ? "warn" : "neutral"
                              }
                            >
                              {row.state === "ready"
                                ? "报价已核验"
                                : row.state === "unknown"
                                  ? "待核验"
                                  : "等待采样"}
                            </Badge>
                            {row.reason && (
                              <button
                                className="intraday-monitor-detail-link intraday-monitor-state-reason"
                                title={row.reason}
                                onClick={() =>
                                  setDetail({ kind: "state", row })
                                }
                              >
                                {row.reason}
                              </button>
                            )}
                          </div>
                        ),
                      },
                    ]}
                    emptyTitle="当前范围没有监控股票"
                    emptyDescription="可调整本地筛选，或在采集设置选择跟踪、自选、手动画线及涨幅榜范围；缺值保持待核验。"
                  />
                </div>
              </>
            )}
            {view === "heat" && (
              <IntradayMonitorHeat
                stocks={visibleStocks}
                events={allEvents}
                topicDate={chartAsOf}
                tradeDate={day}
                chartAsOf={chartAsOf}
                eventsLoaded={allEvents.length}
                eventsTotal={eventScopeMatches ? events.data?.total : undefined}
                onTopic={openHeatTopic}
                onStock={openStock}
              />
            )}
            {view === "settings" && (
              <div className="intraday-monitor-settings">
                {!draft ? (
                  <Empty
                    title="完整采集设置待读取"
                    description="设置未完整读取前不能修改。"
                  />
                ) : (
                  <>
                    <label className="intraday-monitor-enable">
                      <input
                        type="checkbox"
                        checked={draft.enabled}
                        disabled={!!busy || !active}
                        onChange={(event) =>
                          change("enabled", event.target.checked)
                        }
                      />
                      <span>
                        <strong>开启每 10 分钟盘中监控</strong>
                        <small>
                          保存后由服务端在已核验交易日和有效时段运行，关闭页面仍可执行。
                        </small>
                      </span>
                    </label>
                    <fieldset disabled={!!busy || !active}>
                      <legend>股票范围（合并去重）</legend>
                      {Object.entries(originLabels).map(([key, title]) => (
                        <label key={key}>
                          <input
                            type="checkbox"
                            checked={draft.scopes[key]}
                            onChange={(event) => {
                              setDraft({
                                ...draft,
                                scopes: {
                                  ...draft.scopes,
                                  [key]: event.target.checked,
                                },
                              });
                              setDirty(true);
                              setNotice("");
                            }}
                          />
                          {title}
                          <small>
                            {key === "gainers_30d" &&
                            data?.counts?.[key] == null
                              ? "待准备"
                              : num(data?.counts?.[key], 0)}
                          </small>
                        </label>
                      ))}
                    </fieldset>
                    <p>
                      涨幅榜取最新已发布行情日的近30交易日收益前1000，只影响盘中监控名单。各范围合计去重后最多2000只，超限时请缩小范围；开启总开关并保存后才会采样。
                    </p>
                    <fieldset disabled={!!busy || !active}>
                      <legend>提醒依据</legend>
                      <label>
                        <input
                          type="checkbox"
                          checked={draft.manual_enabled}
                          onChange={(event) =>
                            change("manual_enabled", event.target.checked)
                          }
                        />
                        手动画线
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={draft.automatic_enabled}
                          onChange={(event) =>
                            change("automatic_enabled", event.target.checked)
                          }
                        />
                        默认支撑 / 压力价位
                      </label>
                      <p>
                        两类依据同时满足时合并展示，保留各自的价位、版本与观察时间。
                      </p>
                    </fieldset>
                    {settingsChanged && (
                      <Warning>
                        设置已在其他位置变化。请放弃本地修改并重新读取，再进行调整。
                      </Warning>
                    )}
                    <div className="intraday-monitor-setting-actions">
                      <button
                        type="button"
                        className="btn primary"
                        disabled={
                          !dirty || !!busy || !active || settingsChanged
                        }
                        onClick={() => void saveSettings()}
                      >
                        {busy === "settings" ? <Spinner /> : <Save size={15} />}
                        保存采集设置
                      </button>
                      <button
                        type="button"
                        className="btn"
                        disabled={!!busy}
                        onClick={() => {
                          setDirty(false);
                          setDraft(monitorSettingsValue(data?.settings));
                          setError("");
                          setNotice("");
                          overview.reload();
                        }}
                      >
                        放弃修改并重新读取
                      </button>
                      {dirty && <span className="muted">尚未保存</span>}
                    </div>
                  </>
                )}
                <p>
                  普通采样保留 {num(data?.retention?.samples_sessions, 0)}{" "}
                  个交易日；提醒及其必要证据保留{" "}
                  {num(data?.retention?.events_sessions, 0)}{" "}
                  个交易日。有效引用可能延长必要证据保留。
                </p>
                {batch?.reason && <p>最近批次说明：{batch.reason}</p>}
              </div>
            )}
          </Panel>
          <Warning>
            <BellRing size={14} />
            {data?.notice ||
              "这是每10分钟的盘中采样提醒，两个采样点之间可能发生未捕获的穿越；再次采样满足不等于持续站稳，也不等于日K收盘确认。"}{" "}
            刷新只读取已保存结果，不发起采集。
          </Warning>
        </>
      )}
      {detail && (
        <Modal
          title={`${detail.row.name || detail.row.code || detail.row.instrument_id} · ${detailLabels[detail.kind]}`}
          onClose={() => setDetail(null)}
        >
          <div className="modal-body intraday-monitor-detail">
            <DetailContent detail={detail} topicDate={chartAsOf} />
          </div>
        </Modal>
      )}
    </div>
  );
}
