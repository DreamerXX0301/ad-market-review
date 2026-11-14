import { useState } from "react";
import {
  ArrowUpRight,
  BellRing,
  Check,
  CircleAlert,
  Search,
  ShieldCheck,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { list } from "../../lib/api";
import { Empty, ErrorBox, Loading, Modal, Table } from "../../components/UI";
import { dateTime, num } from "../../lib/format";
import {
  lineAlertBasis,
  lineAlertInScope,
  isAutomaticPriceAlert,
  lineAlertChecks,
  lineAlertPrice,
  lineAlertSource,
  lineAlertView,
  lineAlertWatchlistState,
} from "./lineAlertPresentation";
import { filterLineAlerts } from "./lineAlertFiltering";
import "./WatchlistLineAlerts.css";
import WatchlistButton from "./WatchlistButton";
import CopyStockCode from "../../components/CopyStockCode";

export function LineAlertBadge({
  event,
  asOf,
  onOpen,
}: {
  event?: Row | null;
  asOf?: string;
  onOpen: (event: Row) => void;
}) {
  if (!event || !lineAlertInScope(event, asOf))
    return <span className="muted">—</span>;
  const view = lineAlertView(event);
  return (
    <button
      className={`watchlist-line-latest ${view.tone}`}
      onClick={() => onOpen(event)}
      title={`${lineAlertSource(event)} · ${event.trade_date} · ${event.reason || view.title} · 查看日线核验依据`}
    >
      <span>
        {view.invalidated ? <CircleAlert size={12} /> : <BellRing size={12} />}
        {view.title}
      </span>
      <small>
        {lineAlertSource(event)} · {event.trade_date}
      </small>
    </button>
  );
}

export function LineAlertEvidence({ event }: { event: Row }) {
  const view = lineAlertView(event);
  const automatic = isAutomaticPriceAlert(event);
  const level = event.automatic_level;
  const volume = event.volume_evidence;
  return (
    <div className="line-alert-evidence">
      <div className={`line-alert-evidence-heading ${view.tone}`}>
        {view.invalidated ? (
          <CircleAlert size={23} />
        ) : (
          <ShieldCheck size={23} />
        )}
        <div>
          <strong>{view.title}</strong>
          <span>
            {lineAlertSource(event)} · {event.trade_date} · {view.stateLabel}
          </span>
        </div>
        <b>
          {num(lineAlertPrice(event), 4)}
          <small>判定线价</small>
        </b>
      </div>
      <p className="line-alert-reason">
        {event.reason || "核验说明尚未返回，请刷新已保存的记录。"}
      </p>
      <dl className="line-alert-price-evidence">
        {[
          [automatic ? "基准前收盘" : "前收盘", event.previous_close],
          ["开盘", event.open],
          ["最高", event.high],
          ["最低", event.low],
          ["收盘", event.close],
        ].map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{num(value, 4)}</dd>
          </div>
        ))}
      </dl>
      {automatic ? (
        <div className="line-alert-automatic-evidence">
          <strong>触发前已确定的监控基准</strong>
          <p>
            基准日期 {event.level_date || "待核验"} · 判定价{" "}
            {num(lineAlertPrice(event), 4)} · {lineAlertBasis(event)}
          </p>
          {level && (
            <p>
              {level.source === "breakout_retest_reference"
                ? "原突破位回踩参考"
                : "确认高低点参考"}
              {level.formed_at && ` · 形成于 ${level.formed_at}`}
              {level.confirmed_on && ` · 确认于 ${level.confirmed_on}`}
            </p>
          )}
          {typeof level?.buffer_atr === "number" && (
            <p>
              结构基准 {num(level.base_price, 4)} · 波动缓冲{" "}
              {num(level.buffer_atr)} 倍ATR14（{num(level.buffer_value, 4)}）
              {level.atr_reference_date &&
                ` · 截至 ${level.atr_reference_date}`}
            </p>
          )}
          {event.rule === "close_breakout" && (
            <p>
              突破日量能 {num(volume?.ratio)} 倍前20日均量
              {volume?.mean_start &&
                volume?.mean_end &&
                ` · ${volume.mean_start} 至 ${volume.mean_end}，不含突破当日`}
              {volume?.reason && ` · ${volume.reason}`}
            </p>
          )}
          {typeof event.raw_previous_close === "number" &&
            event.raw_previous_close !== event.previous_close && (
              <p>
                本代前收盘修订值 {num(event.raw_previous_close, 4)}
                ；本次穿越判断保留事前监控基准收盘。
              </p>
            )}
          <details>
            <summary>算法与来源</summary>
            <p>
              算法{" "}
              {event.algorithm || event.level_evidence?.algorithm || "待核验"} ·
              配置版本 V{event.revision ?? event.annotation_revision ?? "—"}
            </p>
            <p>基准行情代际 {event.level_evidence?.snapshot_id || "待核验"}</p>
            <p>触发行情代际 {event.market_snapshot_id || "待核验"}</p>
          </details>
        </div>
      ) : (
        <div className="line-alert-evidence-meta">
          <span>
            原保存线价 {num(event.line_price, 4)} · {lineAlertBasis(event)}
          </span>
          <span>
            <Check size={14} />
            {lineAlertWatchlistState(event)}
          </span>
          {event.watchlist_added_business_date && (
            <span>实际加入自选日 {event.watchlist_added_business_date}</span>
          )}
          <span>
            判定价格锚点{" "}
            {event.comparison_evidence?.comparison_anchor || event.trade_date}
          </span>
          <span>划线版本 V{event.annotation_revision ?? "—"}</span>
          {event.annotation_label && (
            <span>划线名称 {event.annotation_label}</span>
          )}
          <span>记录于 {dateTime(event.created_at_ms)}</span>
        </div>
      )}
      <p className="line-alert-evidence-note">
        {automatic
          ? `按所示交易日收盘核验，使用前一市场交易日已保存的基准。后续价位重算不改写本记录，也不修改手动画线。仅提醒，不自动入池或交易。记录于 ${dateTime(event.created_at_ms)}。`
          : "按所示交易日日线保存的核验记录。触碰仅表示日内经过线价；收盘失守不计为回踩成功。补跑历史触发时，自选以实际加入日收盘为独立基准；移出自选不会停用划线提醒，同一事件复查不会重复加入。"}
      </p>
    </div>
  );
}

export function LineAlertDialog({
  event,
  onClose,
  onStock,
}: {
  event: Row;
  onClose: () => void;
  onStock: (event: Row) => void;
}) {
  return (
    <Modal
      title={`${event.name || event.code || event.instrument_id} · ${event.source_kind === "automatic_price_volume" ? "自动量价提醒" : "划线提醒"}`}
      onClose={onClose}
      escapeUnderStock
    >
      <LineAlertEvidence event={event} />
      <div className="line-alert-dialog-actions">
        <CopyStockCode code={event.code} instrumentId={event.instrument_id} />
        <WatchlistButton
          instrumentId={event.instrument_id}
          sourceKind="stock"
          sourceId={event.annotation_id}
          sourceName={
            event.source_kind === "automatic_price_volume"
              ? "自动量价提醒"
              : "划线提醒"
          }
          observedDate={event.trade_date}
        />
        <button
          className="btn primary small"
          onClick={() => {
            onClose();
            onStock(event);
          }}
        >
          查看当日 K 线<ArrowUpRight size={14} />
        </button>
      </div>
    </Modal>
  );
}

export default function WatchlistLineAlerts({
  data,
  loading,
  error,
  asOf,
  onReload,
  onOpen,
  onStock,
  scope = "all",
  onScopeChange,
  onLoadMore,
}: {
  data: Row | null;
  loading: boolean;
  error: string;
  asOf?: string;
  onReload: () => void;
  onOpen: (event: Row) => void;
  onStock: (event: Row) => void;
  scope?: string;
  onScopeChange?: (scope: string) => void;
  onLoadMore?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState("all");
  const loaded = list(data).filter((event) => lineAlertInScope(event, asOf));
  const rows = filterLineAlerts(loaded, query, state, asOf);
  const unknowns = list(data?.unknowns);
  const unknownCount = lineAlertChecks(data).reduce(
    (count, item) => count + (Number(item.check.unknown_count) || 0),
    0,
  );
  return (
    <div className="watchlist-line-alerts">
      <div className="line-alert-view-intro">
        <BellRing size={19} />
        <div>
          <strong>
            {scope === "today"
              ? "今日行情提醒"
              : scope === "history"
                ? "历史行情提醒"
                : "行情触发记录"}
          </strong>
          <p>
            手动画线按原规则监控和到期清理；自动量价按个股设置独立检查，旧触发依据保留。移出自选不会停用提醒。
          </p>
        </div>
        <span>{data?.total ?? "—"} 条已保存</span>
      </div>
      {onScopeChange && (
        <div
          className="line-alert-period-tabs"
          role="group"
          aria-label="提醒日期范围"
        >
          <button
            className={scope === "today" ? "selected" : ""}
            aria-pressed={scope === "today"}
            onClick={() => onScopeChange("today")}
          >
            今日 <b>{data?.today_total ?? "—"}</b>
            <small>{data?.business_date || ""}</small>
          </button>
          <button
            className={scope === "history" ? "selected" : ""}
            aria-pressed={scope === "history"}
            onClick={() => onScopeChange("history")}
          >
            历史 <b>{data?.history_total ?? "—"}</b>
          </button>
          <span>
            今日按最新已发布业务日；历史不含今日，回看只读已保存记录。
          </span>
        </div>
      )}
      <div className="line-alert-filter-bar">
        <div
          className="line-alert-state-filter"
          role="group"
          aria-label="筛选行情提醒状态"
        >
          {[
            ["all", "全部记录"],
            ["triggered", "触发提醒"],
            ["invalidated", "支撑失守 / 止损跌破"],
          ].map(([value, label]) => (
            <button
              key={value}
              aria-pressed={state === value}
              className={state === value ? "selected" : ""}
              onClick={() => setState(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="line-alert-search">
          <Search size={15} />
          <input
            aria-label="搜索行情提醒"
            placeholder="名称、代码、拼音、提醒条件"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <small>
          已加载最近 {loaded.length} / {data?.total ?? "—"} 条
        </small>
        {onLoadMore && data?.total > loaded.length && (
          <button className="btn small" disabled={loading} onClick={onLoadMore}>
            再加载100条
          </button>
        )}
      </div>
      {error && <ErrorBox message={error} retry={onReload} />}
      {loading && !data ? (
        <Loading />
      ) : data && !loaded.length ? (
        <Empty
          title={
            scope === "today"
              ? "今日暂无已确认的行情提醒"
              : "尚无已确认的行情提醒"
          }
          description="在个股日K启用自动量价提醒，或为手动画线勾选提醒条件；盘后核验后在此保留触发与风险记录。"
        />
      ) : data ? (
        <Table
          rows={rows}
          rowKey="event_id"
          pageSize={20}
          columns={[
            { key: "trade_date", title: "触发交易日" },
            {
              key: "source_kind",
              title: "来源",
              sortValue: lineAlertSource,
              render: (_value, event) => (
                <span
                  className={`line-alert-source ${isAutomaticPriceAlert(event) ? "automatic" : "manual"}`}
                >
                  {lineAlertSource(event)}
                </span>
              ),
            },
            {
              key: "name",
              title: "股票",
              render: (_value, event) => (
                <div className="line-alert-stock">
                  <button className="link" onClick={() => onStock(event)}>
                    {event.name || event.code || event.instrument_id}
                  </button>
                  <CopyStockCode
                    code={event.code}
                    instrumentId={event.instrument_id}
                  />
                </div>
              ),
            },
            {
              key: "rule",
              title: "提醒条件",
              sortValue: (event) => lineAlertView(event).title,
              render: (_value, event) => (
                <span
                  className={`line-alert-rule ${lineAlertView(event).tone}`}
                >
                  {lineAlertView(event).title}
                </span>
              ),
            },
            {
              key: "line_price",
              title: "判定线价",
              sortValue: lineAlertPrice,
              render: (_value, event) => num(lineAlertPrice(event), 4),
            },
            {
              key: "close",
              title: "当日收盘",
              render: (value) => num(value, 4),
            },
            {
              key: "watchlist_add_state",
              title: "自动入池",
              render: (_value, event) => (
                <span className="line-alert-auto-state">
                  {lineAlertWatchlistState(event)}
                </span>
              ),
            },
            {
              key: "actions",
              title: "自选",
              sortable: false,
              render: (_value, event) => (
                <WatchlistButton
                  compact
                  instrumentId={event.instrument_id}
                  sourceKind="stock"
                  sourceId={event.annotation_id}
                  sourceName={
                    isAutomaticPriceAlert(event) ? "自动量价提醒" : "划线提醒"
                  }
                  observedDate={event.trade_date}
                />
              ),
            },
            {
              key: "reason",
              title: "核验依据",
              sortable: false,
              render: (value, event) => (
                <div className="line-alert-table-evidence">
                  <span title={value}>{value || "—"}</span>
                  <button className="link" onClick={() => onOpen(event)}>
                    查看详情
                  </button>
                </div>
              ),
            },
          ]}
          emptyTitle="没有匹配的提醒"
          emptyDescription="可更换状态或搜索词；筛选范围为当前已加载的记录。"
        />
      ) : null}
      {unknowns.length > 0 && (
        <details className="line-alert-unknowns">
          <summary>
            {Math.max(unknownCount, unknowns.length)} 项待核验 · 查看缺少的依据
          </summary>
          <ul>
            {unknowns.map((item, index) => (
              <li key={`${item.annotation_id}:${index}`}>
                <strong>{item.name || item.instrument_id}</strong>
                <span>{item.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
