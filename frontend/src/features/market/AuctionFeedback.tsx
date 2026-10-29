import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import { integer, percent, tone } from "../../lib/format";
import { noonRows } from "./noonPresentation";
import {
  auctionFilterFeedbackGroups,
  auctionNumber,
  auctionSortRows,
} from "./auctionPresentation";
import AuctionClassifications from "./AuctionClassifications";
import AuctionCopyText from "./AuctionCopyText";
import CopyStockCode from "../../components/CopyStockCode";
import AuctionAssociationSummary from "./AuctionAssociationSummary";
import AuctionHeightFilters from "./AuctionHeightFilters";
import AuctionStockBadges from "./AuctionStockBadges";
import {
  auctionSortByHeight,
  type AuctionHeightKey,
} from "./auctionHeightPresentation";

const TYPES: Record<string, { title: string; color: string }> = {
  limit_up_open: { title: "一字涨停开", color: "limit-up-open" },
  repair: { title: "修复观察", color: "positive" },
  weaken: { title: "转弱观察", color: "negative" },
  continuation: { title: "高开延续", color: "positive" },
  weak_continuation: { title: "低开延续", color: "negative" },
  flat_limit_up: { title: "涨停后平开", color: "neutral" },
  flat_limit_break: { title: "炸板后平开", color: "neutral" },
};
const pendingLimitOpen = (group: Row) =>
  group.key === "limit_up_open" &&
  !["observed", "reference"].includes(group.state);

export default function AuctionFeedback({
  date,
  onStock,
  chartDate,
  topicView,
  selectedKey,
  selectedIds = null,
  onAssociationSelect,
  heightRows = [],
  selectedHeight = "all",
  onHeightSelect,
}: {
  date: string;
  onStock?: (id: string) => void;
  chartDate?: string;
  topicView: Row | null;
  selectedKey?: string;
  selectedIds?: ReadonlySet<string> | null;
  onAssociationSelect?: (key: string) => void;
  heightRows?: Row[];
  selectedHeight?: AuctionHeightKey;
  onHeightSelect?: (key: AuctionHeightKey) => void;
}) {
  const result = useApi<Row>(
    `/market/auction/feedback?date=${encodeURIComponent(date)}`,
  );
  const view =
    result.data?.date === date && result.data.version === "auction-feedback-v1"
      ? result.data
      : null;
  const sourceGroups = noonRows(view?.groups);
  const sourceByCode = new Map(
    heightRows.map((row) => [row.instrument_id, row]),
  );
  const limitUpOpen = sourceGroups.find(
    (group) => group.key === "limit_up_open",
  );
  const restrictedGroups = auctionFilterFeedbackGroups(
    view
      ? [
          limitUpOpen || {
            key: "limit_up_open",
            title: "今日涨停价开盘",
            interpretation: "仅为竞价口径，不代表全天一字涨停。",
            state: "unknown",
            count: null,
            average_auction_pct: null,
            items: [],
            reason: "原竞价来源未提供今日涨停价及涨停开盘标记，暂不能核验。",
          },
          ...sourceGroups.filter((group) => group.key !== "limit_up_open"),
        ]
      : [],
    selectedIds,
  );
  const visibleGroups = restrictedGroups.filter(
    (group) =>
      TYPES[group.key] &&
      (group.key === "limit_up_open" || (auctionNumber(group.count) || 0) > 0),
  );
  const groups = [
    ...visibleGroups.filter((group) => group.key === "limit_up_open"),
    ...auctionSortRows(
      visibleGroups.filter((group) => group.key !== "limit_up_open"),
      "average_auction_pct",
      "desc",
    ),
  ];
  const unknown = restrictedGroups.filter(
    (group) => !TYPES[group.key] && (auctionNumber(group.count) || 0) > 0,
  );
  return (
    <Panel
      title="昨日 → 今日：强弱对照"
      subtitle="保留强弱分类；组内先按昨日板高、再按竞价涨幅降序。板高与行业概念可组合筛选。"
    >
      {onHeightSelect && (
        <AuctionHeightFilters
          rows={heightRows}
          value={selectedHeight}
          onChange={onHeightSelect}
        />
      )}
      <AuctionAssociationSummary
        view={topicView}
        date={date}
        selectedKey={selectedKey}
        onSelect={onAssociationSelect}
      />
      {selectedIds && (
        <p className="auction-topic-note">
          卡片数量与均幅按当前板高、行业概念的交集统计；再点选中的快捷按钮可取消该条件。
        </p>
      )}
      {result.loading && !view && <Loading />}
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {view && groups.length ? (
        <div className="auction-feedback-cards">
          {groups.map((group) => (
            <article
              className={`auction-feedback-card ${TYPES[group.key].color}`}
              key={group.key}
              data-feedback-group={group.key}
              data-state={
                group.key === "limit_up_open" ? group.state : undefined
              }
            >
              <div className="auction-feedback-heading">
                <strong>{TYPES[group.key].title}</strong>
                {group.key === "limit_up_open" &&
                  group.state === "reference" && (
                    <span className="auction-feedback-reference">参考</span>
                  )}
                {pendingLimitOpen(group) ? (
                  <b className="auction-feedback-pending">待核验</b>
                ) : (
                  <>
                    <b>{integer(group.count)}只</b>
                    {noonRows(group.items).length > 0 && (
                      <AuctionCopyText
                        label={`复制${TYPES[group.key].title}股票`}
                        text={noonRows(group.items)
                          .map(
                            (row) => `${row.instrument_id}\t${row.name || ""}`,
                          )
                          .join("\n")}
                      />
                    )}
                  </>
                )}
              </div>
              <p title={group.interpretation}>
                {group.key === "limit_up_open" && group.state === "reference"
                  ? "竞价涨停开参考"
                  : group.title}
              </p>
              <div className="auction-feedback-average">
                竞价均幅{" "}
                <strong
                  className={tone(
                    pendingLimitOpen(group)
                      ? null
                      : auctionNumber(group.average_auction_pct),
                  )}
                >
                  {percent(
                    pendingLimitOpen(group)
                      ? null
                      : auctionNumber(group.average_auction_pct),
                  )}
                </strong>
              </div>
              {group.key === "limit_up_open" && group.state === "reference" && (
                <p className="auction-feedback-reference-note">
                  常规档参考，不确认当日限价或全天一字。
                </p>
              )}
              {pendingLimitOpen(group) ? (
                <p className="auction-feedback-pending-reason">
                  {group.reason ||
                    "原竞价来源未提供今日涨停价及涨停开盘标记，暂不能核验。"}
                </p>
              ) : noonRows(group.items).length > 0 ? (
                <details
                  key={`${date}:${selectedKey || "all"}:${selectedHeight}`}
                  open={!!selectedIds}
                >
                  <summary>查看本组股票</summary>
                  <div className="auction-feedback-stocks">
                    {auctionSortByHeight(
                      noonRows(group.items).map((row) => ({
                        ...sourceByCode.get(row.instrument_id),
                        ...row,
                      })),
                    ).map((row) => (
                      <div
                        key={row.instrument_id}
                        className={`auction-feedback-stock${selectedIds ? " is-associated" : ""}`}
                        data-associated={selectedIds ? "true" : undefined}
                      >
                        <div className="auction-feedback-stock-head">
                          <button
                            className="auction-stock"
                            onClick={() => onStock?.(row.instrument_id)}
                            disabled={!onStock || !chartDate}
                          >
                            {row.name || row.instrument_id}
                          </button>
                          <CopyStockCode instrumentId={row.instrument_id} />
                          <span
                            className={tone(auctionNumber(row.auction_pct))}
                          >
                            {percent(auctionNumber(row.auction_pct))}
                          </span>
                        </div>
                        <AuctionStockBadges row={row} />
                        <AuctionClassifications
                          view={topicView}
                          date={date}
                          instrumentId={row.instrument_id}
                          compact
                        />
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
            </article>
          ))}
        </div>
      ) : (
        view && (
          <Empty
            title="暂没有可核验的昨日事件对照"
            description="只按已留存的昨日涨停或炸板事实分组，缺失不推测。"
          />
        )
      )}
      {view && (
        <div className="auction-feedback-foot">
          <span>
            昨日事件 {view.previous_date || "未知"} · 已分组{" "}
            {integer(
              selectedIds
                ? groups.reduce(
                    (sum, group) =>
                      pendingLimitOpen(group)
                        ? sum
                        : sum + (auctionNumber(group.count) ?? 0),
                    0,
                  )
                : view.coverage?.classified,
            )}
            只 · 未分组{" "}
            {integer(
              selectedIds
                ? unknown.reduce(
                    (sum, group) => sum + (auctionNumber(group.count) ?? 0),
                    0,
                  )
                : view.coverage?.unknown,
            )}
            只
          </span>
          {unknown.map((group) => (
            <span key={group.key}>
              {group.title} {integer(group.count)}只
            </span>
          ))}
          <p>
            高开是修复/延续的观察起点，低开是转弱的观察事实；是否持续仍看开盘后承接，不自动归因于大盘。
          </p>
        </div>
      )}
    </Panel>
  );
}
