import { useState } from "react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { amount, num, percent, tone } from "../../lib/format";
import {
  Badge,
  ErrorBox,
  Loading,
  Modal,
  Table,
  Warning,
} from "../../components/UI";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import WatchlistButton from "../watchlist/WatchlistButton";
import "./MemberDialog.css";
import DatePicker from "../../components/DatePicker";
import {
  memberQuoteDate,
  sectorMemberNavigationRows,
  sectorMemberBoard,
  sectorMembersEndpoint,
} from "./sectorPresentation";
import {
  MemberBoardCell,
  MemberReturnCell,
  MemberYearLimitCell,
} from "../market/MarketMemberMetrics";
import {
  HistoryReturnCell,
  RecentLimitCell,
} from "../topics/MemberHistoryCells";

export type SectorMemberStockCallback = (
  id: string,
  asOf: string,
  navigation?: StockNavigation,
) => void;
export default function SectorMembersDialog({
  round,
  topic,
  date,
  businessDate,
  initialScope = "latest",
  onChart,
  onClose,
  onStock,
}: {
  round?: Row;
  topic?: Row;
  date: string;
  businessDate?: string;
  initialScope?: "latest" | "historical";
  onChart?: (date?: string) => void;
  onClose: () => void;
  onStock: SectorMemberStockCallback;
}) {
  const identity = round || topic || {};
  const [scope, setScope] = useState(initialScope),
    [asOf, setAsOf] = useState(date),
    [query, setQuery] = useState("");
  const result = useApi<Row>(
    sectorMembersEndpoint({
      roundId: round?.round_id,
      topicCode: topic?.topic_code,
      asOf,
      scope,
    }),
  );
  const data = result.data,
    quoteDate = memberQuoteDate(data);
  const rows = list(data).filter(
    (row) =>
      !query ||
      `${row.name} ${row.code} ${row.instrument_id}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <Modal
      title={`${identity.name} · 成分股`}
      onClose={onClose}
      wide
      escapeUnderStock
    >
      <div className="modal-body sector-members-body">
        {onChart && (
          <div
            className="segmented global-sector-tabs"
            role="tablist"
            aria-label="板块详情视图"
          >
            <button
              role="tab"
              aria-selected="false"
              onClick={() => onChart(asOf)}
            >
              板块K线
            </button>
            <button role="tab" aria-selected="true" className="active">
              成分股
            </button>
          </div>
        )}
        <div className="sector-members-context member-dialog-context">
          <Badge>
            {scope === "latest" ? "当前成员快照" : "指定日历史成员"}
          </Badge>
          <span className="member-context-item">
            <small>成员采集日</small>
            <strong>{data?.membership_as_of || "未知"}</strong>
          </span>
          <span className="member-context-item">
            <small>行情日</small>
            <strong>{quoteDate || "未知"}</strong>
          </span>
          <span className="member-context-item">
            <small>已知成员</small>
            <strong>{data?.total ?? list(data).length} 只</strong>
          </span>
        </div>
        <div className="filter-bar member-dialog-toolbar member-dialog-fields">
          <label className="member-dialog-field">
            <span>成员口径</span>
            <select
              value={scope}
              onChange={(e) => {
                setScope(e.target.value as "latest" | "historical");
                if (e.target.value === "latest") setAsOf(businessDate || date);
              }}
            >
              <option value="latest">当前最新成分</option>
              <option value="historical">观察日历史成分</option>
            </select>
          </label>
          <label className="member-dialog-field">
            <span>行情观察日</span>
            <DatePicker
              ariaLabel="成分股行情观察日"
              title="成分股行情观察日"
              help="选择日期后读取该日已有历史档案，缺档不会使用当前成员回填。"
              max={businessDate || date}
              disabled={scope === "latest"}
              value={asOf}
              onChange={setAsOf}
            />
          </label>
          <label className="member-dialog-field member-dialog-field-search">
            <span>搜索成分股</span>
            <input
              placeholder="股票名称 / 代码"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
        {scope === "latest" && (
          <p className="panel-footnote">
            当前成员按真实采集日展示；股票行情按上方行情日展示，两者不代表历史同日归属。展开成员不会创建个股跟踪。
          </p>
        )}
        {result.error && (
          <ErrorBox message={result.error} retry={result.reload} />
        )}
        {data?.unavailable_reason && (
          <Warning>{data.unavailable_reason}</Warning>
        )}
        {data && (
          <details className="market-data-details">
            <summary>数据口径与完整性</summary>
            <p>{data.membership_basis || data.membership_scope}</p>
            <p>
              成员档案{data.members_complete === true ? "完整" : "覆盖不足"}
              ；有报价{" "}
              {data.quote_coverage?.quoted_count ??
                data.quote_coverage?.available_count ??
                "未知"}
              。历史无档案时不会使用当前成员回填。
            </p>
            {(data.warnings || []).map((warning: any, index: number) => (
              <p key={index}>
                {typeof warning === "string"
                  ? warning
                  : warning.reason || warning.notice || "成员资料存在缺口"}
              </p>
            ))}
          </details>
        )}
        {result.loading ? (
          <Loading />
        ) : (
          <Table
            key={`${round?.round_id || topic?.topic_code}-${scope}-${asOf}-${query}`}
            rows={rows}
            pageSize={25}
            onStock={quoteDate ? (id) => onStock(id, quoteDate) : undefined}
            onStockNavigate={
              quoteDate
                ? (id, orderedRows) =>
                    onStock(
                      id,
                      quoteDate,
                      createStockNavigation(
                        sectorMemberNavigationRows(orderedRows),
                        {
                          source: "tracking",
                          scopeKey: `sector-members:${round?.round_id || topic?.topic_code}:${scope}:${asOf}`,
                          label: `${identity.name} · 成分股`,
                          asOf: quoteDate,
                        },
                      ),
                    )
                : undefined
            }
            columns={[
              { key: "name", title: "股票名称" },
              {
                key: "board_label",
                title: "上市板块",
                sortValue: (row) =>
                  row.board_label || row.board_name || row.board || null,
                render: (_, row) => (
                  <MemberBoardCell row={sectorMemberBoard(row)} />
                ),
              },
              {
                key: "change_pct",
                title: "行情日涨幅",
                render: (value) => (
                  <span className={tone(value)}>{percent(value)}</span>
                ),
              },
              { key: "close", title: "收盘价", render: (value) => num(value) },
              {
                key: "amount",
                title: "成交额",
                render: (value) => amount(value),
              },
              {
                key: "return_ytd_pct",
                title: "年内涨幅",
                render: (_, row) => (
                  <MemberReturnCell row={row} field="return_ytd_pct" />
                ),
              },
              {
                key: "return_mtd_pct",
                title: "本月涨幅",
                render: (_, row) => (
                  <MemberReturnCell row={row} field="return_mtd_pct" />
                ),
              },
              {
                key: "change_30d_pct",
                title: "近30交易日涨幅",
                render: (value, row) => (
                  <HistoryReturnCell
                    value={value}
                    reason={row.change_30d_reason}
                    start={row.change_30d_coverage?.start}
                    end={row.change_30d_coverage?.end}
                    priceBasis={row.change_30d_coverage?.price_basis}
                    missingSessions={row.change_30d_coverage?.missing_sessions}
                  />
                ),
              },
              {
                key: "limit_up_ytd",
                title: "年内涨停次数",
                render: (_, row) => <MemberYearLimitCell row={row} />,
              },
              {
                key: "last_limit_up_date",
                title: "最近涨停 / 距观察日",
                render: (_, row) => <RecentLimitCell row={row} />,
              },
              {
                key: "turnover_rate",
                title: "换手率",
                render: (value, row) => (
                  <span
                    title={
                      value == null
                        ? row.turnover_rate_reason || "当日换手率未核验"
                        : undefined
                    }
                  >
                    {percent(value)}
                  </span>
                ),
              },
              { key: "topics", title: "题材" },
              {
                key: "actions",
                title: "自选",
                sortable: false,
                render: (_, row) => (
                  <WatchlistButton
                    instrumentId={row.instrument_id}
                    sourceKind="sector"
                    sourceId={identity.topic_code || identity.topic_id}
                    sourceName={identity.name}
                    observedDate={quoteDate || undefined}
                    compact
                  />
                ),
              },
            ]}
          />
        )}{" "}
        {!quoteDate && data && (
          <p className="muted">行情日尚未核验，暂不能打开该日期的股票K线。</p>
        )}
      </div>
      <div className="modal-footer">
        <span className="muted">
          {rows.length} 只匹配成员 · 点击股票可连续查看K线，星标加入我的自选
        </span>
        <button className="btn" onClick={onClose}>
          关闭
        </button>
      </div>
    </Modal>
  );
}
