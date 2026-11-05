import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ArrowUpRight, ChevronRight, Radar } from "lucide-react";
import CopyStockCode from "../../components/CopyStockCode";
import { Badge, ErrorBox, Loading, Panel } from "../../components/UI";
import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { amount, integer, percent, tone } from "../../lib/format";
import AuctionHeightBadge from "./AuctionHeightBadge";
import { auctionNumber } from "./auctionPresentation";
import { MarketSortHeader } from "./MarketTable";
import { MemberReturnCell } from "./MarketMemberMetrics";
import { sortMarketRows } from "./marketPresentation";
import type { MarketSort } from "./marketPresentation";
import { boardLabel, boardOf } from "./noonBoardPresentation";
import { noonClock, noonLines, noonRows } from "./noonPresentation";
import "./AuctionDiscovery.css";

const LABELS: Record<string, string> = {
  available: "已留存",
  partial: "部分覆盖",
  unavailable: "本轮不可用",
  not_collected: "当日未留存",
  purged: "已到期清理",
};

type StockLinks = {
  chartDate?: string;
  onStock?: (id: string) => void;
};

const DIRECTION_FILTERS = [
  ["all", "全部"],
  ["industry", "行业"],
  ["concept", "概念"],
] as const;
type DirectionFilter = (typeof DIRECTION_FILTERS)[number][0];

export function discoveryThemeDistribution(items: Row[]) {
  const unique = [
    ...new Map(items.map((row) => [row.instrument_id, row])).values(),
  ];
  return (["industry", "concept"] as const).map((kind) => {
    const groups = new Map<string, Set<string>>();
    let classified = 0;
    for (const stock of unique) {
      const names = new Set<string>(
        noonRows(stock.themes)
          .filter(
            (theme) =>
              theme.kind === kind &&
              typeof theme.name === "string" &&
              theme.name.trim(),
          )
          .map((theme) => theme.name.trim()),
      );
      if (names.size) classified++;
      for (const name of names) {
        if (!groups.has(name)) groups.set(name, new Set());
        groups.get(name)!.add(stock.instrument_id);
      }
    }
    return {
      kind,
      total: unique.length,
      missing: unique.length - classified,
      groups: [...groups]
        .map(([name, symbols]) => ({ name, count: symbols.size }))
        .sort(
          (a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-CN"),
        ),
    };
  });
}

export function DiscoveryWorkspace({
  directions,
  changeLabel,
  amountLabel,
  getChange,
  getAmount,
  getCount,
  getMemberDate,
  getSignal,
  renderStocks,
  emptyText = "当前分类暂无可核验方向。",
}: {
  directions: Row[];
  changeLabel: string;
  amountLabel: string;
  getChange: (row: Row) => number | null;
  getAmount: (row: Row) => number | null;
  getCount: (row: Row) => number | null;
  getMemberDate?: (row: Row) => string | undefined;
  getSignal?: (row: Row) => string | undefined;
  renderStocks: (row: Row) => ReactNode;
  emptyText?: string;
}) {
  const [filter, setFilter] = useState<DirectionFilter>("all");
  const [selected, setSelected] = useState<string>();
  const [sort, setSort] = useState<MarketSort | null>(null);
  const onSort = (key: string) =>
    setSort((current) => ({
      key,
      order: current?.key === key && current.order === "desc" ? "asc" : "desc",
    }));
  const filtered = sortMarketRows(
    directions
      .filter((row) => filter === "all" || row.kind === filter)
      .map((row) => ({
        ...row,
        change_pct: getChange(row),
        member_count: getCount(row),
      })),
    sort,
  );
  const keyFor = (row: Row) => `${row.kind}:${row.instrument_id}`;
  const direction =
    filtered.find((row) => keyFor(row) === selected) || filtered[0];
  const directionName = direction?.name || direction?.instrument_id;
  const memberDate = direction && getMemberDate?.(direction);

  return (
    <div className="discovery-workspace">
      <section className="discovery-direction" aria-label="方向观察">
        <div className="discovery-section-head">
          <h3>方向观察</h3>
          <p>选择行业或题材，查看对应个股</p>
        </div>
        <div
          className="discovery-direction-filters"
          role="group"
          aria-label="板块类型"
        >
          {DIRECTION_FILTERS.map(([key, label]) => (
            <button
              type="button"
              className="btn small"
              key={key}
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        {filtered.length > 0 ? (
          <div className="discovery-scroll">
            <table className="discovery-direction-table">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  {[
                    ["name", "行业 / 题材"],
                    ["change_pct", changeLabel],
                    ["member_count", "样本"],
                  ].map(([key, caption]) => (
                    <th
                      key={key}
                      scope="col"
                      aria-sort={
                        sort?.key === key
                          ? sort.order === "desc"
                            ? "descending"
                            : "ascending"
                          : undefined
                      }
                    >
                      <MarketSortHeader
                        field={key}
                        caption={caption}
                        sort={sort}
                        onSort={onSort}
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((row, i) => {
                  const rowKey = keyFor(row);
                  const active = direction && rowKey === keyFor(direction);
                  const change = getChange(row);
                  const count = getCount(row);
                  return (
                    <tr
                      key={rowKey}
                      data-selected={active}
                      onClick={() => setSelected(rowKey)}
                    >
                      <td>{i + 1}</td>
                      <th scope="row">
                        <button
                          type="button"
                          className="stock-link"
                          aria-pressed={active}
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
                          {getSignal?.(row) && ` · ${getSignal(row)}`}
                        </small>
                      </th>
                      <td className={tone(change)}>{percent(change)}</td>
                      <td>{count == null ? "—" : `${integer(count)} 只`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="discovery-empty">{emptyText}</p>
        )}
      </section>
      <section className="discovery-detail" aria-label="方向内个股">
        <div className="discovery-section-head">
          <span>板块观察</span>
          <h3>
            {directionName || "暂无方向"}
            {directionName && " · 个股"}
          </h3>
        </div>
        {direction ? (
          <>
            <div className="discovery-detail-facts">
              <span>
                {changeLabel}{" "}
                <strong className={tone(getChange(direction))}>
                  {percent(getChange(direction))}
                </strong>
              </span>
              <span>
                {amountLabel} <strong>{amount(getAmount(direction))}</strong>
              </span>
              {memberDate && <span>成员档案 {memberDate}</span>}
            </div>
            {renderStocks(direction)}
          </>
        ) : (
          <p className="discovery-empty">选择有数据的方向后查看个股。</p>
        )}
      </section>
    </div>
  );
}

export function Stocks({
  items,
  chartDate,
  onStock,
  label,
  showLeader = false,
  showDistribution = false,
  history = {},
}: StockLinks & {
  items: Row[];
  label: string;
  showLeader?: boolean;
  showDistribution?: boolean;
  history?: Record<string, Row>;
}) {
  const [themeFilter, setThemeFilter] = useState<{
    kind: string;
    name: string;
  } | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [sort, setSort] = useState<MarketSort>({
    key: "auction_pct",
    order: "desc",
  });
  const onSort = (key: string) => {
    setPage(0);
    setSort((current) => ({
      key,
      order: current.key === key && current.order === "desc" ? "asc" : "desc",
    }));
  };
  const enriched: Row[] = items.map((stock) => {
    const metrics = history[stock.instrument_id] || {};
    return {
      ...metrics,
      ...stock,
      limit_up_1y:
        metrics.limit_up_1y ??
        (metrics.limit_up_1y_observed > 0
          ? metrics.limit_up_1y_observed
          : null),
      board_label: stock.board_label || boardLabel(boardOf(stock)),
      theme_labels:
        noonRows(stock.themes)
          .map((theme) => theme.name)
          .join("、") || null,
      source_warnings: noonLines(stock.warnings).join(" · ") || null,
    };
  });
  const distribution = discoveryThemeDistribution(items);
  const filtered = enriched.filter(
    (stock) =>
      !themeFilter ||
      noonRows(stock.themes).some(
        (theme) =>
          theme.kind === themeFilter.kind &&
          theme.name?.trim() === themeFilter.name,
      ),
  );
  const ordered = sortMarketRows(filtered, sort);
  const pages = Math.max(1, Math.ceil(ordered.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  const rows = ordered.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
  const knownPcts = enriched
    .map((stock) => auctionNumber(stock.auction_pct))
    .filter((value): value is number => value != null);
  const leaderPct =
    showLeader && knownPcts.length ? Math.max(...knownPcts) : null;
  const headers = [
    ["auction_pct", "竞价涨幅"],
    ["name", "股票"],
    ["board_label", "上市板块"],
    ["theme_labels", "行业 / 概念"],
    ["previous_board_count", "昨日板数"],
    ["auction_amount", "竞价额"],
    ["change_30d_pct", "近30交易日涨幅"],
    ["return_ytd_pct", "年内涨幅"],
    ["last_limit_up_date", "最近一次涨停"],
    ["limit_up_1y", "近一年涨停次数"],
    ["source_warnings", "来源异动"],
  ];
  function themeButton(kind: string, group: { name: string; count: number }) {
    const selected =
      themeFilter?.kind === kind && themeFilter.name === group.name;
    return (
      <button
        type="button"
        key={group.name}
        className="discovery-distribution-chip"
        data-kind={kind}
        aria-pressed={selected}
        onClick={() => {
          setThemeFilter(selected ? null : { kind, name: group.name });
          setPage(0);
        }}
      >
        <span>{group.name}</span>
        <b>{group.count}只</b>
      </button>
    );
  }
  return (
    <div className="auction-discovery-list">
      {showDistribution && (
        <div
          className="discovery-distribution"
          aria-label={`${label}行业概念分布`}
        >
          <div className="discovery-distribution-heading">
            <strong>主要行业与概念</strong>
            <small>
              按全部 {integer(distribution[0].total)} 只股票统计，概念可重叠
            </small>
            <button
              type="button"
              className="btn small"
              disabled={!themeFilter}
              onClick={() => {
                setThemeFilter(null);
                setPage(0);
              }}
            >
              清除筛选
            </button>
          </div>
          {distribution.map(({ kind, groups, missing }) => (
            <div className="discovery-distribution-row" key={kind}>
              <span>{kind === "industry" ? "行业" : "概念"}</span>
              <div className="discovery-distribution-options">
                {groups.slice(0, 3).map((group) => themeButton(kind, group))}
                {groups.length > 3 && (
                  <details className="discovery-distribution-more">
                    <summary>
                      其余{groups.length - 3}个
                      {kind === "industry" ? "行业" : "概念"}
                    </summary>
                    <div>
                      {groups.slice(3).map((group) => themeButton(kind, group))}
                    </div>
                  </details>
                )}
                {missing > 0 && <small>另有 {missing} 只归属待核验</small>}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="auction-table-wrap auction-discovery-table-scroll">
        <table
          className="auction-table auction-discovery-stocks"
          aria-label={label}
        >
          <colgroup>
            {[118, 170, 90, 220, 96, 140, 136, 112, 140, 130, 180].map(
              (width, index) => (
                <col key={index} style={{ width }} />
              ),
            )}
          </colgroup>
          <thead>
            <tr>
              {headers.map(([key, caption]) => (
                <th
                  key={key}
                  scope="col"
                  aria-sort={
                    sort.key === key
                      ? sort.order === "desc"
                        ? "descending"
                        : "ascending"
                      : undefined
                  }
                >
                  <MarketSortHeader
                    field={key}
                    caption={caption}
                    sort={sort}
                    onSort={onSort}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((stock) => {
              const pct = auctionNumber(stock.auction_pct);
              const height = auctionNumber(stock.previous_board_count);
              return (
                <tr key={stock.instrument_id}>
                  <td className={tone(pct)}>
                    {percent(pct)}
                    {leaderPct != null && pct === leaderPct && (
                      <span className="auction-discovery-leader">样本领涨</span>
                    )}
                  </td>
                  <td>
                    <div className="auction-identity">
                      <button
                        type="button"
                        className="auction-stock"
                        disabled={!chartDate || !onStock}
                        title={chartDate ? `K线截至 ${chartDate}` : undefined}
                        onClick={() => onStock?.(stock.instrument_id)}
                      >
                        {stock.name || stock.instrument_id}
                      </button>
                      <span className="auction-discovery-code muted">
                        <CopyStockCode instrumentId={stock.instrument_id} />
                      </span>
                    </div>
                  </td>
                  <td>
                    <Badge>{stock.board_label}</Badge>
                  </td>
                  <td className="auction-discovery-themes">
                    {noonRows(stock.themes).length ? (
                      <div className="auction-discovery-theme-list">
                        {noonRows(stock.themes)
                          .slice(0, 3)
                          .map((theme) => (
                            <span
                              key={`${theme.kind}:${theme.name}`}
                              className={`discovery-theme ${theme.kind === "industry" ? "industry" : "concept"}`}
                              title={`成员档案 ${theme.member_date || "待核验"}`}
                            >
                              {theme.name}
                            </span>
                          ))}
                        {noonRows(stock.themes).length > 3 && (
                          <span
                            className="muted"
                            title={noonRows(stock.themes)
                              .slice(3)
                              .map((theme) => theme.name)
                              .join("、")}
                          >
                            +{noonRows(stock.themes).length - 3}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="muted">归属待核验</span>
                    )}
                  </td>
                  <td>
                    {height != null && height > 0 ? (
                      <AuctionHeightBadge row={stock} />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{amount(auctionNumber(stock.auction_amount))}</td>
                  <td>
                    <MemberReturnCell row={stock} field="change_30d_pct" />
                  </td>
                  <td>
                    <MemberReturnCell row={stock} field="return_ytd_pct" />
                  </td>
                  <td title={stock.last_limit_up_reason || undefined}>
                    {stock.last_limit_up_date || "—"}
                    {stock.last_limit_up_date &&
                      stock.last_limit_up_complete !== true && (
                        <small className="auction-discovery-leader">
                          最近已确认
                        </small>
                      )}
                  </td>
                  <td
                    title={
                      stock.limit_up_1y_complete === true
                        ? "近一年逐日收盘涨停已完整核验"
                        : "历史来源覆盖不足，仅列已确认次数，不把缺报补为零"
                    }
                  >
                    {auctionNumber(history[stock.instrument_id]?.limit_up_1y) !=
                    null
                      ? `${integer(history[stock.instrument_id].limit_up_1y)} 次`
                      : stock.limit_up_1y_observed > 0
                        ? `≥${integer(stock.limit_up_1y_observed)} 次`
                        : "—"}
                  </td>
                  <td className="auction-discovery-warnings">
                    {noonLines(stock.warnings).join(" · ") || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="discovery-pagination" aria-label={`${label}分页`}>
        <span>
          {themeFilter && `${themeFilter.name} · `}共 {integer(ordered.length)}{" "}
          只
          {ordered.length > 0 &&
            ` · ${currentPage * pageSize + 1}–${Math.min((currentPage + 1) * pageSize, ordered.length)}`}
        </span>
        <label>
          每页{" "}
          <select
            aria-label={`${label}每页条数`}
            value={pageSize}
            onChange={(event) => {
              setPageSize(Number(event.target.value));
              setPage(0);
            }}
          >
            {[10, 20, 30].map((size) => (
              <option key={size} value={size}>
                {size}条
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="btn small"
          disabled={currentPage === 0}
          onClick={() => setPage(currentPage - 1)}
        >
          上一页
        </button>
        <span>
          {currentPage + 1} / {pages} 页
        </span>
        <button
          type="button"
          className="btn small"
          disabled={currentPage + 1 >= pages}
          onClick={() => setPage(currentPage + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}

export default function AuctionDiscovery({
  date,
  collecting,
  active,
  chartDate,
  onStock,
}: StockLinks & { date: string; collecting?: boolean; active: boolean }) {
  const { data, loading, error, reload } = useApi<Row>(
    `/market/auction/discovery?date=${encodeURIComponent(date)}`,
    [collecting],
  );
  const result = data?.date === date ? data : null;
  const waiting = collecting && (!result || result.state === "not_collected");
  useEffect(() => {
    if (!active || !waiting) return;
    const timer = window.setInterval(reload, 4000);
    return () => window.clearInterval(timer);
  }, [active, waiting, reload]);
  const directions = noonRows(result?.directions);
  const grouped = new Set(
    directions.flatMap((row) =>
      noonRows(row.stocks).map((stock) => stock.instrument_id),
    ),
  );
  const individuals = noonRows(result?.stocks).filter(
    (stock) => !grouped.has(stock.instrument_id),
  );
  const supplement = result?.supplement;
  const supplementRows = noonRows(supplement?.items);
  const hasStockRows =
    directions.some((row) => noonRows(row.stocks).length > 0) ||
    individuals.length > 0 ||
    supplementRows.length > 0;
  const historyQuery = useApi<Row>(
    active && hasStockRows
      ? `/market/auction/discovery/history?date=${encodeURIComponent(date)}`
      : null,
  );
  const history =
    historyQuery.data?.date === date && historyQuery.data?.slot === "auction"
      ? historyQuery.data
      : null;
  const coverage = result?.coverage;
  const state = result?.state;
  const hasObservations = state === "available" || state === "partial";
  const hasSupplement =
    (auctionNumber(supplement?.requested) ?? 0) > 0 ||
    (auctionNumber(supplement?.remaining) ?? 0) > 0;
  const stateLabel = waiting
    ? "正在采集"
    : LABELS[state] || (loading ? "正在读取" : "待核验");
  const reason = waiting
    ? "昨日重点股竞价已留存，正在核对名单外的竞价异动。"
    : typeof result?.reason === "string"
      ? result.reason
      : "";
  return (
    <Panel
      className="auction-discovery"
      title={
        <span className="auction-discovery-title">
          <Radar size={17} />
          今日竞价发现
        </span>
      }
      subtitle="从市场竞价异动中找出重点名单外的线索，再用终态报价核对。"
      actions={
        <Badge
          tone={
            state === "available"
              ? "good"
              : state === "partial" || state === "unavailable"
                ? "warn"
                : "neutral"
          }
        >
          {stateLabel}
        </Badge>
      }
    >
      {!result && loading && !error && <Loading />}
      {error && <ErrorBox message={error} retry={reload} />}
      {(result || !loading) && (
        <div className="auction-discovery-body">
          {coverage &&
            (hasObservations || (auctionNumber(coverage.pages) ?? 0) > 0) &&
            auctionNumber(coverage.candidates) != null && (
              <div className="auction-discovery-coverage">
                <span>
                  名单外线索 <b>{integer(coverage.candidates)}</b> 只
                </span>
                <span>
                  终态核验 <b>{integer(coverage.valid)}</b> 只
                </span>
                {result?.captured_at_ms && (
                  <span>留存于 {noonClock(result.captured_at_ms)}</span>
                )}
                {coverage.has_more === true && (
                  <span>来源仍有未读取的记录</span>
                )}
              </div>
            )}
          {reason && <p className="auction-discovery-note">{reason}</p>}
          {hasStockRows && (
            <p className="auction-discovery-note">
              {history?.as_of
                ? `历史指标截至 ${history.as_of} 收盘，竞价数据来自当天；缺项保留未知。`
                : historyQuery.loading
                  ? "正在读取上一已发布收盘日的历史指标…"
                  : "历史指标暂不可用，竞价数据仍可查看。"}
              {historyQuery.error && (
                <button
                  type="button"
                  className="btn small"
                  onClick={historyQuery.reload}
                >
                  重试历史指标
                </button>
              )}
            </p>
          )}
          {directions.length > 0 && (
            <div className="auction-discovery-group">
              <h3>多股同时高开</h3>
              <DiscoveryWorkspace
                key={date}
                directions={directions}
                changeLabel="涨幅中位数"
                amountLabel="样本竞价额"
                getChange={(row) => auctionNumber(row.median_auction_pct)}
                getAmount={(row) => auctionNumber(row.total_auction_amount)}
                getCount={(row) => auctionNumber(row.stock_count)}
                getMemberDate={(row) => row.members_observed_date}
                renderStocks={(row) => (
                  <Stocks
                    items={noonRows(row.stocks)}
                    label={`${row.name || row.instrument_id}竞价个股`}
                    showLeader
                    history={history?.items}
                    {...{ chartDate, onStock }}
                  />
                )}
              />
            </div>
          )}
          {individuals.length > 0 && (
            <details className="auction-discovery-extra" open>
              <summary>
                <ChevronRight size={15} aria-hidden="true" />
                其他个股异动 · {integer(individuals.length)} 只
              </summary>
              <Stocks
                items={individuals}
                label="其他个股竞价异动"
                showDistribution
                history={history?.items}
                {...{ chartDate, onStock }}
              />
            </details>
          )}
          {hasSupplement && (
            <details className="auction-discovery-extra" open>
              <summary>
                <ChevronRight size={15} aria-hidden="true" />
                昨日重点补查 · 核验 {integer(supplement.valid)} /{" "}
                {integer(supplement.requested)} 只
                <span>原名单仍有 {integer(supplement.remaining)} 只未覆盖</span>
              </summary>
              {supplementRows.length > 0 ? (
                <Stocks
                  items={supplementRows}
                  label="昨日重点补查股票"
                  showDistribution
                  history={history?.items}
                  {...{ chartDate, onStock }}
                />
              ) : (
                <p className="auction-discovery-note">
                  本轮暂无可核验的补查个股。
                </p>
              )}
            </details>
          )}
          {hasObservations && (
            <p className="auction-discovery-note">
              {directions.length === 0 && "本轮没有核验到多股同时高开的方向。"}
              统计限于本次异动样本，同股可归入多个方向。
            </p>
          )}
          {!error &&
            !waiting &&
            !loading &&
            (state === "not_collected" || !state) && (
              <p className="auction-discovery-note">
                {!reason && "所选日期暂无竞价发现记录。"}
                <a href="#data?section=automatic">前往自动采集计划</a>
              </p>
            )}
        </div>
      )}
    </Panel>
  );
}
