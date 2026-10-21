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
  filterLineAlerts,
  lineAlertBasis,
  lineAlertInScope,
  lineAlertPrice,
  lineAlertView,
  lineAlertWatchlistState,
} from "./lineAlertPresentation";
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
      title={`${event.trade_date} · ${event.reason || view.title} · 查看日线核验依据`}
    >
      <span>
        {view.invalidated ? <CircleAlert size={12} /> : <BellRing size={12} />}
        {view.title}
      </span>
      <small>{event.trade_date}</small>
    </button>
  );
}

export function LineAlertEvidence({ event }: { event: Row }) {
  const view = lineAlertView(event);
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
            {event.trade_date} · {view.stateLabel}
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
          ["前收盘", event.previous_close],
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
      <p className="line-alert-evidence-note">
        按所示交易日日线保存的核验记录。触碰仅表示日内经过线价；收盘失守不计为回踩成功。补跑历史触发时，自选以实际加入日收盘为独立基准；移出自选不会停用划线提醒，同一事件复查不会重复加入。
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
      title={`${event.name || event.code || event.instrument_id} · 划线提醒`}
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
          sourceName="划线提醒"
          observedDate={event.trade_date}
        />
        <button className="btn primary small" onClick={() => onStock(event)}>
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
}: {
  data: Row | null;
  loading: boolean;
  error: string;
  asOf?: string;
  onReload: () => void;
  onOpen: (event: Row) => void;
  onStock: (event: Row) => void;
}) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState("all");
  const loaded = list(data).filter((event) => lineAlertInScope(event, asOf));
  const rows = filterLineAlerts(loaded, query, state, asOf);
  const unknowns = list(data?.unknowns);
  return (
    <div className="watchlist-line-alerts">
      <div className="line-alert-view-intro">
        <BellRing size={19} />
        <div>
          <strong>划线触发记录</strong>
          <p>包含搜索打开个股后设置的提醒；触发后移出自选，历史记录仍保留。</p>
        </div>
        <span>{data?.total ?? "—"} 条已保存</span>
      </div>
      <div className="line-alert-filter-bar">
        <div
          className="line-alert-state-filter"
          role="group"
          aria-label="筛选划线提醒状态"
        >
          {[
            ["all", "全部记录"],
            ["triggered", "触发提醒"],
            ["invalidated", "支撑失守"],
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
            aria-label="搜索划线提醒"
            placeholder="股票、代码、提醒条件"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <small>
          已加载最近 {loaded.length} / {data?.total ?? "—"} 条
        </small>
      </div>
      {error && <ErrorBox message={error} retry={onReload} />}
      {loading && !data ? (
        <Loading />
      ) : data && !loaded.length ? (
        <Empty
          title="尚无已确认的划线触发"
          description="在个股 K 线上设置支撑或压力线并勾选提醒条件；盘后核验后在此保留触发与失守记录。"
        />
      ) : data ? (
        <Table
          rows={rows}
          rowKey="event_id"
          pageSize={20}
          columns={[
            { key: "trade_date", title: "触发交易日" },
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
                  sourceName="划线提醒"
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
            {data?.latest_check?.unknown_count ?? unknowns.length} 项待核验 ·
            查看缺少的依据
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
