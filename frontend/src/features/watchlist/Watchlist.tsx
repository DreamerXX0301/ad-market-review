import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BellRing,
  Layers3,
  RefreshCw,
  Search,
  Star,
  Target,
  X,
} from "lucide-react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Badge, ErrorBox, Loading, Panel, Table } from "../../components/UI";
import { dateTime, num, percent, tone } from "../../lib/format";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import WatchlistButton from "./WatchlistButton";
import { WATCHLIST_CHANGED } from "./watchlistStore";
import DatePicker from "../../components/DatePicker";
import { activityPresentation } from "../../components/taskActivityPresentation";
import WatchlistLineAlerts, {
  LineAlertBadge,
  LineAlertDialog,
} from "./WatchlistLineAlerts";
import { useLineAlerts } from "./useLineAlerts";
import {
  watchlistEventBaseline,
  watchlistSourceLabel,
  filterWatchlistRows,
  watchlistPerformance,
} from "./watchlistPresentation";
import "./Watchlist.css";
import HotBrowseFilter, {
  HotBrowseBadge,
  useHotBrowseFilter,
} from "../../components/HotBrowseFilter";

function MembershipChips({
  tags,
  industries,
}: {
  tags: Row[];
  industries: Row[];
}) {
  const concepts = tags.filter((tag) => tag.kind !== "industry");
  const visible = [...industries.slice(0, 1), ...concepts.slice(0, 2)];
  const hidden = [...industries.slice(1), ...concepts.slice(2)];
  if (!visible.length)
    return (
      <span
        className="watchlist-membership-unknown"
        title="本地暂无已知正归属；不代表不属于任何分类"
      >
        暂无已知归属
      </span>
    );
  const chip = (tag: Row) => (
    <span
      className="watchlist-classification-chip"
      data-kind={tag.kind === "industry" ? "industry" : "concept"}
      key={tag.topic_id || tag.name}
      title={
        tag.source_observed_date
          ? `来源采集日 ${tag.source_observed_date} · 当前研究归属`
          : "当前本地研究归属；不是历史成员证明"
      }
    >
      {tag.name}
    </span>
  );
  return (
    <div className="watchlist-memberships">
      <div className="watchlist-membership-chips">{visible.map(chip)}</div>
      {hidden.length > 0 && (
        <details>
          <summary>另 {hidden.length} 项</summary>
          <div className="watchlist-membership-chips">{hidden.map(chip)}</div>
        </details>
      )}
    </div>
  );
}

export default function Watchlist({
  onStock,
}: {
  onStock: (id: string, navigation?: StockNavigation) => void;
}) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [view, setView] = useState("stocks");
  const [alertDate, setAlertDate] = useState("");
  const [selectedAlert, setSelectedAlert] = useState<Row | null>(null);
  const result = useApi<Row>("/watchlist");
  const day = result.data?.business_date;
  const dayCaption =
    day ||
    (result.loading ? "读取中…" : result.error ? "暂未读取" : "待初始化");
  const alertAsOf = alertDate && (!day || alertDate <= day) ? alertDate : day;
  const alerts = useLineAlerts(alertAsOf);
  const checking = alerts.task ? activityPresentation(alerts.task) : null;
  const lastCheck = alerts.result.data?.latest_check;
  const staleCheck =
    !!lastCheck && (lastCheck.stale === true || lastCheck.is_current === false);
  const openAlertStock = (event: Row) =>
    onStock(
      event.instrument_id,
      createStockNavigation([event], {
        source: "watchlist",
        scopeKey: `line-alert:${event.event_id}`,
        label: "划线提醒",
        asOf: event.trade_date,
      }),
    );
  useEffect(() => {
    const refresh = () => result.reload();
    const onRoute = (event: Event) => {
      if ((event as CustomEvent).detail === "watchlist") refresh();
    };
    window.addEventListener(WATCHLIST_CHANGED, refresh);
    window.addEventListener("workbench-route", onRoute);
    return () => {
      window.removeEventListener(WATCHLIST_CHANGED, refresh);
      window.removeEventListener("workbench-route", onRoute);
    };
  }, [result.reload]);
  useEffect(() => {
    const closeOnLeave = () => {
      if (location.hash.slice(1).split("?")[0] !== "watchlist")
        setSelectedAlert(null);
    };
    window.addEventListener("hashchange", closeOnLeave);
    return () => window.removeEventListener("hashchange", closeOnLeave);
  }, []);
  const allRows = list(result.data);
  const sourceRows = filterWatchlistRows(allRows, query, source);
  const hotFilter = useHotBrowseFilter(
    sourceRows,
    result.data?.hot_filter_scope,
    view === "stocks",
  );
  const rows = hotFilter.rows;
  return (
    <div className="watchlist-page">
      <div className="watchlist-intro">
        <span className="watchlist-intro-icon">
          <Star size={24} />
        </span>
        <div>
          <h2>
            我的自选池 <Badge>{result.data?.total ?? "—"} 只</Badge>
          </h2>
          <p>集中查看收藏股票、独立加入基准，以及支撑与压力线的盘后提醒。</p>
        </div>
        <span className="muted">行情日 {dayCaption}</span>
      </div>
      <Panel
        className="watchlist-panel"
        title="我的关注"
        subtitle="自选与划线提醒独立保存 · 移出自选不会停用划线提醒"
        actions={
          <>
            <button
              className="btn primary small"
              onClick={() => void alerts.evaluate()}
              disabled={alerts.busy || !day}
            >
              <BellRing size={15} className={alerts.busy ? "pulse" : ""} />
              {alerts.pending
                ? "正在提交…"
                : alerts.busy
                  ? checking?.statusLabel || "检查进行中"
                  : "检查划线提醒"}
            </button>
            <button
              className="btn small"
              onClick={() => {
                result.reload();
                if (hotFilter.enabled) hotFilter.result.reload();
                alerts.result.reload();
              }}
            >
              <RefreshCw size={15} />
              刷新列表
            </button>
          </>
        }
      >
        <div className="watchlist-view-bar">
          <div
            className="watchlist-view-tabs"
            role="group"
            aria-label="自选池视图"
          >
            <button
              aria-pressed={view === "stocks"}
              className={view === "stocks" ? "selected" : ""}
              onClick={() => setView("stocks")}
            >
              <Star size={15} />
              自选股票<b>{result.data?.total ?? "—"}</b>
            </button>
            <button
              aria-pressed={view === "alerts"}
              className={view === "alerts" ? "selected" : ""}
              onClick={() => setView("alerts")}
            >
              <BellRing size={15} />
              划线提醒<b>{alerts.result.data?.total ?? "—"}</b>
            </button>
          </div>
          {view === "alerts" && (
            <label className="watchlist-alert-date">
              截至日期
              <DatePicker
                compact
                value={alertAsOf || ""}
                max={day}
                onChange={setAlertDate}
                ariaLabel="划线提醒截至日期"
                title="选择提醒观察日"
                help="只显示该日及以前保存的触发，不会重新运行历史核验。"
                shortcut={day ? { label: "最新行情日", date: day } : undefined}
              />
            </label>
          )}
        </div>
        <div className="watchlist-line-check" role="status">
          <span>
            启用提醒 <strong>{alerts.result.data?.rules_total ?? "—"}</strong>{" "}
            条规则
          </span>
          {alerts.busy && checking ? (
            <span className="watchlist-line-check-progress">
              <BellRing size={13} />
              {checking.statusLabel} · {checking.stage} ·{" "}
              {checking.progressLabel}
            </span>
          ) : lastCheck ? (
            <span
              className={staleCheck ? "watchlist-line-check-stale" : ""}
              title={lastCheck.stale_reason || undefined}
            >
              {staleCheck ? "需更新核验 · 上次" : "最近核验"}{" "}
              {lastCheck.checked_rules} 条 · 触发 {lastCheck.trigger_count} ·
              待核验 {lastCheck.unknown_count}
            </span>
          ) : (
            <span>按已发布行情日线核验，结果持久保存</span>
          )}
          <small>
            {lastCheck
              ? `核验于 ${dateTime(lastCheck.checked_at_ms)}`
              : `手动检查使用最新行情日 ${dayCaption}`}
          </small>
        </div>
        {alerts.error && (
          <ErrorBox
            message={alerts.error}
            retry={() => void alerts.evaluate()}
          />
        )}
        {alerts.task?.status === "failed" && (
          <ErrorBox
            message={
              alerts.task.error ||
              alerts.task.error_message ||
              "划线提醒检查失败，已有记录仍保留。"
            }
          />
        )}
        <div hidden={view !== "stocks"}>
          {alerts.result.error && (
            <ErrorBox
              message={`划线提醒摘要暂未更新：${alerts.result.error}`}
              retry={alerts.result.reload}
            />
          )}
          {allRows.length > 0 && (
            <div className="watchlist-toolbar">
              <div className="watchlist-search">
                <Search size={17} aria-hidden="true" />
                <input
                  aria-label="搜索自选股票"
                  placeholder="搜索股票、代码、来源策略或行业题材"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                {query && (
                  <button
                    className="icon-btn"
                    aria-label="清空自选搜索"
                    onClick={() => setQuery("")}
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
              <select
                className="watchlist-source-filter"
                aria-label="按自选加入来源筛选"
                value={source}
                onChange={(event) => setSource(event.target.value)}
              >
                <option value="all">全部来源</option>
                <option value="tracking">策略跟踪</option>
                <option value="sector">行业与题材</option>
                <option value="stock">个股详情 / 划线提醒</option>
              </select>
              <span className="watchlist-result-count">
                {query.trim() || source !== "all" || hotFilter.enabled ? (
                  <>
                    匹配 <strong>{rows.length}</strong> / {allRows.length} 只
                  </>
                ) : (
                  <>
                    共 <strong>{allRows.length}</strong> 只自选
                  </>
                )}
              </span>
            </div>
          )}
          {view === "stocks" && (
            <HotBrowseFilter model={hotFilter} onRefreshList={result.reload} />
          )}
          {result.error && (
            <ErrorBox message={result.error} retry={result.reload} />
          )}
          {result.loading && !result.data ? (
            <Loading />
          ) : result.data && allRows.length === 0 ? (
            <div className="watchlist-empty">
              <div className="watchlist-empty-heading">
                <span className="watchlist-empty-icon">
                  <Star size={23} />
                </span>
                <div>
                  <h3>把值得关注的股票，放在这里</h3>
                  <p>在股票旁点击星标收藏，集中查看行情与原跟踪基准。</p>
                </div>
              </div>
              <div className="watchlist-empty-actions">
                <a href="#tracking">
                  <Target size={17} />
                  <span>从策略跟踪挑选</span>
                  <ArrowUpRight size={15} />
                </a>
                <a href="#sectors">
                  <Layers3 size={17} />
                  <span>浏览行业与题材</span>
                  <ArrowUpRight size={15} />
                </a>
              </div>
              <p className="watchlist-empty-note">
                也可从顶部搜索打开个股详情后收藏。自选独立保存，移除不会结束策略跟踪。
              </p>
            </div>
          ) : result.data ? (
            <Table
              rows={rows}
              pageSize={25}
              onStock={onStock}
              onStockNavigate={(id, orderedRows) =>
                onStock(
                  id,
                  createStockNavigation(
                    orderedRows.map((row) => ({
                      ...row,
                      eventBaseline: watchlistEventBaseline(row),
                    })),
                    {
                      source: "watchlist",
                      scopeKey: JSON.stringify({
                        source: "personal-watchlist",
                        query,
                        sourceFilter: source,
                        hot: hotFilter.enabled
                          ? {
                              config: hotFilter.applied,
                              mode: hotFilter.mode,
                              includeUnknown: hotFilter.includeUnknown,
                              scope: result.data?.hot_filter_scope,
                            }
                          : null,
                      }),
                      label: "我的自选",
                      asOf: day,
                    },
                  ),
                )
              }
              columns={[
                { key: "name", title: "股票" },
                {
                  key: "latest_line_alert",
                  title: "最近划线触发",
                  sortValue: (row) => row.latest_line_alert?.trade_date || null,
                  render: (_value, row) => (
                    <LineAlertBadge
                      event={row.latest_line_alert}
                      asOf={day}
                      onOpen={setSelectedAlert}
                    />
                  ),
                },
                {
                  key: "today_change_pct",
                  title: "今日涨幅",
                  render: (value) => (
                    <strong className={tone(value)}>{percent(value)}</strong>
                  ),
                },
                {
                  key: "watchlist_change_pct",
                  title: "加入后涨幅",
                  render: (_value, row) => {
                    const performance = watchlistPerformance(row);
                    return (
                      <div
                        className="watchlist-performance"
                        title={
                          performance.reason ||
                          `首次加入自选日 ${performance.entryDate}，冻结收盘 ${num(performance.entryPrice)} 元 → ${row.quote_date || day} 收盘。独立于原跟踪事件基准，重复收藏不重置。`
                        }
                      >
                        <strong className={tone(performance.value)}>
                          {percent(performance.value)}
                        </strong>
                        <small>
                          {performance.value === null
                            ? "待核验"
                            : `自选基准 ${num(performance.entryPrice)}`}
                        </small>
                      </div>
                    );
                  },
                },
                {
                  key: "latest_price",
                  title: "收盘价",
                  render: (value) => num(value),
                },
                {
                  key: "source_name",
                  title: "加入来源",
                  render: (value, row) => (
                    <div className="watchlist-source">
                      <strong>{value}</strong>
                      <small>
                        {watchlistSourceLabel[row.source_kind] || "手动添加"}
                      </small>
                    </div>
                  ),
                },
                {
                  key: "current_topics",
                  title: "行业 / 题材",
                  sortValue: (row) =>
                    row.current_topics
                      ?.map((tag: Row) => tag.name)
                      .join("、") || null,
                  render: (value, row) => (
                    <div>
                      <MembershipChips
                        tags={Array.isArray(value) ? value : []}
                        industries={
                          Array.isArray(row.current_industries)
                            ? row.current_industries
                            : []
                        }
                      />
                      <HotBrowseBadge row={row} />
                    </div>
                  ),
                },
                { key: "added_business_date", title: "加入自选日" },
                {
                  key: "tracking_entry_date",
                  title: "原跟踪入池日",
                  render: (value) => value || <span className="muted">—</span>,
                },
                {
                  key: "event_close",
                  title: "事件日收盘价",
                  render: (value, row) => (
                    <span
                      title={
                        row.event_baseline_reason ||
                        `冻结入池日 ${row.event_date} · ${row.event_price_basis === "none" ? "未复权原价" : "原跟踪价格口径"}`
                      }
                    >
                      {num(value)}
                    </span>
                  ),
                },
                {
                  key: "observed_date",
                  title: "来源观察日",
                  render: (value) => value || "—",
                },
                {
                  key: "actions",
                  title: "自选",
                  sortable: false,
                  render: (_, row) => (
                    <WatchlistButton instrumentId={row.instrument_id} compact />
                  ),
                },
              ]}
              emptyTitle="没有匹配的自选"
              emptyDescription="可调整搜索词或来源筛选；搜索同时匹配全部已知行业和题材名称。"
            />
          ) : null}
          {allRows.length > 0 && (
            <div className="panel-footnote">
              加入后涨幅从首次加入自选日的冻结收盘计算，重复收藏不重置；原跟踪事件价独立保留。价格缺失或公司行动不可比时待核验。当前行业与题材是研究归属，采集日见标签提示；人工收藏没有原跟踪事件基准时显示“—”。
            </div>
          )}
        </div>
        {view === "alerts" && (
          <WatchlistLineAlerts
            data={alerts.result.data}
            loading={alerts.result.loading}
            error={alerts.result.error}
            asOf={alertAsOf}
            onReload={alerts.result.reload}
            onOpen={setSelectedAlert}
            onStock={openAlertStock}
          />
        )}
      </Panel>
      {selectedAlert && (
        <LineAlertDialog
          event={selectedAlert}
          onClose={() => setSelectedAlert(null)}
          onStock={openAlertStock}
        />
      )}
    </div>
  );
}
