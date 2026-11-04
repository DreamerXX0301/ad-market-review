import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Clock3,
  RefreshCw,
  ArrowUpRight,
  Sparkles,
} from "lucide-react";
import { Empty, ErrorBox, Loading, Panel, Warning } from "../../components/UI";
import { post, requestKey, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { amount, integer, num, percent, tone } from "../../lib/format";
import {
  boardLabel,
  boardOf,
  filterRows,
  NOON_BOARD_OPTIONS,
} from "./noonBoardPresentation";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import IntradayAutomation from "./IntradayAutomation";
import AuctionHeightReference from "./AuctionHeightReference";
import "./IntradayObservation.css";

const activeStatus = (value: unknown) =>
  value === "queued" || value === "running";
const clock = (value: unknown) =>
  typeof value === "string"
    ? value.match(/T(\d\d:\d\d:\d\d)/)?.[1] || "—"
    : "—";
const delta = (value: number | null | undefined, unit: string) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${num(value)} ${unit}`;

const evidenceCount = (value: number | null | undefined, state: unknown) =>
  value == null ? "—" : `${state === "partial" ? "≥" : ""}${integer(value)}`;

type OpenStock = (
  id: string,
  context?: { slot: string; snapshotId?: string },
) => void;
type StockHistoryScope = { date: string; slot: string };
type MemberScope = StockHistoryScope & { snapshotId: string; topicCode: string };

const stockHistoryQuery = ({ date, slot }: StockHistoryScope, rows: Row[]) =>
  `/market/intraday/history?${new URLSearchParams({ date, slot, symbols: rows.map((row) => row.instrument_id).join(",") })}`;

function ThemeChips({
  row,
  showMissing = false,
}: {
  row: Row;
  showMissing?: boolean;
}) {
  const themes: Row[] =
    row.themes?.length > 0
      ? row.themes
      : ((row.board_names || []) as string[]).map((name) => ({ name }));
  if (!themes.length) {
    return showMissing ? (
      <small className="intraday-theme-missing">题材未核验</small>
    ) : null;
  }
  return (
    <span className="intraday-theme-chips">
      {themes.slice(0, 3).map((theme, i) => (
        <span
          key={`${theme.name}:${i}`}
          data-kind={theme.kind}
          title={
            theme.member_date ? `名单留存日 ${theme.member_date}` : undefined
          }
        >
          {theme.name}
        </span>
      ))}
      {themes.length > 3 && (
        <span
          title={themes
            .slice(3)
            .map((theme) => theme.name)
            .join("、")}
        >
          +{themes.length - 3}
        </span>
      )}
    </span>
  );
}

function MarketPulse({ pulse, interval }: { pulse: Row; interval?: Row }) {
  const breadth = pulse.breadth;
  const turnover = pulse.turnover;
  return (
    <section className="intraday-pulse-panel" aria-label="市场背景">
      <div className="intraday-market-pulse">
        {(pulse.indices || []).map((row: Row) => (
          <div key={row.instrument_id} className="intraday-metric">
            <span>{row.name}</span>
            <strong className={tone(row.change_pct)}>
              {percent(row.change_pct)}
            </strong>
            <small>
              {row.latest_price == null ? "点位未取得" : num(row.latest_price)}
            </small>
          </div>
        ))}
        {breadth && (
          <div className="intraday-metric">
            <span>上涨 / 下跌 / 平盘</span>
            <strong>
              <span className="up">{integer(breadth.up)}</span> /{" "}
              <span className="down">{integer(breadth.down)}</span> /{" "}
              {integer(breadth.flat)}
            </strong>
            <small>
              可比 {integer(breadth.valid)} / {integer(breadth.observed)} 只
            </small>
            {breadth.valid > 0 && (
              <div className="intraday-breadth-bar" aria-hidden="true">
                <i style={{ flexGrow: breadth.up }} data-kind="up" />
                <i style={{ flexGrow: breadth.down }} data-kind="down" />
                <i style={{ flexGrow: breadth.flat }} data-kind="flat" />
              </div>
            )}
          </div>
        )}
        {turnover && (
          <div className="intraday-metric">
            <span>样本累计成交额</span>
            <strong>{amount(turnover.amount)}</strong>
            <small>已取得 {integer(turnover.valid)} 只</small>
          </div>
        )}
        {interval && (
          <div className="intraday-metric">
            <span>本段新增成交额</span>
            <strong>{amount(interval.amount)}</strong>
            <small>
              {interval.change_from_previous != null
                ? `较昨同段 ${interval.change_from_previous > 0 ? "+" : ""}${amount(interval.change_from_previous)}`
                : interval.amount == null
                  ? "区间端点未核验"
                  : "昨同段暂无可比成交额"}
            </small>
          </div>
        )}
      </div>
    </section>
  );
}

const relayLabels: Record<string, string> = {
  promoted: "已核验晋级",
  sealed: "本轮封板",
  not_in_verified_pool: "本轮未入池",
  unknown: "状态待核验",
};

function StockSamples({
  rows,
  onStock,
  broken = false,
}: {
  rows: Row[];
  onStock?: (id: string) => void;
  broken?: boolean;
}) {
  if (!rows.length) return null;
  return (
    <ul className="intraday-feedback-stocks">
      {rows.map((row) => (
        <li key={row.instrument_id}>
          {onStock ? (
            <button
              className="stock-link"
              onClick={() => onStock(row.instrument_id)}
            >
              {row.name || row.instrument_id}
            </button>
          ) : (
            <span>{row.name || row.instrument_id}</span>
          )}
          <span className={tone(row.change_pct)}>
            {percent(row.change_pct)}
          </span>
          {broken && (
            <span className="intraday-stock-state" data-kind="broken">
              炸板
            </span>
          )}
          <ThemeChips row={row} showMissing={Array.isArray(row.themes)} />
          {row.previous_board_count != null && (
            <small>
              前日 {integer(row.previous_board_count)} 板 ·{" "}
              {relayLabels[row.state] || "待核验"}
            </small>
          )}
        </li>
      ))}
    </ul>
  );
}

function LadderCard({
  ladder,
  onStock,
}: {
  ladder: Row;
  onStock?: (id: string) => void;
}) {
  const broken = ladder.broken_board;
  return (
    <details className="intraday-feedback-card">
      <summary>
        <strong>盘中封板梯队</strong>
        <span>
          封板 {evidenceCount(ladder.sealed_count, ladder.state)} · 首板{" "}
          {evidenceCount(ladder.first_board_count, ladder.height_state)} · 连板{" "}
          {evidenceCount(ladder.multi_board_count, ladder.height_state)} · 最高{" "}
          {ladder.highest_board == null
            ? "—"
            : `${evidenceCount(ladder.highest_board, ladder.height_state)} 板`}
          {broken && (
            <>
              {" · "}
              <span className="intraday-break-count">
                炸板 {evidenceCount(broken.count, broken.state)}
              </span>
            </>
          )}
        </span>
      </summary>
      <div className="intraday-feedback-body">
        <p className="intraday-feedback-note">
          本轮采集区间的已核验封板，≥ 表示已确认下界，— 表示待核验。
          {ladder.unknown_height_count > 0 &&
            ` ${evidenceCount(ladder.unknown_height_count, ladder.state)} 只封板股高度待核验。`}
        </p>
        {(ladder.groups || []).map((group: Row) => (
          <div className="intraday-feedback-group" key={group.board_count}>
            <strong>
              {integer(group.board_count)} 板 ·{" "}
              {evidenceCount(group.count, ladder.height_state)} 只
            </strong>
            <StockSamples rows={group.items || []} onStock={onStock} />
          </div>
        ))}
        {ladder.omitted_groups > 0 && (
          <p className="intraday-feedback-note">仅展示主要梯队和代表股。</p>
        )}
        {broken && (
          <div className="intraday-feedback-group">
            <strong>
              炸板池 · {evidenceCount(broken.count, broken.state)} 只
            </strong>
            <p className="intraday-feedback-note">
              {broken.state === "unknown"
                ? "本轮炸板池待核验。"
                : "按本轮来源的炸板池核验。"}
              {broken.conflict_count > 0 &&
                ` ${integer(broken.conflict_count)} 只池间状态冲突，待核验。`}
            </p>
            <StockSamples rows={broken.items || []} onStock={onStock} broken />
          </div>
        )}
      </div>
    </details>
  );
}

function PriorSessionCard({
  prior,
  priorBroken,
  onStock,
}: {
  prior: Row;
  priorBroken?: Row;
  onStock?: (id: string) => void;
}) {
  return (
    <details className="intraday-feedback-card">
      <summary>
        <strong>前日涨停反馈</strong>
        <span>
          {prior.state === "unknown"
            ? "前日收盘样本待核验"
            : `样本 ${evidenceCount(prior.total, prior.state)} · 本轮封板 ${evidenceCount(prior.sealed_count, prior.sealed_state)} · 晋级 ${evidenceCount(prior.promotion_count, prior.promotion_state)}`}
        </span>
      </summary>
      <div className="intraday-feedback-body">
        <p className="intraday-feedback-note">
          {prior.date
            ? `前一交易日 ${prior.date} 收盘涨停样本。`
            : "未取得前一交易日的收盘涨停样本。"}
          {prior.state !== "unknown" &&
            `本轮有可比报价 ${integer(prior.priced)} 只，上涨 ${integer(prior.up)}、下跌 ${integer(prior.down)}、平盘 ${integer(prior.flat)}，平均 ${percent(prior.average_change_pct)}。`}
          晋级要求本轮已核验封板且板数比前日增加一板。
        </p>
        {!!prior.groups?.length && (
          <div className="intraday-table-scroll intraday-feedback-table">
            <table>
              <thead>
                <tr>
                  <th scope="col">前日梯队</th>
                  <th scope="col">可比 / 样本</th>
                  <th scope="col">涨 / 跌 / 平</th>
                  <th scope="col">平均涨幅</th>
                  <th scope="col">本轮封板</th>
                  <th scope="col">本轮晋级</th>
                </tr>
              </thead>
              <tbody>
                {prior.groups.map((group: Row) => (
                  <tr key={group.key}>
                    <th scope="row">{group.label}</th>
                    <td>
                      {integer(group.priced)} /{" "}
                      {evidenceCount(group.total, prior.state)}
                    </td>
                    <td>
                      {integer(group.up)} / {integer(group.down)} /{" "}
                      {integer(group.flat)}
                    </td>
                    <td className={tone(group.average_change_pct)}>
                      {percent(group.average_change_pct)}
                    </td>
                    <td>
                      {evidenceCount(group.sealed_count, group.sealed_state)}
                    </td>
                    <td>
                      {evidenceCount(
                        group.promotion_count,
                        group.promotion_state,
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <StockSamples rows={prior.items || []} onStock={onStock} />
        {priorBroken && (
          <div className="intraday-feedback-group intraday-prior-broken">
            <strong>前日炸板反馈</strong>
            <p className="intraday-feedback-note">
              {priorBroken.state === "unknown"
                ? "前日收盘炸板样本待核验。"
                : `样本 ${evidenceCount(priorBroken.total, priorBroken.state)} · 可比 ${integer(priorBroken.priced)} 只 · 平均 ${percent(priorBroken.average_change_pct)} · 本轮封板 ${evidenceCount(priorBroken.sealed_count, priorBroken.sealed_state)}`}
            </p>
            <StockSamples rows={priorBroken.items || []} onStock={onStock} />
          </div>
        )}
      </div>
    </details>
  );
}

function SameDayCard({
  comparison,
  onStock,
}: {
  comparison: Row;
  onStock?: (id: string) => void;
}) {
  const groups = [
    ["held", "两轮均入池"],
    ["entered", "本轮新入池"],
    ["absent", "本轮未入池"],
  ];
  const count = (key: string) =>
    evidenceCount(
      comparison[`${key}_count`],
      comparison[`${key}_state`] ?? comparison.state,
    );
  return (
    <details className="intraday-feedback-card">
      <summary>
        <strong>同日封板变化</strong>
        <span>
          {comparison.state === "unknown"
            ? comparison.collected_end_at
              ? "两轮池状态待核验"
              : "等待同日两份快照"
            : groups
                .map(([key, label]) => `${label} ${count(key)}`)
                .join(" · ")}
        </span>
      </summary>
      <div className="intraday-feedback-body">
        <p className="intraday-feedback-note">
          {comparison.collected_end_at
            ? `对照 ${comparison.label || "上一份快照"} ${clock(comparison.collected_end_at)}。比较仅覆盖两次采样，具体开板时间待核验。`
            : "同一交易日取得两份盘中快照后显示。"}
        </p>
        {groups.map(
          ([key, label]) =>
            !!comparison[key]?.length && (
              <div className="intraday-feedback-group" key={key}>
                <strong>
                  {label} · {count(key)} 只
                </strong>
                <StockSamples rows={comparison[key]} onStock={onStock} />
              </div>
            ),
        )}
      </div>
    </details>
  );
}

export function IntradaySnapshotFeedback({
  snapshot,
  onStock,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
}) {
  return (
    <>
      {snapshot.market_pulse && (
        <MarketPulse
          pulse={snapshot.market_pulse}
          interval={snapshot.summary?.interval_amount}
        />
      )}
      {(snapshot.ladder || snapshot.relay_feedback) && (
        <div className="intraday-feedback-grid" key={snapshot.id}>
          {snapshot.ladder && (
            <LadderCard ladder={snapshot.ladder} onStock={onStock} />
          )}
          {snapshot.relay_feedback?.prior_session && (
            <PriorSessionCard
              prior={snapshot.relay_feedback.prior_session}
              priorBroken={snapshot.relay_feedback.prior_broken_session}
              onStock={onStock}
            />
          )}
          {snapshot.relay_feedback?.same_day && (
            <SameDayCard
              comparison={snapshot.relay_feedback.same_day}
              onStock={onStock}
            />
          )}
        </div>
      )}
    </>
  );
}

function Ranking({
  rows,
  sector = false,
  title,
  subtitle,
  showRankChange = false,
  stockMode,
  emptyTitle,
  onStock,
  historyScope,
  memberScope,
}: {
  rows: Row[];
  sector?: boolean;
  title?: string;
  subtitle?: string;
  showRankChange?: boolean;
  stockMode?: StockMode;
  emptyTitle?: string;
  onStock?: (id: string) => void;
  historyScope?: StockHistoryScope;
  memberScope?: MemberScope;
}) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const [boardFilter, setBoardFilter] = useState<NoonBoardFilterValue>("all");
  const memberLimit = 20;
  const memberQuery = memberScope
    ? new URLSearchParams({
        date: memberScope.date,
        slot: memberScope.slot,
        snapshot_id: memberScope.snapshotId,
        topic_code: memberScope.topicCode,
        board: boardFilter,
        page: String(page + 1),
        limit: String(memberLimit),
      })
    : null;
  const members = useApi<Row>(
    memberQuery ? `/market/intraday/members?${memberQuery}` : null,
  );
  const loaded = members.data;
  const memberPage =
    memberScope &&
    loaded?.date === memberScope.date &&
    loaded.slot === memberScope.slot &&
    loaded.snapshot_id === memberScope.snapshotId &&
    loaded.topic_code === memberScope.topicCode &&
    loaded.board === boardFilter &&
    loaded.page === page + 1
      ? loaded
      : null;
  const useFrozenSample = memberPage?.state === "unknown";
  const displayRows: Row[] = memberScope
    ? useFrozenSample
      ? rows
      : memberPage?.items || []
    : rows;
  const filteredRows =
    !sector && historyScope && (!memberScope || useFrozenSample)
      ? filterRows(displayRows, boardFilter)
      : displayRows;
  const pageSize = expanded ? 30 : 10;
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filteredRows.length / pageSize) - 1),
  );
  const visibleRows = memberScope
    ? filteredRows
    : filteredRows.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const history = useApi<Row>(
    historyScope && !sector && visibleRows.length
      ? stockHistoryQuery(historyScope, visibleRows)
      : null,
  );
  const historyRows =
    history.data?.date === historyScope?.date &&
    history.data?.slot === historyScope?.slot
      ? history.data?.items
      : undefined;
  const broken = stockMode === "broken_stocks";
  const compact = stockMode != null;
  const hasOpenTimes = broken && displayRows.some((row) => row.open_times != null);
  const hasOpenPrice = displayRows.some((row) => row.open_change_pct != null);
  const hasAmount = displayRows.some((row) => row.amount != null);
  const hasComparison = displayRows.some(
    (row) => row.change_pp != null || row.amount_delta != null,
  );
  return (
    <Panel
      title={compact ? undefined : title || (sector ? "强势板块" : "强势个股")}
      subtitle={
        subtitle ||
        (sector
          ? "按涨跌幅排序 · 仅覆盖本轮观察板块"
          : "按涨跌幅排序 · 基于本轮已核验的个股报价")
      }
      className={`intraday-ranking${compact ? " intraday-stock-ranking" : ""}`}
    >
      {!sector && historyScope && (memberScope || rows.length > 0) && (
        <div
          className="intraday-stock-toolbar intraday-filters"
          role="group"
          aria-label="筛选上市板块"
        >
          {NOON_BOARD_OPTIONS.map(({ value, label }) => (
            <button
              type="button"
              className="btn small"
              key={value}
              aria-pressed={boardFilter === value}
              onClick={() => {
                setBoardFilter(value);
                setPage(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {memberPage && (
        <p className="intraday-stock-note">
          {useFrozenSample
            ? displayRows.length
              ? `该时点仅留存 ${integer(displayRows.length)} 只个股样本。`
              : "该时点暂无个股明细。"
            : [
                memberPage.membership_as_of && `名单留存日 ${memberPage.membership_as_of}`,
                memberPage.total != null && `留存名单 ${integer(memberPage.total)} 只`,
                memberPage.quote_coverage && `匹配本时点报价 ${integer(memberPage.quote_coverage.valid)} 只`,
                boardFilter !== "all" && memberPage.matched != null &&
                  `当前筛选 ${integer(memberPage.matched)} 只`,
              ].filter(Boolean).join(" · ")}
          {memberPage.unavailable_reason && ` ${memberPage.unavailable_reason}`}
        </p>
      )}
      {memberScope && members.error ? (
        <ErrorBox message={members.error} retry={members.reload} />
      ) : memberScope && !memberPage ? (
        <Loading />
      ) : filteredRows.length ? (
        <>
          {compact && !memberScope && (
            <p className="intraday-stock-note">
              {subtitle} · 留存样本 {integer(filteredRows.length)} 只
            </p>
          )}
          <div className="intraday-table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">排名</th>
                  <th scope="col">{sector ? "板块" : "股票"}</th>
                  <th scope="col">
                    {sector
                      ? "指数涨跌幅"
                      : historyScope?.slot === "auction"
                        ? "竞价涨跌幅"
                        : "今日涨跌幅"}
                  </th>
                  {!sector && historyScope && (
                    <>
                      <th scope="col">近30交易日涨幅</th>
                      <th scope="col">年内涨幅</th>
                      <th scope="col">最近一次涨停日期</th>
                      <th scope="col">近一年涨停次数</th>
                    </>
                  )}
                  {hasOpenPrice && <th scope="col">较开盘</th>}
                  {hasAmount && <th scope="col">累计成交额</th>}
                  {hasOpenTimes && <th scope="col">开板次数</th>}
                  {hasComparison && <th scope="col">较上次涨幅</th>}
                  {hasComparison && <th scope="col">区间新增成交额</th>}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, index) => {
                  const stockHistory = historyRows?.[row.instrument_id];
                  const previousHeight =
                    row.previous_board_count ??
                    stockHistory?.previous_board_count;
                  return (
                    <tr key={row.instrument_id}>
                      <td>
                        {row.rank ?? (memberScope ? page * memberLimit : currentPage * pageSize) + index + 1}
                        {showRankChange &&
                          row.previous_rank != null &&
                          row.rank != null &&
                          row.previous_rank !== row.rank && (
                            <small
                              title={`上次排名第 ${row.previous_rank}`}
                              className={tone(row.previous_rank - row.rank)}
                            >
                              {row.previous_rank > row.rank ? "↑" : "↓"}
                              {Math.abs(row.previous_rank - row.rank)}
                            </small>
                          )}
                      </td>
                      <th scope="row">
                        {!sector && onStock ? (
                          <button
                            className="stock-link"
                            onClick={() => onStock(row.instrument_id)}
                          >
                            {row.name || row.instrument_id}
                          </button>
                        ) : (
                          row.name || row.instrument_id
                        )}
                        <div className="intraday-stock-meta">
                          {row.instrument_id}
                          {!sector && (
                            <span
                              className="intraday-stock-state"
                              data-kind="listing"
                            >
                              {row.board_label || boardLabel(boardOf(row))}
                            </span>
                          )}
                          {row.kind === "industry"
                            ? " · 行业"
                            : row.kind === "concept"
                              ? " · 概念"
                              : ""}
                          {broken && (
                            <span
                              className="intraday-stock-state"
                              data-kind="broken"
                            >
                              炸板
                            </span>
                          )}
                          {stockMode === "sealed_stocks" && (
                            <span
                              className="intraday-stock-state"
                              data-kind="sealed"
                            >
                              {row.continue_day_cnt > 1
                                ? `${row.continue_day_cnt} 连板`
                                : row.continue_day_cnt === 1
                                  ? "首板"
                                  : "封板"}
                            </span>
                          )}
                          {!sector && previousHeight != null && (
                            <span className="intraday-stock-state">
                              前日 {integer(previousHeight)} 板
                            </span>
                          )}
                        </div>
                        <ThemeChips
                          row={row}
                          showMissing={compact && Array.isArray(row.themes)}
                        />
                        {row.signals?.length > 0 && (
                          <small>{row.signals.join(" · ")}</small>
                        )}
                        {row.limit_up_state === "unknown" && (
                          <small>封板状态待核验</small>
                        )}
                        {stockMode !== "sealed_stocks" &&
                          row.limit_up_state === "sealed" && (
                            <span
                              className="intraday-stock-state"
                              data-kind="sealed"
                            >
                              {row.continue_day_cnt > 1
                                ? `${row.continue_day_cnt} 连板`
                                : row.continue_day_cnt === 1
                                  ? "首板"
                                  : "封板"}
                            </span>
                          )}
                        {!broken && row.limit_up_state === "broken" && (
                          <span
                            className="intraday-stock-state"
                            data-kind="broken"
                          >
                            炸板
                          </span>
                        )}
                      </th>
                      <td className={tone(row.change_pct)}>
                        {percent(row.change_pct)}
                      </td>
                      {!sector && historyScope && (
                        <>
                          <td className={tone(stockHistory?.change_30d_pct)}>
                            {percent(stockHistory?.change_30d_pct)}
                          </td>
                          <td className={tone(stockHistory?.return_ytd_pct)}>
                            {percent(stockHistory?.return_ytd_pct)}
                          </td>
                          <td>
                            {stockHistory?.last_limit_up_date || "—"}
                            {stockHistory?.last_limit_up_date &&
                              stockHistory.last_limit_up_complete === false && (
                                <small>最近确认</small>
                              )}
                          </td>
                          <td>
                            {stockHistory?.limit_up_1y != null
                              ? integer(stockHistory.limit_up_1y)
                              : stockHistory?.limit_up_1y_observed > 0
                                ? `≥${integer(stockHistory.limit_up_1y_observed)}`
                                : "—"}
                          </td>
                        </>
                      )}
                      {hasOpenPrice && (
                        <td className={tone(row.open_change_pct)}>
                          {percent(row.open_change_pct)}
                        </td>
                      )}
                      {hasAmount && <td>{amount(row.amount)}</td>}
                      {hasOpenTimes && (
                        <td>
                          {row.open_times == null
                            ? "—"
                            : integer(row.open_times)}
                        </td>
                      )}
                      {hasComparison && (
                        <td className={tone(row.change_pp)}>
                          {delta(row.change_pp, "百分点")}
                        </td>
                      )}
                      {hasComparison && <td>{amount(row.amount_delta)}</td>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {historyScope && !sector && (
            <p className="intraday-stock-note intraday-history-note">
              {history.error
                ? "历史行情暂不可读"
                : history.loading
                  ? "历史行情读取中"
                  : `历史指标截至 ${history.data?.as_of || "已发布可核验交易日"} · — 表示资料缺失，≥ 表示可核验下界`}
            </p>
          )}
          {!memberScope && filteredRows.length > 10 && (
            <div className="intraday-expand">
              <button
                className="btn small"
                onClick={() => {
                  setExpanded(!expanded);
                  setPage(0);
                }}
              >
                {expanded ? "收起" : `查看已留存的 ${filteredRows.length} 项`}
              </button>
              {expanded && filteredRows.length > pageSize && (
                <>
                  <button
                    className="btn small"
                    disabled={currentPage === 0}
                    onClick={() => setPage(currentPage - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="btn small"
                    disabled={
                      (currentPage + 1) * pageSize >= filteredRows.length
                    }
                    onClick={() => setPage(currentPage + 1)}
                  >
                    下一页
                  </button>
                </>
              )}
            </div>
          )}
          {memberPage && memberPage.pages > 1 && (
            <div className="intraday-expand">
              <button className="btn small" disabled={page === 0} onClick={() => setPage(page - 1)}>
                上一页
              </button>
              <span>第 {page + 1} / {memberPage.pages} 页</span>
              <button className="btn small" disabled={page + 1 >= memberPage.pages} onClick={() => setPage(page + 1)}>
                下一页
              </button>
            </div>
          )}
        </>
      ) : (
        <Empty
          title={
            (rows.length || (memberScope && boardFilter !== "all")
              ? "该上市板块暂无留存个股" : emptyTitle) ||
            (sector ? "本轮没有可核验的板块排名" : "本轮没有可核验的个股排名")
          }
          description={memberScope ? "" : "仅展示该时点有记录的数据。"}
        />
      )}
    </Panel>
  );
}

type BoardMode =
  | "strong_sectors"
  | "limit_up_sectors"
  | "index_only_sectors"
  | "wide_limit_up_sectors";
type BoardKind = "all" | "industry" | "concept";
type StockMode = "members" | "candidates" | "sealed_stocks" | "broken_stocks";

const boardModes: [BoardMode, string][] = [
  ["strong_sectors", "板块强度"],
  ["limit_up_sectors", "封板家数"],
  ["wide_limit_up_sectors", "宽口径概念"],
  ["index_only_sectors", "仅指数线索"],
];

const anchorSlots = {
  auction: { key: "auction", label: "早盘竞价", start: "09:25" },
  noon: { key: "noon", label: "午评", start: "11:30" },
  close: { key: "close", label: "收盘复盘", start: "15:00" },
};

const currentCaptureSlots = [
  { key: "1000", label: "开盘首轮", start: "10:00" },
  { key: "1030", label: "早盘一小时", start: "10:30" },
  { key: "1100", label: "上午观察", start: "11:00" },
  { key: "1330", label: "午后首轮", start: "13:30" },
  { key: "1400", label: "午后观察", start: "14:00" },
  { key: "1430", label: "尾盘观察", start: "14:30" },
];

export function timelineSlots(view: Row) {
  const source = new Map<string, Row>(
    ((view.slots || []) as Row[]).map((slot) => [slot.key, slot]),
  );
  const slots: Row[] = [
    ...Object.values(anchorSlots),
    ...currentCaptureSlots.map((slot) => source.get(slot.key) || slot),
  ].sort((a, b) => a.start.localeCompare(b.start));
  for (const row of (view.snapshots || []) as Row[]) {
    if (!slots.some((slot) => slot.key === row.slot)) {
      slots.push({
        key: row.slot,
        label: row.label || row.slot_label || row.slot,
        start:
          source.get(row.slot)?.start ||
          clock(row.collected_end_at).slice(0, 5),
        legacy: true,
      });
    }
  }
  return slots;
}

function BriefCard({ brief }: { brief: Row }) {
  const context = (brief.context || []) as Row[];
  const evidence = (brief.evidence || []) as Row[];
  return (
    <section className="panel intraday-brief" aria-label="累计盘面简报">
      <div className="intraday-brief-top">
        <span className="intraday-eyebrow">
          <Sparkles size={14} aria-hidden="true" /> 累计盘面简报
        </span>
        <small>
          {brief.origin === "local_ai"
            ? "本机 AI · 已核验事实"
            : "已核验事实摘要"}
        </small>
      </div>
      <h2>{brief.headline}</h2>
      {brief.body && <p>{brief.body}</p>}
      {context.length > 0 && (
        <ul className="intraday-brief-context" aria-label="纳入简报的时点">
          {context.map((item) => (
            <li key={item.slot} data-status={item.status}>
              {item.label}
              {item.status === "missing"
                ? " · 缺档"
                : item.status === "partial"
                  ? " · 部分留存"
                  : ""}
            </li>
          ))}
        </ul>
      )}
      {evidence.length > 0 && (
        <details className="intraday-brief-evidence">
          <summary>查看核验依据</summary>
          <ul>
            {evidence.map((item) => (
              <li key={item.id}>
                {item.label && <strong>{item.label} </strong>}
                {item.text}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function stageBoards(snapshot?: Row): Row[] {
  if (!snapshot) return [];
  if (!snapshot.strength_coverage) return snapshot.sectors || [];
  return Array.from(
    new Map<string, Row>(
      boardModes.flatMap(([key]) =>
        ((snapshot[key] || []) as Row[]).map((row): [string, Row] => [
          row.instrument_id,
          row,
        ]),
      ),
    ).values(),
  );
}

export function Rotation({
  stages,
  samples,
  activeStage,
  onSelect,
}: {
  stages: Row[];
  samples: Map<string, Row>;
  activeStage: string;
  onSelect: (slot: string, board: string) => void;
}) {
  const limit = stages.findIndex((slot) => slot.key === activeStage);
  const columns = stages.slice(0, limit + 1).filter((slot) => !slot.legacy);
  const bySlot = new Map(
    columns.map((slot) => [slot.key, stageBoards(samples.get(slot.key))]),
  );
  const featured = Array.from(
    new Map<string, Row>(
      [...columns]
        .reverse()
        .flatMap((slot) =>
          (bySlot.get(slot.key) || [])
            .slice(0, 4)
            .map((board): [string, Row] => [board.instrument_id, board]),
        ),
    ).values(),
  ).slice(0, 12);
  if (columns.length < 2 || !featured.length) return null;
  return (
    <Panel
      className="intraday-rotation intraday-ranking"
      title="题材轮动"
      subtitle="截至当前观察时点 · 点击有数据的单元格查看该时点的题材个股"
    >
      <div
        className="intraday-table-scroll"
        tabIndex={0}
        aria-label="横向滚动查看各时点"
      >
        <table>
          <thead>
            <tr>
              <th scope="col">行业 / 题材</th>
              {columns.map((slot) => (
                <th scope="col" key={slot.key}>
                  <time>{slot.start}</time>
                  <small>{slot.label}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {featured.map((board) => (
              <tr key={board.instrument_id}>
                <th scope="row">
                  {board.name || board.instrument_id}
                  <small>{board.kind === "industry" ? "行业" : "概念"}</small>
                </th>
                {columns.map((slot) => {
                  const item = bySlot
                    .get(slot.key)
                    ?.find((row) => row.instrument_id === board.instrument_id);
                  const snapshot = samples.get(slot.key);
                  const canOpen =
                    item &&
                    (snapshot?.strength_coverage || Array.isArray(item.stocks));
                  return (
                    <td key={slot.key} className={tone(item?.change_pct)}>
                      {canOpen ? (
                        <button
                          type="button"
                          className="intraday-rotation-cell"
                          onClick={() =>
                            onSelect(slot.key, board.instrument_id)
                          }
                          aria-label={`${slot.start} ${board.name || board.instrument_id} 查看个股`}
                        >
                          <strong>{percent(item.change_pct)}</strong>
                          <small>
                            {slot.key === "auction"
                              ? "竞价候选均值"
                              : item.limit_up_count != null
                                ? `封板 ${evidenceCount(item.limit_up_count, item.limit_up_count_state)} 只`
                                : "查看个股"}
                          </small>
                        </button>
                      ) : item ? (
                        percent(item.change_pct)
                      ) : (
                        <small>{snapshot ? "—" : "缺档"}</small>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Retrospective({ view }: { view: Row }) {
  const events = (view.events || []) as Row[];
  if (!events.length) return null;
  const max = Math.max(1, ...events.map((event) => event.count));
  return (
    <Panel
      className="intraday-retrospective"
      title={view.title || "盘后回溯 · 首次封板"}
      subtitle={view.scope}
    >
      <ul className="intraday-event-bars">
        {events.map((event) => (
          <li key={event.label}>
            <span>{event.label}</span>
            <span className="intraday-event-track" aria-hidden="true">
              <i style={{ width: `${(100 * event.count) / max}%` }} />
            </span>
            <strong>{integer(event.count)} 只</strong>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function AnchorDetails({
  snapshot,
  onStock,
  requestedBoard,
  historyScope,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
  requestedBoard?: string;
  historyScope: StockHistoryScope;
}) {
  const boards = stageBoards(snapshot);
  const [selected, setSelected] = useState(
    requestedBoard || boards[0]?.instrument_id,
  );
  const board = boards.find((row) => row.instrument_id === selected);
  const stocks = ((board?.stocks || []) as Row[]).map((row) => ({
    ...row,
    continue_day_cnt: row.continue_day_cnt ?? row.board_count,
    limit_up_state: ["limit_up", "sealed"].includes(row.state)
      ? "sealed"
      : ["limit_break", "broken"].includes(row.state)
        ? "broken"
        : row.limit_up_state,
  }));
  return (
    <>
      {snapshot.scope && (
        <p className="intraday-stage-scope">{snapshot.scope}</p>
      )}
      <IntradaySnapshotFeedback
        snapshot={{
          ...snapshot,
          market_pulse: snapshot.market || snapshot.market_pulse,
          ladder: snapshot.ladder?.state ? snapshot.ladder : undefined,
        }}
        onStock={onStock}
      />
      {snapshot.slot === "auction" && (
        <AuctionHeightReference date={historyScope.date} onStock={onStock} />
      )}
      {!!boards.length && (
        <div className="intraday-direction-workspace">
          <Panel title="方向观察" className="intraday-direction-list">
            <div className="intraday-anchor-boards">
              {boards.map((row) => (
                <button
                  key={row.instrument_id}
                  className="intraday-anchor-board"
                  type="button"
                  aria-pressed={row.instrument_id === selected}
                  onClick={() => setSelected(row.instrument_id)}
                >
                  <span>
                    {row.name || row.instrument_id}
                    <small>{row.kind === "industry" ? "行业" : "概念"}</small>
                  </span>
                  <strong className={tone(row.change_pct)}>
                    {percent(row.change_pct)}
                  </strong>
                </button>
              ))}
            </div>
          </Panel>
          {board && (
            <Ranking
              key={board.instrument_id}
              rows={stocks}
              title={`${board.name || board.instrument_id} · 个股`}
              subtitle={
                snapshot.slot === "auction"
                  ? "只统计该题材中纳入竞价观察的候选"
                  : "按所选时点的成员名单和报价展示"
              }
              onStock={onStock}
              historyScope={historyScope}
              memberScope={{
                ...historyScope,
                snapshotId: snapshot.id,
                topicCode: board.instrument_id,
              }}
              emptyTitle="该时点暂无可核验个股"
            />
          )}
        </div>
      )}
      {snapshot.slot === "close" && snapshot.retrospective && (
        <Retrospective view={snapshot.retrospective} />
      )}
    </>
  );
}

function StrengthView({
  snapshot,
  onStock,
  historyScope,
  requestedBoard,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
  historyScope: StockHistoryScope;
  requestedBoard?: string;
}) {
  const requestedMode = boardModes.find(([key]) =>
    (snapshot[key] || []).some(
      (row: Row) => row.instrument_id === requestedBoard,
    ),
  )?.[0];
  const initialBoard =
    requestedMode &&
    (snapshot[requestedMode] || []).find(
      (row: Row) => row.instrument_id === requestedBoard,
    );
  const [mode, setMode] = useState<BoardMode>(
    requestedMode || "strong_sectors",
  );
  const [kind, setKind] = useState<BoardKind>("all");
  const [selectedBoard, setSelectedBoard] = useState<string | null>(
    initialBoard?.instrument_id || null,
  );
  const [stockMode, setStockMode] = useState<StockMode>(
    initialBoard
      ? "members"
      : "candidates",
  );
  const boards = ((snapshot[mode] || []) as Row[]).filter(
    (board) => kind === "all" || board.kind === kind,
  );
  const board = boards.find((row) => row.instrument_id === selectedBoard);
  const members = board
    ? snapshot.board_stocks?.[board.instrument_id]
    : undefined;
  const displayedMode =
    stockMode === "members" && !board ? "candidates" : stockMode;
  const poolComplete = snapshot.strength_coverage?.pool_complete === true;
  const breakout = snapshot.ladder?.broken_board;
  const rowsFor = (value: StockMode): Row[] =>
    value === "members"
      ? members?.items || []
      : value === "broken_stocks"
        ? board
          ? board.broken || []
          : snapshot.broken_stocks?.length
            ? snapshot.broken_stocks
            : breakout?.items || []
        : (board || snapshot)[value] || [];
  const stockModes: [StockMode, string][] = [
    ...(board ? [["members", "成分个股"] as [StockMode, string]] : []),
    ["candidates", "走强"],
    ["sealed_stocks", "封板"],
    ...(breakout ? [["broken_stocks", "炸板"] as [StockMode, string]] : []),
  ];
  const kinds: [BoardKind, string][] = [
    ["all", "全部"],
    ["industry", "行业"],
    ["concept", "概念"],
  ];
  return (
    <div className="intraday-direction-workspace">
      <Panel
        title="盘中方向"
        subtitle="选中一个板块，在右侧查看成员线索"
        className="intraday-ranking intraday-direction-list"
      >
        <div className="intraday-board-toolbar">
          <div className="intraday-filters" role="group" aria-label="榜单口径">
            {boardModes.map(([value, label]) => (
              <button
                className="btn small"
                key={value}
                aria-pressed={value === mode}
                onClick={() => {
                  setMode(value);
                  if (value === "wide_limit_up_sectors") setKind("all");
                  setSelectedBoard(null);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="intraday-filters" role="group" aria-label="板块类型">
            {kinds.map(([value, label]) => (
              <button
                className="btn small"
                key={value}
                aria-pressed={value === kind}
                disabled={
                  mode === "wide_limit_up_sectors" && value === "industry"
                }
                onClick={() => {
                  setKind(value);
                  setSelectedBoard(null);
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {mode === "wide_limit_up_sectors" && (
          <p className="intraday-intro">
            成员超过 200 只的概念单列，避免宽口径标签淹没更集中的方向。
          </p>
        )}
        {mode === "index_only_sectors" && (
          <p className="intraday-intro">
            这些板块的成员覆盖未达到强度榜门槛，只展示可核验的指数报价和样本。
          </p>
        )}
        {boards.length ? (
          <div className="intraday-table-scroll intraday-board-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">行业 / 题材</th>
                  <th scope="col">指数</th>
                  <th scope="col">上涨占比</th>
                  <th scope="col">封板</th>
                  {breakout && <th scope="col">炸板</th>}
                </tr>
              </thead>
              <tbody>
                {boards.map((row, i) => (
                  <tr
                    key={row.instrument_id}
                    data-selected={row.instrument_id === selectedBoard}
                  >
                    <td>{row.rank ?? i + 1}</td>
                    <th scope="row">
                      <button
                        className="stock-link"
                        aria-pressed={row.instrument_id === selectedBoard}
                        onClick={() => {
                          if (row.instrument_id !== selectedBoard)
                            setStockMode("members");
                          setSelectedBoard(
                            row.instrument_id === selectedBoard
                              ? null
                              : row.instrument_id,
                          );
                        }}
                      >
                        {row.name || row.instrument_id}
                        <ArrowUpRight size={13} aria-hidden="true" />
                      </button>
                      <small>
                        {row.kind === "industry"
                          ? "行业"
                          : row.kind === "concept"
                            ? "概念"
                            : "—"}
                      </small>
                    </th>
                    <td className={tone(row.change_pct)}>
                      {percent(row.change_pct)}
                    </td>
                    <td>
                      {row.up_ratio == null ? "—" : `${num(row.up_ratio)}%`}
                      {row.advancers != null && (
                        <small>上涨 {integer(row.advancers)} 只</small>
                      )}
                    </td>
                    <td>
                      {row.limit_up_count == null
                        ? "待核验"
                        : `${["partial", "lower_bound"].includes(row.limit_up_count_state) ? "≥" : ""}${integer(row.limit_up_count)} 只`}
                      {row.limit_up_ratio != null && (
                        <small>占成员 {num(row.limit_up_ratio)}%</small>
                      )}
                    </td>
                    {breakout && (
                      <td>
                        {evidenceCount(
                          row.broken_count,
                          row.broken_count_state,
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="当前口径没有可核验板块"
            description="可以切换榜单口径或板块类型。"
          />
        )}
        <details className="intraday-scope">
          <summary>榜单口径</summary>
          <p>
            强度榜要求指数上涨、过半可比成员上涨、成员涨幅中位数为正，且至少 5
            只可比成员、覆盖达到 60%。行业和概念可能共享成员。
          </p>
        </details>
      </Panel>
      <section
        className="panel intraday-direction-detail"
        aria-label="方向内个股"
      >
        <div className="intraday-detail-heading">
          <div>
            <span className="intraday-eyebrow">
              {board ? "板块观察" : "市场观察"}
            </span>
            <h2>{board?.name || "盘中个股线索"}</h2>
          </div>
          {board && (
            <button
              className="btn small"
              onClick={() => setSelectedBoard(null)}
            >
              全部方向
            </button>
          )}
        </div>
        {board && (
          <div className="intraday-detail-facts">
            <span>
              指数{" "}
              <strong className={tone(board.change_pct)}>
                {percent(board.change_pct)}
              </strong>
            </span>
            <span>
              较开盘 <strong>{percent(board.open_change_pct)}</strong>
            </span>
            <span>
              成员涨幅中位数 <strong>{percent(board.median_change_pct)}</strong>
            </span>
            <span>
              覆盖{" "}
              <strong>
                {integer(board.rankable_members)} /{" "}
                {integer(board.member_count)}
              </strong>
            </span>
            <span>
              首板 / 连板{" "}
              <strong>
                {evidenceCount(
                  board.first_board_count,
                  board.board_height_count_state,
                )}{" "}
                /{" "}
                {evidenceCount(
                  board.multi_board_count,
                  board.board_height_count_state,
                )}
              </strong>
            </span>
            {board.member_date && <span>名单 {board.member_date}</span>}
            {board.change_pp != null && (
              <span>
                较上轮{" "}
                <strong className={tone(board.change_pp)}>
                  {delta(board.change_pp, "百分点")}
                </strong>
              </span>
            )}
            {board.amount_delta != null && (
              <span>
                区间新增成交 <strong>{amount(board.amount_delta)}</strong>
              </span>
            )}
          </div>
        )}
        <div
          className="intraday-detail-tabs intraday-filters"
          role="group"
          aria-label="个股观察类型"
        >
          {stockModes.map(([value, label]) => (
            <button
              className="btn small"
              key={value}
              aria-pressed={value === displayedMode}
              onClick={() => setStockMode(value)}
            >
              {label}
              {value === "broken_stocks" && (
                <span className="intraday-break-count">
                  {board
                    ? evidenceCount(
                        board.broken_count,
                        board.broken_count_state,
                      )
                    : evidenceCount(breakout?.count, breakout?.state)}
                </span>
              )}
            </button>
          ))}
        </div>
        <Ranking
          key={`${board?.instrument_id || "market"}:${displayedMode}`}
          rows={rowsFor(displayedMode)}
          onStock={onStock}
          stockMode={displayedMode}
          historyScope={historyScope}
          memberScope={
            displayedMode === "members" && board
              ? {
                  ...historyScope,
                  snapshotId: snapshot.id,
                  topicCode: board.instrument_id,
                }
              : undefined
          }
          subtitle={
            displayedMode === "members"
              ? "按所选时点的成员名单和报价展示"
              : displayedMode === "candidates"
                ? "价格走强、未列入本轮已核验封板池"
                : displayedMode === "sealed_stocks"
                  ? "本轮封板池核验样本"
                  : "本轮炸板池核验样本"
          }
          emptyTitle={
            displayedMode === "members"
              ? "当前没有可核验的成员报价"
              : displayedMode === "candidates"
                ? poolComplete
                  ? "本轮暂无符合条件的走强股"
                  : "封板池待核验"
                : displayedMode === "sealed_stocks"
                  ? "本轮暂无封板样本"
                  : breakout?.state === "unknown"
                    ? "本轮炸板池待核验"
                    : "本轮暂无可核验炸板样本"
          }
        />
      </section>
    </div>
  );
}

export default function IntradayObservation({
  date,
  today,
  dateFixed,
  onResetDate,
  onStock,
}: {
  date: string;
  today: string;
  dateFixed: boolean;
  onResetDate: () => void;
  onStock?: OpenStock;
}) {
  const active = usePageActive();
  const result = useApi<Row>(
    date ? `/market/intraday?date=${encodeURIComponent(date)}` : null,
  );
  const view = result.data?.date === date ? result.data : null;
  const snapshots: Row[] = view?.snapshots || [];
  const anchors: Row[] = view?.anchors || [];
  const slots = view ? timelineSlots(view) : [];
  const samples = new Map<string, Row>(
    [...snapshots, ...anchors].map((row) => [row.slot, row]),
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [rotationBoard, setRotationBoard] = useState<string>();
  const pendingDrill = useRef(false);
  const details = useRef<HTMLDivElement>(null);
  const latest = slots
    .filter((slot) => samples.has(slot.key))
    .sort((a, b) => a.start.localeCompare(b.start))
    .at(-1);
  const selectedSlot =
    selected && slots.some((slot) => slot.key === selected)
      ? selected
      : latest?.key || view?.current_slot?.key || slots[0]?.key;
  const snapshot = samples.get(selectedSlot);
  const anchor = anchors.find((row) => row.slot === selectedSlot);
  const brief = ((view?.briefs || []) as Row[]).find(
    (row) => row.slot === selectedSlot,
  );
  const historyScope = { date, slot: selectedSlot };
  const openStock =
    onStock && snapshot
      ? (id: string) =>
          onStock(id, { slot: selectedSlot, snapshotId: snapshot.id })
      : undefined;
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [error, setError] = useState("");
  const lastRequest = useRef<{ slot: string; key: string } | null>(null);
  const reload = result.reload;
  const job = view?.latest_job;
  const running = activeStatus(job?.status) || activeStatus(view?.state);
  useEffect(() => {
    if (!pendingDrill.current) return;
    pendingDrill.current = false;
    details.current?.focus({ preventScroll: true });
    details.current?.scrollIntoView({ block: "start" });
  }, [selectedSlot, rotationBoard]);
  useEffect(() => {
    if (!active || date !== view?.today) return;
    const timer = window.setInterval(
      reload,
      running || submitted ? 2500 : 30000,
    );
    return () => window.clearInterval(timer);
  }, [active, date, view?.today, running, submitted, reload]);
  useEffect(() => {
    if (!submitted) return;
    if (job?.job_id === submitted || !view?.can_capture) {
      setSubmitted(null);
      return;
    }
    const timer = window.setTimeout(() => setSubmitted(null), 15000);
    return () => window.clearTimeout(timer);
  }, [submitted, job?.job_id, view?.can_capture]);

  async function capture() {
    if (pending || submitted || !view?.can_capture) return;
    const slot = view.current_slot?.key || "";
    if (!lastRequest.current || lastRequest.current.slot !== slot)
      lastRequest.current = { slot, key: requestKey() };
    setPending(true);
    setError("");
    try {
      const task = await post<Row>("/market/intraday/jobs", {
        date,
        request_key: lastRequest.current.key,
      });
      setSelected(null);
      setRotationBoard(undefined);
      setSubmitted(task.job_id || null);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "提交失败");
    } finally {
      setPending(false);
      reload();
    }
  }

  const summary = snapshot?.summary;
  const historical = !!today && !!date && date < today;
  return (
    <div className="intraday-workbench">
      <section className="panel intraday-header" aria-label="盘中观察">
        <div className="intraday-hero">
          <div className="intraday-hero-meta" role="status">
            {date && (
              <time className="intraday-active-date" dateTime={date}>
                {date}
              </time>
            )}
            {historical && (
              <span className="intraday-history-tag">历史快照</span>
            )}
            {snapshot && (
              <span className="intraday-snapshot-time">
                <Clock3 size={13} />
                {snapshot.label ||
                  snapshot.slot_label ||
                  slots.find((slot) => slot.key === snapshot.slot)?.label} ·{" "}
                {clock(snapshot.collected_end_at)}
              </span>
            )}
          </div>
          <div className="intraday-actions">
            <IntradayAutomation />
            {(historical || dateFixed) && (
              <button
                className="btn small"
                onClick={onResetDate}
                disabled={!today}
              >
                回到今天
              </button>
            )}
            <button
              className="btn"
              onClick={reload}
              aria-label="刷新盘中观察状态"
            >
              <RefreshCw size={15} />
              刷新状态
            </button>
            <button
              className="btn primary"
              disabled={!view?.can_capture || running || pending || !!submitted}
              onClick={capture}
              title={view?.reason || undefined}
            >
              <Activity size={15} />
              {running
                ? "采集中"
                : pending || submitted
                  ? "提交中"
                  : "采集当前时段"}
            </button>
          </div>
        </div>
        {view?.preview_notice && <Warning>{view.preview_notice}</Warning>}
        {error && <ErrorBox message={error} />}
        {result.error && <ErrorBox message={result.error} retry={reload} />}
        {!snapshot && view?.reason && (
          <p className="intraday-intro">{view.reason}</p>
        )}
        {job?.error_summary && !running && (
          <Warning>{job.error_summary}</Warning>
        )}
        {slots.length > 0 && (
          <div
            className="intraday-timeline"
            role="group"
            aria-label="盘中观察时段"
          >
            {slots.map((slot) => {
              const item = samples.get(slot.key);
              const current = view?.current_slot?.key === slot.key;
              const waitingClose = !item && slot.key === "close" && !historical;
              const status = item
                ? item.quality === "partial"
                  ? "部分留存"
                  : "已留存"
                : current
                  ? running
                    ? "采集中"
                    : "采集窗口"
                  : waitingClose
                    ? "等待发布"
                    : historical
                      ? "缺快照"
                      : "未留存";
              return (
                <button
                  key={slot.key}
                  className="intraday-slot"
                  data-current={current}
                  data-available={!!item}
                  aria-pressed={selectedSlot === slot.key}
                  onClick={() => {
                    setSelected(slot.key);
                    setRotationBoard(undefined);
                  }}
                >
                  <time>{slot.start}</time>
                  <span>{item?.label || slot.label}</span>
                  <small>
                    {status}
                    {slot.legacy ? " · 旧窗口" : ""}
                  </small>
                </button>
              );
            })}
          </div>
        )}
      </section>
      {brief && <BriefCard brief={brief} />}
      <div className="intraday-stage-details" ref={details} tabIndex={-1}>
        {anchor ? (
          <AnchorDetails
            key={`${anchor.id}:${rotationBoard || ""}`}
            snapshot={anchor}
            requestedBoard={rotationBoard}
            onStock={openStock}
            historyScope={historyScope}
          />
        ) : snapshot ? (
          <>
            <IntradaySnapshotFeedback snapshot={snapshot} onStock={openStock} />
            {snapshot.strength_coverage ? (
              <StrengthView
                key={`${snapshot.id}:${rotationBoard || ""}`}
                snapshot={snapshot}
                onStock={openStock}
                requestedBoard={rotationBoard}
                historyScope={historyScope}
              />
            ) : (
              <div className="intraday-rankings" key={snapshot.id}>
                <Ranking
                  rows={snapshot.sectors || []}
                  sector
                  title="板块指数涨幅"
                  subtitle="旧快照未留存板块成员与封板证据"
                  showRankChange
                />
                <Ranking
                  rows={snapshot.stocks || []}
                  onStock={openStock}
                  historyScope={historyScope}
                  title="个股涨幅"
                  subtitle="旧快照未留存封板证据"
                  showRankChange
                />
              </div>
            )}
            <details className="intraday-scope intraday-snapshot-scope">
              <summary>
                快照采集范围 · {clock(snapshot.collected_start_at)}–
                {clock(snapshot.collected_end_at)}（北京时间）
                {snapshot.quality === "partial" && " · 报价部分缺失"}
              </summary>
              <div className="intraday-facts">
                {summary?.stock_declared != null && (
                  <span>
                    来源声明{" "}
                    <strong>{integer(summary.stock_declared)} 只股票</strong>
                  </span>
                )}
                {summary?.coverage?.stocks?.rankable != null && (
                  <span>
                    可排名{" "}
                    <strong>
                      {integer(summary.coverage.stocks.rankable)} 只股票
                    </strong>
                  </span>
                )}
                {summary?.board_planned != null && (
                  <span>
                    板块报价{" "}
                    <strong>
                      {integer(summary.board_returned)} /{" "}
                      {integer(summary.board_planned)}
                    </strong>
                  </span>
                )}
                {summary?.comparison_slot && (
                  <span>与同日上一份可比快照比较</span>
                )}
              </div>
              {summary?.reason && (
                <p className="intraday-intro">{summary.reason}</p>
              )}
              {summary?.recent_listing_count > 0 && (
                <p className="intraday-intro">
                  新股单列（上市前 5 个交易日，共{" "}
                  {integer(summary.recent_listing_count)} 只可排名） ·{" "}
                  {(summary.recent_listings || [])
                    .map(
                      (row: Row) =>
                        `${row.name || row.instrument_id} ${percent(row.change_pct)}`,
                    )
                    .join("、")}
                </p>
              )}
              <p className="intraday-intro">
                排名基于已取得的报价。区间成交额是两次累计值之差，不代表净流入。快照之间没有连续分时记录。
              </p>
            </details>
          </>
        ) : result.loading && !view ? (
          <Loading />
        ) : (
          <Panel>
            <Empty
              title={
                running && view?.current_slot?.key === selectedSlot
                  ? "正在采集盘中报价"
                  : `${slots.find((slot) => slot.key === selectedSlot)?.start || "所选时点"} 暂无快照`
              }
              description={
                selectedSlot === "close"
                  ? "收盘行情正式发布后更新全天复盘。"
                  : `${date} 的这个时点没有留存报价，因此无法显示该时点的个股与板块。`
              }
              action={
                samples.size > 0 && (
                  <div className="intraday-expand">
                    {slots.filter((slot) => samples.has(slot.key)).map((slot) => (
                      <button
                        type="button"
                        className="btn small"
                        key={slot.key}
                        onClick={() => {
                          setSelected(slot.key);
                          setRotationBoard(undefined);
                        }}
                      >
                        查看 {slot.start} {slot.label}
                      </button>
                    ))}
                  </div>
                )
              }
            />
          </Panel>
        )}
      </div>
      <Rotation
        stages={slots}
        samples={samples}
        activeStage={selectedSlot}
        onSelect={(slot, board) => {
          if (selectedSlot === slot && rotationBoard === board) {
            details.current?.focus({ preventScroll: true });
            details.current?.scrollIntoView({ block: "start" });
            return;
          }
          pendingDrill.current = true;
          setSelected(slot);
          setRotationBoard(board);
        }}
      />
    </div>
  );
}
