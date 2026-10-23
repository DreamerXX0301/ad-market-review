import { useState } from "react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
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
import "../sector/MemberDialog.css";
import { SortableMarketTable } from "./MarketTable";
import { filterMarketMembers, topicMembersRequest } from "./marketPresentation";
import {
  MemberBoardCell,
  MemberReturnCell,
  MemberYearLimitCell,
  MemberRecentLimitCell,
} from "./MarketMemberMetrics";

export default function TopicMembersDialog({
  topic,
  date,
  onClose,
  onStock,
  onChart,
}: {
  topic: Row;
  date: string;
  onClose: () => void;
  onStock: (id: string, navigation?: StockNavigation) => void;
  onChart?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [board, setBoard] = useState("all");
  const path = topicMembersRequest(topic, date);
  const result = useApi<Row>(path);
  const data = result.data;
  const items = list(data).map((row) =>
    row.board === "unknown" ? { ...row, board: null } : row,
  );
  const filtered = filterMarketMembers(items, query, board);
  const snapshotDay =
    data?.membership_snapshot_day || data?.membership_observed_date;
  return (
    <Modal
      title={`${topic.name || "分类"} · 成分股`}
      onClose={onClose}
      wide
      escapeUnderStock
    >
      <div className="modal-body market-members-body">
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
          <Badge>{date} · 盘面日期</Badge>
          <span className="member-context-item">
            <small>已知成员</small>
            <strong>{data?.total ?? "—"} 只</strong>
          </span>
          {data?.quote_coverage && (
            <span className="member-context-item">
              <small>报价可用</small>
              <strong>
                {data.quote_coverage.known ?? "—"} /{" "}
                {data.quote_coverage.total ?? "—"}
              </strong>
            </span>
          )}
        </div>
        {data?.unavailable_reason ? (
          <Warning>{data.unavailable_reason}</Warning>
        ) : (
          data?.match_type === "cross_provider_name_proxy" && (
            <Warning>
              此处查看本地同名行业的同花顺口径成员，不能视为东财资金分类的原始成分股。
            </Warning>
          )
        )}
        <details className="market-members-details">
          <summary>数据口径与来源</summary>
          <div>
            <div className="market-members-summary">
              <span>
                成员观察日{" "}
                {data?.membership_observed_date ||
                  data?.topic?.observed_date ||
                  "待核验"}
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
              历史指标均截至 {date}
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
                ? "正在读取所选日成员…"
                : data
                  ? `已返回 ${items.length}${data.total != null ? ` / ${data.total}` : ""} 只 · 当前匹配 ${filtered.length} 只`
                  : "成员数量待核验"}
            </span>
          </div>
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
              (topic.topic_id || topic.topic_code || topic.name) +
              query +
              board
            }
            rows={filtered}
            defaultSort={{ key: "change_pct", order: "desc" }}
            sortScope={`仅对 ${date} 已留存成员和该日已返回报价排序，不使用今日成员回填历史；缺失报价不补0`}
            columns={[
              { key: "name" },
              {
                key: "board",
                title: "上市板块",
                render: (_value: unknown, row: Row) => (
                  <MemberBoardCell row={row} />
                ),
              },
              {
                key: "change_pct",
                title: "所选日涨幅",
                render: (value: unknown, row: Row) => (
                  <span
                    className={tone(value)}
                    title={
                      row.quote_available === false
                        ? "该日缺少可核验报价，未使用其它日报价替代"
                        : `报价日 ${row.quote_date || date}`
                    }
                  >
                    {percent(value)}
                  </span>
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
                render: (_value: unknown, row: Row) => (
                  <MemberYearLimitCell row={row} />
                ),
              },
              {
                key: "last_limit_up_date",
                title: "最近涨停 / 交易日距离",
                render: (_value: unknown, row: Row) => (
                  <MemberRecentLimitCell row={row} date={date} />
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
                      typeof value === "number" && Number.isFinite(value)
                        ? ""
                        : "muted"
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
                    observedDate={date}
                    compact
                  />
                ),
              },
            ]}
            onStock={onStock}
            onStockNavigate={(id, orderedRows) =>
              onStock(
                id,
                createStockNavigation(orderedRows, {
                  source: "members",
                  scopeKey: JSON.stringify({
                    topic: topic.topic_code || topic.topic_id || topic.name,
                    date,
                    query,
                    board,
                  }),
                  label: `${topic.name || "行业与题材"} · 当前成员列表`,
                  asOf: date,
                }),
              )
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
      <div className="modal-footer">
        <span className="muted">
          默认按所选日涨幅降序；点击股票查看该日截止K线。
        </span>
        <button className="btn" onClick={onClose}>
          关闭
        </button>
      </div>
    </Modal>
  );
}
