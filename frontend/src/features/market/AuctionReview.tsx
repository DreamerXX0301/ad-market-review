import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CalendarDays,
  CheckCircle2,
  Clock3,
  RefreshCw,
  Sunrise,
} from "lucide-react";
import { post, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  useToast,
} from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import AIEntry from "../ai/AIEntry";
import { amount, integer, num, percent, tone } from "../../lib/format";
import { noonClock, noonLines, noonRows } from "./noonPresentation";
import {
  auctionAutomaticToday,
  auctionAutomationView,
  auctionBackgroundTags,
  auctionCanAct,
  auctionCaptureState,
  auctionNumber,
  auctionPayload,
  auctionPreviousFocus,
  auctionShouldPoll,
  auctionSortRows,
  auctionStates,
} from "./auctionPresentation";
import AuctionAutomation from "./AuctionAutomation";
import AuctionDiscovery from "./AuctionDiscovery";
import AuctionFeedback from "./AuctionFeedback";
import AuctionBoardExplorer from "./AuctionBoardExplorer";
import AuctionClassifications from "./AuctionClassifications";
import AuctionHeightFilters from "./AuctionHeightFilters";
import AuctionHeightBadge from "./AuctionHeightBadge";
import AuctionStockBadges from "./AuctionStockBadges";
import {
  auctionHeightMatches,
  auctionSortByHeight,
  type AuctionHeightKey,
} from "./auctionHeightPresentation";
import AuctionCopyText from "./AuctionCopyText";
import AuctionAssociationSummary from "./AuctionAssociationSummary";
import AuctionPageNavigation from "./AuctionPageNavigation";
import useAuctionCloseRefresh from "./useAuctionCloseRefresh";
import { useHistoricalPrefetch } from "./useHistoricalPrefetch";
import AuctionCloseGroups from "./AuctionCloseGroups";
import { refreshTaskActivity } from "../../components/taskActivity";
import "./AuctionReview.css";

const AUCTION_PAGE_SECTIONS = [
  {
    id: "auction-overview",
    label: "竞价概览",
    description: "日期、留存状态与样本覆盖",
  },
  {
    id: "auction-feedback",
    label: "昨日重点反馈",
    description: "昨日涨停炸板与今日高低开的反馈",
  },
  {
    id: "auction-discovery",
    label: "今日竞价发现",
    description: "从市场竞价异动中找重点名单外的新方向",
  },
  {
    id: "auction-previous",
    label: "昨日复盘",
    description: "此前留存的原判断与今日竞价对照",
  },
  {
    id: "auction-low-open",
    label: "低开复核",
    description: "本次样本中开盘后需要关注的低开股",
  },
  {
    id: "auction-topics",
    label: "板块与个股",
    description: "行业概念与共振合并，左侧板块、右侧冻结个股",
  },
  {
    id: "auction-observation",
    label: "竞价观察单",
    description: "冻结名单、点选分组、排序与分页",
  },
  {
    id: "auction-benchmark",
    label: "来源基准",
    description: "来源短线样本及其独立标签",
  },
  {
    id: "auction-close-review",
    label: "盘后核对",
    description: "竞价到收盘的事后核对",
  },
  {
    id: "auction-automation",
    label: "自动采集",
    description: "服务计划、工作进程与最近结果",
  },
];

const COLUMNS = [
  ["auction_price", "竞价价", "元"],
  ["auction_pct", "竞价涨幅", "%"],
  ["auction_amount", "竞价额", "元"],
  ["auction_volume", "成交量", "手"],
  ["auction_volume_ratio", "来源量比", "倍"],
  ["auction_yesterday_ratio_pct", "占昨日量", "%"],
  ["auction_turnover_pct", "竞价换手", "%"],
];
function metric(row: Row, field: string) {
  const value = auctionNumber(row[field]);
  if (value == null) return "—";
  if (field === "auction_amount") return amount(value);
  if (field === "auction_volume") return integer(value);
  if (field === "auction_pct") return percent(value);
  if (field.endsWith("_pct")) return `${num(value)}%`;
  if (field === "auction_volume_ratio") return `${num(value)}倍`;
  return num(value);
}
function BackgroundTags({ reasons }: { reasons: unknown }) {
  const tags = auctionBackgroundTags(reasons);
  return (
    <div className="auction-background-tags">
      {tags.length ? (
        tags.map((tag) => (
          <span
            key={tag.source}
            className={`auction-background-tag ${tag.tone}`}
            title={tag.source}
          >
            {tag.label}
          </span>
        ))
      ) : (
        <span className="auction-muted">关注</span>
      )}
    </div>
  );
}

export default function AuctionReview({
  date,
  latestDate,
  prefetchDates,
  onDate,
  onDatesChanged,
  onStock,
  chartDate,
}: {
  date: string;
  latestDate?: string;
  prefetchDates?: string[];
  onDate: (day: string) => void;
  onDatesChanged?: () => void;
  onStock?: (id: string) => void;
  chartDate?: string;
}) {
  const path = `/market/auction${date ? `?date=${encodeURIComponent(date)}` : ""}`;
  const result = useApi<Row>(path);
  const automaticResult = useApi<Row>("/market/auction/automation");
  const automation = auctionAutomationView(automaticResult.data);
  const active = usePageActive();
  const notify = useToast();
  const view = result.data?.date === date || !date ? result.data : null;
  const selected = date || view?.date || "";
  const automaticToday =
    auctionAutomaticToday(automation) && selected === automation?.today;
  const payload = auctionPayload(view, selected);
  const primarySettled =
    !result.loading &&
    !result.error &&
    view?.date === date &&
    !["queued", "running", "paused"].includes(view?.task?.status);
  useHistoricalPrefetch(
    date === latestDate && primarySettled && !!payload,
    path,
    date,
    view?.snapshot?.snapshot_id,
    prefetchDates,
    primarySettled,
  );
  const [closeOpen, setCloseOpen] = useState(false);
  const closeResult = useApi<Row>(
    closeOpen && payload
      ? `/market/auction/close-review?date=${encodeURIComponent(selected)}`
      : null,
  );
  const closeReview =
    closeResult.data?.date === selected ? closeResult.data : null;
  useAuctionCloseRefresh({
    open: closeOpen,
    hasSnapshot: !!payload,
    reload: closeResult.reload,
  });
  const topicResult = useApi<Row>(
    payload && selected
      ? `/market/auction/topic-summary?date=${encodeURIComponent(selected)}`
      : null,
  );
  const topicView =
    topicResult.data?.date === selected &&
    topicResult.data?.version === "auction-topic-summary-v1"
      ? topicResult.data
      : null;
  const closeItems = noonRows(closeReview?.items).filter((row) =>
    /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id || ""),
  );
  const hasCloseFacts =
    closeItems.length > 0 ||
    (auctionNumber(closeReview?.summary?.requested) != null &&
      closeReview!.summary.requested >= 0 &&
      auctionNumber(closeReview?.summary?.valid) != null &&
      closeReview!.summary.valid >= 0);
  const hasCloseGroups =
    closeReview?.version === "auction-close-review-v1" &&
    noonRows(closeReview.groups).length > 0;
  const [submitError, setSubmitError] = useState("");
  const [pendingDay, setPendingDay] = useState("");
  const [sort, setSort] = useState<{ key: string; direction: "asc" | "desc" }>({
    key: "previous_board_count",
    direction: "desc",
  });
  const [benchmarkDirection, setBenchmarkDirection] = useState<"asc" | "desc">(
    "desc",
  );
  const [observationPage, setObservationPage] = useState(0);
  const [associationSelection, setAssociationSelection] = useState({
    date: "",
    key: "",
  });
  const [heightSelection, setHeightSelection] = useState<{
    date: string;
    key: AuctionHeightKey;
  }>({ date: "", key: "all" });
  const submitting = useRef(false);
  const currentDay = useRef(selected);
  currentDay.current = selected;
  const control = auctionCaptureState(
    view,
    selected,
    pendingDay === selected,
    automation,
  );
  const busy = pendingDay === selected;
  const taskId = view?.task?.task_id || view?.task?.job_id;
  useEffect(() => {
    setSubmitError("");
    setCloseOpen(false);
    setObservationPage(0);
    setAssociationSelection({ date: selected, key: "" });
    setHeightSelection({ date: selected, key: "all" });
  }, [selected]);
  useEffect(() => {
    if (!active || !auctionShouldPoll(view, selected)) return;
    const timer = window.setInterval(result.reload, 2000);
    return () => window.clearInterval(timer);
  }, [active, view?.state, selected, result.reload]);
  useEffect(() => {
    if (!active || view?.today !== selected || view?.state !== "not_collected")
      return;
    // Poll saved local state so a page opened before 09:25:30 can observe the automatic result.
    const timer = window.setInterval(result.reload, 20000);
    return () => window.clearInterval(timer);
  }, [active, view?.today, view?.state, selected, result.reload]);
  useEffect(() => {
    const unlocked = () => {
      result.reload();
      automaticResult.reload();
      onDatesChanged?.();
    };
    window.addEventListener("workbench-auction-unlocked", unlocked);
    return () =>
      window.removeEventListener("workbench-auction-unlocked", unlocked);
  }, [result.reload, automaticResult.reload, onDatesChanged]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(automaticResult.reload, 15000);
    return () => window.clearInterval(timer);
  }, [active, automaticResult.reload]);
  useEffect(() => {
    if (automation?.last_run?.date === selected) result.reload();
  }, [
    selected,
    automation?.last_run?.job_id,
    automation?.last_run?.state,
    automation?.last_run?.attempts,
    result.reload,
  ]);
  async function changeTask(action: "cancel" | "pause") {
    if (submitting.current || !auctionCanAct(view, selected, action) || !taskId)
      return;
    const day = selected;
    submitting.current = true;
    setPendingDay(day);
    setSubmitError("");
    try {
      await post(`/tasks/${encodeURIComponent(taskId)}/${action}`);
      refreshTaskActivity();
      notify(
        action === "pause"
          ? "已暂停采集，服务不会自动继续。"
          : "已取消本日采集。",
      );
      onDatesChanged?.();
    } catch (error: any) {
      if (currentDay.current === day)
        setSubmitError(error.message || "任务操作未完成，请刷新状态。");
    } finally {
      submitting.current = false;
      setPendingDay("");
      result.reload();
    }
  }
  const summary = payload?.quotes?.summary || {};
  const universe = payload?.universe || {};
  const candidates = new Map(
    noonRows(universe.items).map((row) => [row.instrument_id, row]),
  );
  const sourceRows = noonRows(payload?.quotes?.items).map((row) => ({
    ...candidates.get(row.instrument_id),
    ...row,
    ...(view?.stock_boards?.[row.instrument_id] || {}),
    suspension: view?.stock_suspensions?.[row.instrument_id],
  }));
  const rows =
    sort.key === "previous_board_count"
      ? auctionSortByHeight(sourceRows, sort.direction)
      : auctionSortRows(sourceRows, sort.key, sort.direction);
  const selectedHeight =
    heightSelection.date === selected ? heightSelection.key : "all";
  function selectHeight(key: AuctionHeightKey) {
    setHeightSelection({ date: selected, key });
    setObservationPage(0);
  }
  const selectedAssociationKey =
    associationSelection.date === selected ? associationSelection.key : "";
  const selectedAssociation = noonRows(topicView?.groups).find(
    (row) =>
      `${row.source}:${row.kind}:${row.topic_code}` === selectedAssociationKey,
  );
  const selectedAssociationIds =
    selectedAssociation && Array.isArray(selectedAssociation.instrument_ids)
      ? new Set<string>(selectedAssociation.instrument_ids)
      : null;
  const combinedIds =
    selectedHeight === "all"
      ? selectedAssociationIds
      : new Set<string>(
          sourceRows
            .filter(
              (row) =>
                auctionHeightMatches(row, selectedHeight) &&
                (!selectedAssociationIds ||
                  selectedAssociationIds.has(row.instrument_id)),
            )
            .map((row) => row.instrument_id),
        );
  const observationRows = combinedIds
    ? rows.filter((row) => combinedIds.has(row.instrument_id))
    : rows;
  function selectAssociation(key: string) {
    setAssociationSelection((previous) => ({
      date: selected,
      key: previous.date === selected && previous.key === key ? "" : key,
    }));
    setObservationPage(0);
  }
  const observationPages = Math.max(1, Math.ceil(observationRows.length / 20));
  const safeObservationPage = Math.min(observationPage, observationPages - 1);
  const visibleObservationRows = observationRows.slice(
    safeObservationPage * 20,
    (safeObservationPage + 1) * 20,
  );
  const gaps = [
    ...new Set([
      ...noonLines(payload?.gaps),
      ...noonLines(payload?.quotes?.gaps),
    ]),
  ];
  const benchmark = payload?.benchmark || {};
  const benchmarkRows = auctionSortRows(
    noonRows(benchmark.items),
    "auction_pct",
    benchmarkDirection,
  );
  const analysis = noonLines(payload?.analysis);
  const previousFocus = auctionPreviousFocus(payload);
  const preparation =
    !payload && selected === view?.today && view?.preparation
      ? auctionPreviousFocus({
          date: selected,
          previous_date: view.preparation.date,
          collected_start_at: view.now,
          previous_review: view.preparation,
          quotes: { items: [] },
        })
      : null;
  const lowOpen = auctionSortRows(
    rows.filter((row) => (auctionNumber(row.auction_pct) ?? 0) < 0),
    "auction_pct",
    "asc",
  ).slice(0, 6);
  const state = view?.state || "not_collected";
  const identity = (row: Row, showHeight = true) => (
    <div className="auction-identity">
      <button
        className="auction-stock"
        disabled={!chartDate || !onStock}
        title={
          chartDate
            ? `K线截至 ${chartDate}，竞价价格见本页`
            : "该观察日之前没有已发布收盘行情"
        }
        onClick={() => onStock?.(row.instrument_id)}
      >
        {row.name || row.instrument_id}
      </button>
      <CopyStockCode instrumentId={row.instrument_id} />
      <AuctionStockBadges
        row={{
          ...candidates.get(row.instrument_id),
          ...row,
          ...(view?.stock_boards?.[row.instrument_id] || {}),
          suspension: view?.stock_suspensions?.[row.instrument_id],
        }}
        showEvent={showHeight}
      />
    </div>
  );
  return (
    <AuctionPageNavigation sections={AUCTION_PAGE_SECTIONS}>
      <div className="auction-review page-stack">
        <div className="auction-page-head" id="auction-overview" tabIndex={-1}>
          <div>
            <div className="auction-eyebrow">
              <Sunrise size={16} />{" "}
              {automaticToday
                ? "服务自动采集 · 终态观察"
                : "开盘前五分钟 · 终态观察"}
            </div>
            <h2>竞价概览</h2>
            <p>核对昨日重点股的竞价表现，也看重点名单外有哪些新方向。</p>
          </div>
          <div className="auction-head-actions">
            {selected && (
              <AIEntry kind="market" asOf={selected} marketStage="auction" />
            )}
            {view?.today && (
              <button
                className="btn"
                onClick={() => onDate(view.today)}
                disabled={selected === view.today}
              >
                <CalendarDays size={14} />
                今天
              </button>
            )}
            <button
              className="btn"
              onClick={result.reload}
              disabled={result.loading}
            >
              <RefreshCw size={14} />
              读取状态
            </button>
          </div>
        </div>
        <div className="auction-scope-strip">
          <strong>{selected || "正在读取观察日"}</strong>
          <Badge tone={state === "available" ? "good" : "neutral"}>
            {auctionStates[state] || state}
          </Badge>
          <span>
            <Clock3 size={13} /> 北京时间09:25:30自动采集 · 09:30前截止
          </span>
          {payload && <span>前一交易日 {payload.previous_date || "未知"}</span>}
        </div>
        <p className="auction-control-reason" role="status">
          {control.reason}
        </p>
        {view?.retention?.notice && (
          <p className="auction-retention-note">{view.retention.notice}</p>
        )}
        {result.error && (
          <ErrorBox message={result.error} retry={result.reload} />
        )}
        {submitError && (
          <ErrorBox message={submitError} retry={result.reload} />
        )}
        {result.loading && !view && <Loading />}
        {!payload && preparation && (
          <Panel
            title="采集前准备 · 最近已发布日"
            subtitle={
              view?.preparation?.notice ||
              "严格前一交易日待采集时核验；当前材料尚未冻结。"
            }
            actions={<Badge tone="warn">尚未冻结</Badge>}
          >
            <div className="auction-panel-body">
              <div className="auction-preparation-head">
                <span>
                  材料日期{" "}
                  <b>
                    {preparation.previous?.date ||
                      view?.preparation?.date ||
                      "未知"}
                  </b>
                </span>
                <span>
                  实际记录{" "}
                  {preparation.retained
                    ? noonClock(preparation.previous?.recorded_at_ms)
                    : "未留存"}
                </span>
              </div>
              {preparation.retained ? (
                <>
                  <ul className="auction-lines">
                    {preparation.lines.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                  {preparation.items.length > 0 && (
                    <div className="auction-preparation-list">
                      {preparation.items.map((row) => (
                        <article key={row.instrument_id}>
                          {identity(row)}
                          {row.mainline && <strong>{row.mainline}</strong>}
                          {noonRows(row.notes).length ? (
                            <div className="auction-original-notes">
                              {noonRows(row.notes).map((note, index) => (
                                <p key={index}>
                                  <b>{note.label}</b>
                                  {note.text}
                                </p>
                              ))}
                            </div>
                          ) : (
                            <p className="auction-muted">
                              已留存关注，暂无逐股判断原文。
                            </p>
                          )}
                          <small>
                            竞价尚未采集，09:25:30后留存结果再逐项对照。
                          </small>
                        </article>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <p className="auction-muted">
                  最近已发布日尚未留存可核验的复盘判断或逐股主线。可以先整理自选；本次不会替你补写昨日判断。
                </p>
              )}
              <p className="auction-reading-note">
                这份准备会随着本地保存的复盘变化。采集时由工作进程核实严格前一交易日，并冻结实际引用版本。
              </p>
            </div>
          </Panel>
        )}
        {view?.task && !payload && state !== "purged" && (
          <Panel
            title="本日采集任务"
            actions={
              <div className="button-group">
                {auctionCanAct(view, selected, "pause") && (
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => changeTask("pause")}
                  >
                    暂停
                  </button>
                )}
                {auctionCanAct(view, selected, "cancel") && (
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => changeTask("cancel")}
                  >
                    取消
                  </button>
                )}
              </div>
            }
          >
            <div className="auction-panel-body">
              <p>
                {view.task.error_summary ||
                  view.task.message ||
                  view.task.phase ||
                  auctionStates[state]}
              </p>
              <small className="auction-muted">
                {taskId} · 过窗不补造；暂停或取消后不会自动继续。
              </small>
            </div>
          </Panel>
        )}
        {!payload && !["queued", "running"].includes(state) && (
          <Panel>
            <Empty
              title={
                state === "purged"
                  ? "竞价已按保留规则清理"
                  : state === "not_collected"
                    ? automaticToday
                      ? "已安排今天的自动竞价"
                      : "自动竞价尚未开启"
                    : auctionStates[state]
              }
              description={
                state === "purged"
                  ? "该日竞价正文及内嵌原件已到期清理，只保留日任务台账和清理回执；不重新采集或补造历史竞价。"
                  : state === "not_collected"
                    ? automaticToday
                      ? "服务运行时，约09:25:30起在09:30前自动采集。浏览器可以关闭，结果就绪后回来查看；错过窗口不补造。"
                      : "在数据中心开启自动竞价后，服务仅在交易日09:25:30起采集系统名单。没有留存的历史竞价保持空白。"
                    : "当前没有可用的竞价正文。仅来源暂未就绪时允许窗口内有限自动重试；显式暂停、取消或最终失败不自动继续。"
              }
            />
          </Panel>
        )}
        {payload && (
          <>
            <div className="auction-metrics">
              <div>
                <span>有效竞价覆盖</span>
                <strong>
                  {integer(summary.valid)}
                  <small> / {integer(summary.requested)}</small>
                </strong>
                <p>缺报价 {integer(summary.missing)} 只</p>
              </div>
              <div>
                <span>高开 / 低开 / 平开</span>
                <strong className="auction-breadth">
                  <b className="up">{integer(summary.up)}</b>
                  <i>/</i>
                  <b className="down">{integer(summary.down)}</b>
                  <i>/</i>
                  <b>{integer(summary.flat)}</b>
                </strong>
                <p>只统计本次有效样本</p>
              </div>
              <div>
                <span>样本竞价涨幅中位数</span>
                <strong className={tone(auctionNumber(summary.median_pct))}>
                  {percent(auctionNumber(summary.median_pct))}
                </strong>
                <p>相对来源前收盘价</p>
              </div>
              <div>
                <span>有效样本竞价额</span>
                <strong>{amount(auctionNumber(summary.amount))}</strong>
                <p>金额有效 {integer(summary.amount_valid)} 只</p>
              </div>
            </div>
            <div className="auction-capture-proof">
              <CheckCircle2 size={14} />
              <span>留存于 {noonClock(view?.snapshot?.recorded_at_ms)}</span>
              <span>
                采集 {noonClock(view?.snapshot?.collected_start_at)} —{" "}
                {noonClock(view?.snapshot?.collected_end_at)}
              </span>
              <Badge>
                {view?.snapshot?.quality === "complete"
                  ? "核验通过"
                  : "部分可用"}
              </Badge>
            </div>
            <section id="auction-feedback" tabIndex={-1}>
              <AuctionFeedback
                date={selected}
                topicView={topicView}
                selectedKey={selectedAssociationKey}
                selectedIds={combinedIds}
                onAssociationSelect={selectAssociation}
                heightRows={sourceRows}
                selectedHeight={selectedHeight}
                onHeightSelect={selectHeight}
                onStock={onStock}
                chartDate={chartDate}
              />
            </section>
            <section id="auction-discovery" tabIndex={-1}>
              <AuctionDiscovery
                key={selected}
                date={selected}
                collecting={
                  view?.task?.status === "running" &&
                  view.task.parameters?.automation?.discovery?.version ===
                    "auction-discovery-v1"
                }
                active={active}
                onStock={onStock}
                chartDate={chartDate}
              />
            </section>
            <section id="auction-previous" tabIndex={-1}>
              <Panel
                title="昨日复盘 → 今日竞价"
                subtitle="引用采集开始前已经保存的严格前一交易日材料，原判断与本次事实并列。"
                actions={
                  <Badge tone={previousFocus.retained ? "good" : "warn"}>
                    {previousFocus.retained ? "已留存原文" : "原判断未留存"}
                  </Badge>
                }
              >
                <div className="auction-panel-body">
                  <div className="auction-prior-summary">
                    <div>
                      <span className="auction-section-eyebrow">
                        昨天怎么看
                      </span>
                      {previousFocus.retained ? (
                        <ul className="auction-lines">
                          {previousFocus.lines.map((line, index) => (
                            <li key={index}>{line}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="auction-muted">
                          {previousFocus.previous?.state === "background_only"
                            ? "昨日只有已发布收盘事实，没有留存复盘判断；不补写昨天的预期。"
                            : "没有可核验的昨日留存判断。已取得的竞价仍按真实样本展示。"}
                        </p>
                      )}
                      <small>
                        {payload.previous_date || "严格前日待核验"} · 原记录{" "}
                        {previousFocus.retained
                          ? noonClock(previousFocus.previous?.recorded_at_ms)
                          : "未留存"}
                      </small>
                    </div>
                    <div>
                      <span className="auction-section-eyebrow">
                        今天先核对
                      </span>
                      <p>
                        原关注股票的竞价是否仍有承接；明显低开的样本先列为待复核。
                      </p>
                      <small>
                        自由文字不自动判定兑现或证伪，当前题材不回填昨日。
                      </small>
                    </div>
                  </div>
                  {previousFocus.items.length ? (
                    <div className="auction-focus-wrap">
                      <table className="auction-focus-table">
                        <thead>
                          <tr>
                            <th>昨日原关注与判断</th>
                            <th>本次竞价事实</th>
                            <th>开盘后核对</th>
                          </tr>
                        </thead>
                        <tbody>
                          {previousFocus.items.map((row) => (
                            <tr key={row.instrument_id}>
                              <td>
                                {identity(row)}
                                {row.mainline && (
                                  <strong className="auction-original-mainline">
                                    {row.mainline}
                                  </strong>
                                )}
                                {noonRows(row.notes).length ? (
                                  <details
                                    className="auction-original-notes"
                                    open
                                  >
                                    <summary>
                                      原判断与依据 · {row.notes.length}条
                                    </summary>
                                    {noonRows(row.notes).map((note, index) => (
                                      <p key={index}>
                                        <b>{note.label}</b>
                                        {note.text}
                                        {note.source && (
                                          <small>来源：{note.source}</small>
                                        )}
                                      </p>
                                    ))}
                                  </details>
                                ) : (
                                  <small className="auction-muted">
                                    原留存没有逐股判断文字
                                  </small>
                                )}
                                {row.recorded_at_ms && (
                                  <small className="auction-muted">
                                    记录 {noonClock(row.recorded_at_ms)}
                                  </small>
                                )}
                              </td>
                              <td>
                                {row.quote &&
                                auctionNumber(row.quote.auction_price) !=
                                  null ? (
                                  <>
                                    <strong
                                      className={tone(
                                        auctionNumber(row.quote.auction_pct),
                                      )}
                                    >
                                      {percent(
                                        auctionNumber(row.quote.auction_pct),
                                      )}
                                    </strong>
                                    <span>
                                      竞价价{" "}
                                      {num(
                                        auctionNumber(row.quote.auction_price),
                                      )}{" "}
                                      元
                                    </span>
                                    <small>
                                      竞价额{" "}
                                      {amount(
                                        auctionNumber(row.quote.auction_amount),
                                      )}
                                    </small>
                                  </>
                                ) : (
                                  <span className="auction-muted">
                                    {row.quote?.reason ||
                                      "本次未取得该股有效竞价，不能按0处理。"}
                                  </span>
                                )}
                              </td>
                              <td>
                                {row.quote &&
                                auctionNumber(row.quote.auction_price) !=
                                  null ? (
                                  <p>
                                    观察开盘后是否维持在本次竞价价{" "}
                                    <b>
                                      {num(
                                        auctionNumber(row.quote.auction_price),
                                      )}
                                      元
                                    </b>{" "}
                                    附近，成交后有无明显回落。
                                  </p>
                                ) : (
                                  <p className="auction-muted">
                                    先核验真实报价，再与原判断对照。
                                  </p>
                                )}
                                {(noonLines(row.triggers).length > 0 ||
                                  noonLines(row.invalidations).length > 0) && (
                                  <details className="auction-original-notes">
                                    <summary>原观察条件</summary>
                                    {noonLines(row.triggers).map(
                                      (line, index) => (
                                        <p key={`t-${index}`}>原观察：{line}</p>
                                      ),
                                    )}
                                    {noonLines(row.invalidations).map(
                                      (line, index) => (
                                        <p key={`i-${index}`}>
                                          原失效条件：{line}
                                        </p>
                                      ),
                                    )}
                                  </details>
                                )}
                                <small className="auction-muted">
                                  仅供观察，不触发策略或自动跟踪。
                                </small>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="auction-prior-empty">
                      昨日没有留存明确的逐股关注。下方保留本次冻结候选，之后可先在复盘中留下原判断。
                    </p>
                  )}
                </div>
              </Panel>
            </section>
            <section id="auction-low-open" tabIndex={-1}>
              <Panel
                title="开盘后优先复核"
                subtitle="从本次有效样本中列示低开股票；低开不自动等于失败或买卖信号。"
              >
                <div className="auction-panel-body">
                  {lowOpen.length ? (
                    <div className="auction-low-open">
                      {lowOpen.map((row) => (
                        <article key={row.instrument_id}>
                          <div>
                            {identity(row)}
                            <strong className="down">
                              {percent(auctionNumber(row.auction_pct))}
                            </strong>
                          </div>
                          <BackgroundTags reasons={row.reasons} />
                          <AuctionClassifications
                            view={topicView}
                            date={selected}
                            instrumentId={row.instrument_id}
                          />
                          <span>
                            竞价价 {num(auctionNumber(row.auction_price))}元 ·
                            竞价额 {amount(auctionNumber(row.auction_amount))}
                          </span>
                          <small>
                            观察开盘后能否收回竞价参照，或成交后继续回落；结合原判断核对。
                          </small>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="auction-muted">
                      本次有效报价样本没有低开记录；缺报价仍单独保留未知。
                    </p>
                  )}
                </div>
              </Panel>
            </section>
            <section id="auction-topics" tabIndex={-1}>
              <AuctionBoardExplorer
                key={selected}
                result={{ ...topicResult, data: topicView }}
                date={selected}
                quotes={sourceRows}
                onStock={onStock}
                chartDate={chartDate}
              />
            </section>
            <section id="auction-observation" tabIndex={-1}>
              <Panel
                title="竞价观察单"
                subtitle={
                  universe.scope ||
                  summary.scope ||
                  "冻结候选样本；不据高开或量比自动产生买卖判断。"
                }
                actions={
                  <Badge>
                    {integer(universe.requested ?? rows.length)}只已选 ·{" "}
                    {integer(universe.omitted ?? 0)}只未纳入
                  </Badge>
                }
              >
                <AuctionHeightFilters
                  rows={sourceRows}
                  value={selectedHeight}
                  onChange={selectHeight}
                />
                <AuctionAssociationSummary
                  view={topicView}
                  date={selected}
                  selectedKey={selectedAssociationKey}
                  onSelect={selectAssociation}
                />
                <div className="auction-observation-controls">
                  <span>
                    当前 {integer(observationRows.length)} /{" "}
                    {integer(rows.length)}只 · 每页20只
                    {selectedAssociation
                      ? ` · ${selectedAssociation.name}`
                      : ""}
                    {selectedHeight !== "all" ? " · 已按昨日板高筛选" : ""}
                  </span>
                  {selectedAssociation && (
                    <button
                      className="btn small"
                      onClick={() => selectAssociation(selectedAssociationKey)}
                    >
                      取消选中
                    </button>
                  )}
                  <AuctionCopyText
                    text={observationRows
                      .map((row) => row.instrument_id.slice(0, 6))
                      .join("\n")}
                    label="复制当前股票列表代码"
                  />
                </div>
                <p className="auction-height-sort-note">
                  默认按昨日板高降序、同高度按竞价涨幅降序。可点表头切换；未知值排在末尾。
                </p>
                <div className="auction-table-wrap">
                  <table className="auction-table">
                    <thead>
                      <tr>
                        <th>股票</th>
                        <th
                          aria-sort={
                            sort.key === "previous_board_count"
                              ? sort.direction === "desc"
                                ? "descending"
                                : "ascending"
                              : "none"
                          }
                        >
                          <button
                            type="button"
                            className="auction-sort"
                            onClick={() =>
                              setSort({
                                key: "previous_board_count",
                                direction:
                                  sort.key === "previous_board_count" &&
                                  sort.direction === "desc"
                                    ? "asc"
                                    : "desc",
                              })
                            }
                          >
                            昨日板高 / 入选背景{" "}
                            {sort.key === "previous_board_count" &&
                              (sort.direction === "desc" ? (
                                <ArrowDown size={12} />
                              ) : (
                                <ArrowUp size={12} />
                              ))}
                          </button>
                        </th>
                        <th className="auction-affiliations-column">
                          行业 / 概念 · 样本均幅
                        </th>
                        {COLUMNS.map(([field, title, unit]) => (
                          <th
                            key={field}
                            aria-sort={
                              sort.key === field
                                ? sort.direction === "asc"
                                  ? "ascending"
                                  : "descending"
                                : "none"
                            }
                          >
                            <button
                              className="auction-sort"
                              onClick={() =>
                                setSort({
                                  key: field,
                                  direction:
                                    sort.key === field &&
                                    sort.direction === "desc"
                                      ? "asc"
                                      : "desc",
                                })
                              }
                            >
                              {title}
                              <small>{unit}</small>
                              {sort.key === field &&
                                (sort.direction === "asc" ? (
                                  <ArrowUp size={12} />
                                ) : (
                                  <ArrowDown size={12} />
                                ))}
                            </button>
                          </th>
                        ))}
                        <th>核验与缺口</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleObservationRows.map((row) => (
                        <tr key={row.instrument_id}>
                          <td>{identity(row, false)}</td>
                          <td className="auction-background">
                            <BackgroundTags reasons={row.reasons} />
                            <AuctionHeightBadge row={row} />
                          </td>
                          <td className="auction-affiliations-column">
                            <AuctionClassifications
                              view={topicView}
                              date={selected}
                              instrumentId={row.instrument_id}
                            />
                          </td>
                          {COLUMNS.map(([field]) => (
                            <td
                              key={field}
                              className={
                                field === "auction_pct"
                                  ? tone(auctionNumber(row[field]))
                                  : ""
                              }
                            >
                              {metric(row, field)}
                            </td>
                          ))}
                          <td className="auction-row-state">
                            {row.reason ||
                              (row.state === "unknown"
                                ? "报价未知"
                                : "终态已核验")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="auction-observation-pagination">
                  <span>
                    每页20只 · 第{safeObservationPage + 1}/{observationPages}页
                  </span>
                  <button
                    className="btn small"
                    disabled={safeObservationPage === 0}
                    onClick={() => setObservationPage(safeObservationPage - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="btn small"
                    disabled={safeObservationPage + 1 >= observationPages}
                    onClick={() => setObservationPage(safeObservationPage + 1)}
                  >
                    下一页
                  </button>
                </div>
                <div className="auction-table-foot">
                  竞价额以元、成交量以手展示；来源量比与占昨日量分别列示。未提供逐票行情时刻，不把响应组装时间当报价时间。K线截至{" "}
                  {chartDate || "此前已发布收盘日未知"}。
                </div>
              </Panel>
            </section>
            <div className="auction-reading-grid">
              <Panel
                title="本次观察要点"
                subtitle="只描述本份样本，后续走势需要继续核对。"
              >
                <div className="auction-panel-body">
                  {analysis.length ? (
                    <ul className="auction-lines">
                      {analysis.map((line, index) => (
                        <li key={index}>{line}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="auction-muted">暂无足够证据生成观察要点。</p>
                  )}
                  <p className="auction-reading-note">
                    重点看昨日关注是否仍有承接，以及明显低开的负反馈。单次终态无法还原撤单过程。
                  </p>
                </div>
              </Panel>
              <section id="auction-benchmark" tabIndex={-1}>
                <Panel
                  title="来源短线基准"
                  actions={
                    <div
                      className="button-group"
                      aria-label="来源短线基准涨幅排序"
                    >
                      <button
                        className={`btn small${benchmarkDirection === "desc" ? " active" : ""}`}
                        aria-pressed={benchmarkDirection === "desc"}
                        onClick={() => setBenchmarkDirection("desc")}
                        disabled={!benchmarkRows.length}
                      >
                        <ArrowDown size={12} /> 高 → 低
                      </button>
                      <button
                        className={`btn small${benchmarkDirection === "asc" ? " active" : ""}`}
                        aria-pressed={benchmarkDirection === "asc"}
                        onClick={() => setBenchmarkDirection("asc")}
                        disabled={!benchmarkRows.length}
                      >
                        <ArrowUp size={12} /> 低 → 高
                      </button>
                    </div>
                  }
                  subtitle="服务端标签单独展示，不等同于本次候选或全市场。"
                >
                  <div className="auction-panel-body">
                    {benchmarkRows.length ? (
                      <div className="auction-benchmark-list">
                        {benchmarkRows.map((row) => (
                          <div key={row.instrument_id}>
                            <div>
                              {identity(row)}
                              <span
                                className={tone(auctionNumber(row.auction_pct))}
                              >
                                {percent(auctionNumber(row.auction_pct))}
                              </span>
                            </div>
                            <div className="auction-tags">
                              {noonLines(row.tags).map((tag, index) => (
                                <Badge key={index}>{tag}</Badge>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="auction-muted">
                        {benchmark.reason ||
                          "本次未取得可核验的同日基准，保持未知。"}
                      </p>
                    )}
                  </div>
                </Panel>
              </section>
            </div>
            {gaps.length > 0 && (
              <Panel
                title="本次缺口"
                subtitle="缺报价不填零，也不据此认定停牌。"
              >
                <div className="auction-panel-body">
                  <ul className="auction-lines">
                    {gaps.map((line, index) => (
                      <li key={index}>{line}</li>
                    ))}
                  </ul>
                </div>
              </Panel>
            )}
            <Panel>
              <details
                id="auction-close-review"
                tabIndex={-1}
                className="auction-close-review"
                open={closeOpen}
                onToggle={(event) => setCloseOpen(event.currentTarget.open)}
              >
                <summary>
                  收盘后，回头核对这份观察{" "}
                  <span>竞价至收盘价差 · 同日已发布行情</span>
                </summary>
                <div className="auction-panel-body">
                  <div className="auction-close-head">
                    <p>
                      盘后增量完成后自动刷新；只核对盘后结果，保留原竞价记录。
                    </p>
                    <button
                      className="btn small"
                      onClick={closeResult.reload}
                      disabled={closeResult.loading}
                    >
                      <RefreshCw size={12} />
                      读取盘后结果
                    </button>
                  </div>
                  {closeResult.loading && !closeReview && <Loading />}
                  {closeResult.error && (
                    <ErrorBox
                      message={closeResult.error}
                      retry={closeResult.reload}
                    />
                  )}
                  {closeReview &&
                    !["available", "partial"].includes(closeReview.state) && (
                      <p className="auction-muted">
                        {closeReview.reason || "等待同日盘后增量发布。"}
                      </p>
                    )}
                  {hasCloseGroups && (
                    <AuctionAssociationSummary
                      date={selected}
                      view={topicView}
                      closeReview={closeReview}
                      selectedKey={selectedAssociationKey}
                      onSelect={selectAssociation}
                    />
                  )}
                  {closeReview && hasCloseFacts && (
                    <>
                      {(!hasCloseGroups || !selectedAssociationIds) && (
                        <div
                          className="auction-close-metrics"
                          aria-label="全部冻结股票盘后汇总"
                        >
                          <span>全部样本</span>
                          <span
                            title={`相对原竞价来源前收；有效${integer(auctionNumber(closeReview.summary?.close_valid_count))}只`}
                          >
                            收盘均涨幅{" "}
                            <b
                              className={tone(
                                auctionNumber(
                                  closeReview.summary?.average_close_pct,
                                ),
                              )}
                            >
                              {percent(
                                auctionNumber(
                                  closeReview.summary?.average_close_pct,
                                ),
                              )}
                            </b>
                          </span>
                          <span
                            title={`相对原竞价价；可比${integer(auctionNumber(closeReview.summary?.valid))}只`}
                          >
                            竞价至收盘均变化{" "}
                            <b
                              className={tone(
                                auctionNumber(
                                  closeReview.summary
                                    ?.average_auction_to_close_pct,
                                ),
                              )}
                            >
                              {percent(
                                auctionNumber(
                                  closeReview.summary
                                    ?.average_auction_to_close_pct,
                                ),
                              )}
                            </b>
                          </span>
                          <span>
                            可比{" "}
                            <b>
                              {integer(
                                auctionNumber(closeReview.summary?.valid),
                              )}
                            </b>{" "}
                            /{" "}
                            {integer(
                              auctionNumber(closeReview.summary?.requested),
                            )}
                            只
                          </span>
                          <span>
                            收盘高于 / 低于竞价{" "}
                            <b className="up">
                              {integer(
                                auctionNumber(
                                  closeReview.summary?.above_auction,
                                ),
                              )}
                            </b>{" "}
                            /{" "}
                            <b className="down">
                              {integer(
                                auctionNumber(
                                  closeReview.summary?.below_auction,
                                ),
                              )}
                            </b>
                            只
                          </span>
                          <span>
                            价差中位数{" "}
                            <b
                              className={tone(
                                auctionNumber(
                                  closeReview.summary
                                    ?.median_auction_to_close_pct,
                                ),
                              )}
                            >
                              {percent(
                                auctionNumber(
                                  closeReview.summary
                                    ?.median_auction_to_close_pct,
                                ),
                              )}
                            </b>
                          </span>
                          <span>
                            开盘价不一致{" "}
                            {integer(
                              auctionNumber(closeReview.summary?.open_mismatch),
                            )}
                            只 · 缺失{" "}
                            {integer(
                              auctionNumber(closeReview.summary?.missing),
                            )}
                            只
                          </span>
                        </div>
                      )}
                      {!hasCloseGroups && (
                        <div className="auction-table-wrap">
                          <table className="auction-table">
                            <thead>
                              <tr>
                                <th>原冻结股票</th>
                                <th>盘前参照</th>
                                <th>竞价价（元）</th>
                                <th>开盘（元）</th>
                                <th>收盘（元）</th>
                                <th>竞价至收盘价差</th>
                                <th>核对状态</th>
                              </tr>
                            </thead>
                            <tbody>
                              {closeItems.map((row) => (
                                <tr key={row.instrument_id}>
                                  <td>{identity(row)}</td>
                                  <td>
                                    {percent(auctionNumber(row.auction_pct))}
                                  </td>
                                  <td>
                                    {num(auctionNumber(row.auction_price))}
                                  </td>
                                  <td>{num(auctionNumber(row.open))}</td>
                                  <td>{num(auctionNumber(row.close))}</td>
                                  <td
                                    className={tone(
                                      row.state === "observed"
                                        ? auctionNumber(
                                            row.auction_to_close_pct,
                                          )
                                        : null,
                                    )}
                                  >
                                    {row.state === "observed"
                                      ? percent(
                                          auctionNumber(
                                            row.auction_to_close_pct,
                                          ),
                                        )
                                      : "—"}
                                  </td>
                                  <td className="auction-row-state">
                                    {row.reason ||
                                      (row.state === "observed"
                                        ? "同日价格已核验"
                                        : "待核验")}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </>
                  )}
                  {hasCloseGroups && (
                    <AuctionCloseGroups
                      date={selected}
                      view={closeReview}
                      topicView={topicView}
                      selectedIds={selectedAssociationIds}
                      onStock={onStock}
                      chartDate={chartDate}
                    />
                  )}
                  <p className="auction-reading-note">
                    {closeReview?.notice ||
                      "只用同日已发布未复权价格核对，价差不等于真实成交收益或策略胜率。开盘与竞价价不一致的样本单列，不计入可比统计。"}
                  </p>
                </div>
              </details>
            </Panel>
          </>
        )}
        <section id="auction-automation" tabIndex={-1}>
          <AuctionAutomation
            value={automaticResult.data}
            loading={automaticResult.loading}
            error={automaticResult.error}
            reload={automaticResult.reload}
          />
        </section>
        <details className="auction-method">
          <summary>怎么看竞价结果</summary>
          <div className="auction-method-strip">
            <div>
              <span>01</span>
              <strong>准备关注</strong>
              <p>先看昨日留存复盘与原关注，再补自选及已核验强势样本。</p>
            </div>
            <div>
              <span>02</span>
              <strong>09:25:30自动核对</strong>
              <p>
                开启计划后由服务自动留存终态，比较高低开、竞价额与来源量比。
              </p>
            </div>
            <div>
              <span>03</span>
              <strong>带着证据观察</strong>
              <p>高开不等于持续走强；开盘后的变化另行核验。</p>
            </div>
          </div>
        </details>
        <p className="auction-boundary-note">
          观察边界：竞价只留存当日终态，不提供09:20—09:24的挂单轨迹，也不据未匹配量推断净流入或封单方向。页面只读取设置与结果；服务自动采集遵循已保存授权，不依赖浏览器保持打开。
        </p>
      </div>
    </AuctionPageNavigation>
  );
}
