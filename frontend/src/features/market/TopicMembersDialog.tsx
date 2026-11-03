import { useState } from "react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import type { Column } from "../../components/UI";
import {
  createStockNavigation,
  type StockNavigation,
} from "../../components/stockNavigation";
import {
  Badge,
  ErrorBox,
  Loading,
  Modal,
  Warning,
  Warnings,
} from "../../components/UI";
import { num, percent, tone } from "../../lib/format";
import MarketDataCoverage from "./MarketDataCoverage";
import WatchlistButton from "../watchlist/WatchlistButton";
import { StockEventBadges } from "./BoardTags";
import MemberDailyLimitBadge from "./MemberDailyLimitBadge";
import "../sector/MemberDialog.css";
import "./TopicMembersContent.css";
import { SortableMarketTable } from "./MarketTable";
import { filterMarketMembers } from "./marketPresentation";
import {
  latestMemberTopicCode,
  memberQuoteDate,
  topicMemberScopeRequest,
  type TopicMemberScope,
} from "./topicMemberScope";
import {
  MemberBoardCell,
  MemberReturnCell,
  MemberYearLimitCell,
  MemberRecentLimitCell,
} from "./MarketMemberMetrics";

type TopicMembersContentProps = {
  topic: Row;
  date: string;
  onStock: (id: string, navigation?: StockNavigation) => void;
  onChart?: () => void;
  compact?: boolean;
  /** Only the modal wrapper supplies a close action and its existing footer. */
  onClose?: () => void;
};

const compactMemberColumns = new Set([
  "name",
  "board",
  "change_pct",
  "change_30d_pct",
  "limit_up_ytd",
]);

/** One read model serves the existing dialog and the inline sector explorer. */
export function TopicMembersContent({
  topic,
  date,
  onStock,
  onChart,
  compact = false,
  onClose,
}: TopicMembersContentProps) {
  const [showAllMetrics, setShowAllMetrics] = useState(false);
  const [query, setQuery] = useState("");
  const [board, setBoard] = useState("all");
  const [scope, setScope] = useState<TopicMemberScope>("historical");
  const path = topicMemberScopeRequest(topic, date, scope);
  const result = useApi<Row>(path);
  const data = result.data;
  const quoteDate = memberQuoteDate(data, date, scope);
  const currentCode = latestMemberTopicCode(topic);
  const membershipDate =
    data?.membership_as_of || data?.membership_observed_date;
  const items = list(data).map((row) =>
    row.board === "unknown" ? { ...row, board: null } : row,
  );
  const filtered = filterMarketMembers(items, query, board);
  const snapshotDay =
    data?.membership_snapshot_day || data?.membership_observed_date;
  const columns: Column[] = [
    { key: "name" },
    {
      key: "board",
      title: "上市板块",
      render: (_value: unknown, row: Row) => <MemberBoardCell row={row} />,
    },
    {
      key: "change_pct",
      title: scope === "latest" ? "行情日涨幅" : "所选日涨幅",
      render: (value: unknown, row: Row) => (
        <div className="market-member-day-change">
          <span
            className={tone(value)}
            title={
              row.quote_available === false
                ? "该日缺少可核验报价，未使用其它日报价替代"
                : `报价日 ${row.quote_date || quoteDate || "未知"}`
            }
          >
            {percent(value)}
          </span>
          <MemberDailyLimitBadge row={row} date={quoteDate || date} />
          {quoteDate &&
            row.quote_available === true &&
            row.quote_date === quoteDate && (
              <StockEventBadges
                row={row}
                date={quoteDate}
                snapshotId={data?.snapshot_id}
              />
            )}
        </div>
      ),
    },
    {
      key: "change_30d_pct",
      title: "近30交易日涨幅",
      render: (_value: unknown, row: Row) => (
        <MemberReturnCell row={row} field="change_30d_pct" />
      ),
    },
    {
      key: "return_ytd_pct",
      title: "年内涨幅",
      render: (_value: unknown, row: Row) => (
        <MemberReturnCell row={row} field="return_ytd_pct" />
      ),
    },
    {
      key: "return_mtd_pct",
      title: "本月涨幅",
      render: (_value: unknown, row: Row) => (
        <MemberReturnCell row={row} field="return_mtd_pct" />
      ),
    },
    {
      key: "limit_up_ytd",
      title: "年内涨停次数",
      render: (_value: unknown, row: Row) => <MemberYearLimitCell row={row} />,
    },
    {
      key: "last_limit_up_date",
      title: "最近涨停 / 交易日距离",
      render: (_value: unknown, row: Row) => (
        <MemberRecentLimitCell row={row} date={quoteDate || date} />
      ),
    },
    { key: "close", title: "收盘价" },
    { key: "amount", title: "成交额" },
    {
      key: "turnover_rate",
      title: "换手率",
      render: (value: unknown, row: Row) => (
        <span
          className={
            typeof value === "number" && Number.isFinite(value) ? "" : "muted"
          }
          title={[
            row.turnover_rate_reason ||
              (value == null
                ? "该日换手率缺少可核验来源，未补0"
                : "所选盘面日换手率"),
            row.turnover_rate_source
              ? "来源 " + row.turnover_rate_source
              : null,
          ]
            .filter(Boolean)
            .join("；")}
        >
          {typeof value === "number" && Number.isFinite(value)
            ? num(value, 2) + "%"
            : "—"}
        </span>
      ),
    },
    { key: "quote_date", title: "报价日" },
    {
      key: "actions",
      title: "自选",
      sortable: false,
      render: (_value: unknown, row: Row) => (
        <WatchlistButton
          instrumentId={row.instrument_id}
          sourceKind="sector"
          sourceId={topic.topic_code || topic.topic_id}
          sourceName={topic.name}
          observedDate={quoteDate || undefined}
          compact
        />
      ),
    },
  ];
  const visibleColumns =
    compact && !showAllMetrics
      ? columns.filter((column) => compactMemberColumns.has(column.key))
      : columns;
  return (
    <>
      <div
        className={
          compact
            ? "market-members-body market-members-inline"
            : "modal-body market-members-body"
        }
      >
        {onChart && (
          <div
            className="segmented global-sector-tabs"
            role="tablist"
            aria-label="板块详情视图"
          >
            <button role="tab" aria-selected="false" onClick={onChart}>
              板块K线
            </button>
            <button role="tab" aria-selected="true" className="active">
              成分股
            </button>
          </div>
        )}
        <div className="market-members-summary member-dialog-context">
          <Badge>
            {scope === "latest" ? "当前最新成员" : `${date} · 历史成员档案`}
          </Badge>
          <span className="member-context-item">
            <small>成员采集日</small>
            <strong>{membershipDate || "未知"}</strong>
          </span>
          <span className="member-context-item">
            <small>行情日</small>
            <strong>{quoteDate || "未知"}</strong>
          </span>
          <span className="member-context-item">
            <small>已知成员</small>
            <strong>
              {data?.unavailable_reason ? "未知" : `${data?.total ?? "—"} 只`}
            </strong>
          </span>
          {data?.quote_coverage && (
            <span className="member-context-item">
              <small>报价可用</small>
              <strong>
                {data.quote_coverage.known ??
                  data.quote_coverage.quoted_count ??
                  data.quote_coverage.available_count ??
                  "—"}{" "}
                / {data.quote_coverage.total ?? "—"}
              </strong>
            </span>
          )}
        </div>
        {data?.unavailable_reason ? (
          <Warning>{data.unavailable_reason}</Warning>
        ) : (
          (data?.match_type || topic.membership_match_type) ===
            "cross_provider_name_proxy" && (
            <Warning>
              此处查看本地同名行业的同花顺口径成员，不能视为东财资金分类的原始成分股。
            </Warning>
          )
        )}
        {scope === "latest" && (
          <Warning>
            当前成员采集于 {membershipDate || "未知日期"}，股票行情截至{" "}
            {quoteDate || "未知日期"}。这是当前来源成员及本地人工调整，不代表{" "}
            {date} 的历史归属；不会据此补写历史档案。
          </Warning>
        )}
        <details className="market-members-details">
          <summary>数据口径与来源</summary>
          <div>
            <div className="market-members-summary">
              <span>
                成员观察日{" "}
                {membershipDate || data?.topic?.observed_date || "待核验"}
              </span>
              <span>归档日 {snapshotDay || "待核验"}</span>
              <Badge tone={data?.members_complete === true ? "good" : "warn"}>
                {data?.members_complete === true
                  ? "成员档案完整"
                  : "成员范围待核验"}
              </Badge>
            </div>
            {data?.membership_basis && (
              <p className="muted">{data.membership_basis}</p>
            )}
            <p className="muted">
              股票指标均截至 {quoteDate || "待核验行情日"}
              。年内从年初计算；本月为MTD、不是近30个交易日。指标描述股票本身，不代表过去已属于当前题材。年内涨停覆盖不足时只展示确认下界；无记录不代表未涨停。
            </p>
            <MarketDataCoverage
              coverage={data?.data_coverage}
              loading={result.loading}
            />
            <Warnings items={data?.warnings} />
          </div>
        </details>
        <div className="member-dialog-toolbar">
          <div className="result-toolbar member-dialog-search-row">
            <label className="member-dialog-field">
              <span>成员口径</span>
              <select
                aria-label="选择成员口径"
                value={scope}
                onChange={(event) => {
                  setScope(event.target.value as TopicMemberScope);
                  setQuery("");
                  setBoard("all");
                }}
              >
                <option value="historical">所选日历史成员</option>
                <option value="latest" disabled={!currentCode}>
                  当前最新成员{!currentCode ? " · 来源代码未核验" : ""}
                </option>
              </select>
            </label>
            <label className="member-dialog-field member-dialog-field-search">
              <span>搜索成分股</span>
              <input
                aria-label="搜索分类成分股"
                className="search-input"
                placeholder="搜索成员名称、代码…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <span className="muted">
              {result.loading
                ? "正在读取所选口径成员…"
                : data
                  ? data.unavailable_reason
                    ? "成员档案缺失，数量未知；可手动切换成员口径"
                    : `已返回 ${items.length}${data.total != null ? ` / ${data.total}` : ""} 只 · 当前匹配 ${filtered.length} 只`
                  : "成员数量待核验"}
            </span>
          </div>
          <div className={compact ? "member-inline-board-row" : undefined}>
            <div
              className="market-members-board-filter"
              role="group"
              aria-label="按上市板块筛选成分股"
            >
              {[
                ["all", "全部"],
                ["main", "主板"],
                ["chinext", "创业板"],
                ["star", "科创板"],
                ["beijing", "北交所"],
                ["unknown", "未知"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  className={board === value ? "active" : ""}
                  aria-pressed={board === value}
                  onClick={() => setBoard(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            {compact && (
              <label className="checkbox-label market-members-metric-toggle">
                <input
                  type="checkbox"
                  checked={showAllMetrics}
                  onChange={(event) => setShowAllMetrics(event.target.checked)}
                />
                完整指标
              </label>
            )}
          </div>
        </div>
        {!path ? (
          <Warning>此分类缺少稳定标识，无法核验所选日期成员。</Warning>
        ) : result.error ? (
          <ErrorBox message={result.error} retry={result.reload} />
        ) : result.loading ? (
          <Loading />
        ) : (
          <SortableMarketTable
            key={
              date +
              scope +
              (topic.topic_id || topic.topic_code || topic.name) +
              query +
              board +
              (compact && !showAllMetrics ? "compact" : "full")
            }
            rows={filtered}
            defaultSort={{ key: "change_pct", order: "desc" }}
            sortScope={
              scope === "latest"
                ? `当前成员按 ${quoteDate || "已核验行情日"} 报价排序；成员采集日独立标明，不表示过去归属`
                : `仅对 ${date} 已留存成员和该日已返回报价排序，不使用今日成员回填历史；缺失报价不补0`
            }
            columns={visibleColumns}
            onStock={quoteDate ? onStock : undefined}
            onStockNavigate={
              quoteDate
                ? (id, orderedRows) =>
                    onStock(
                      id,
                      createStockNavigation(orderedRows, {
                        source: "members",
                        scopeKey: JSON.stringify({
                          topic:
                            topic.topic_code || topic.topic_id || topic.name,
                          date,
                          scope,
                          query,
                          board,
                        }),
                        label: `${topic.name || "行业与题材"} · ${scope === "latest" ? "当前" : "历史"}成员列表`,
                        asOf: quoteDate,
                      }),
                    )
                : undefined
            }
            pageSize={20}
            emptyTitle={
              data?.unavailable_reason ? "所选日成员尚不可用" : "暂无可核验成员"
            }
            emptyDescription={
              data?.unavailable_reason ||
              "历史成员档案缺失时保持未知；不会改用今天的分类成员。"
            }
          />
        )}
      </div>
      {onClose && (
        <div className="modal-footer">
          <span className="muted">
            默认按行情日涨幅降序；点击股票查看 {quoteDate || "已核验行情日"}{" "}
            截止K线。
          </span>
          <button className="btn" onClick={onClose}>
            关闭
          </button>
        </div>
      )}
    </>
  );
}

export default function TopicMembersDialog({
  topic,
  date,
  onClose,
  onStock,
  onChart,
}: TopicMembersContentProps & { onClose: () => void }) {
  return (
    <Modal
      title={`${topic.name || "分类"} · 成分股`}
      onClose={onClose}
      wide
      escapeUnderStock
    >
      <TopicMembersContent
        topic={topic}
        date={date}
        onStock={onStock}
        onChart={onChart}
        onClose={onClose}
      />
    </Modal>
  );
}
