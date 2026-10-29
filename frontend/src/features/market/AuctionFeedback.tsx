import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import { integer, percent, tone } from "../../lib/format";
import { noonRows } from "./noonPresentation";
import { auctionNumber, auctionSortRows } from "./auctionPresentation";
import AuctionClassifications from "./AuctionClassifications";
import AuctionCopyText from "./AuctionCopyText";
import CopyStockCode from "../../components/CopyStockCode";

const TYPES: Record<string, { title: string; color: string }> = {
  repair: { title: "修复观察", color: "positive" },
  weaken: { title: "转弱观察", color: "negative" },
  continuation: { title: "高开延续", color: "positive" },
  weak_continuation: { title: "低开延续", color: "negative" },
  flat_limit_up: { title: "涨停后平开", color: "neutral" },
  flat_limit_break: { title: "炸板后平开", color: "neutral" },
};

export default function AuctionFeedback({
  date,
  onStock,
  chartDate,
  topicView,
}: {
  date: string;
  onStock?: (id: string) => void;
  chartDate?: string;
  topicView: Row | null;
}) {
  const result = useApi<Row>(
    `/market/auction/feedback?date=${encodeURIComponent(date)}`,
  );
  const view =
    result.data?.date === date && result.data.version === "auction-feedback-v1"
      ? result.data
      : null;
  const groups = noonRows(view?.groups).filter(
    (group) => TYPES[group.key] && (auctionNumber(group.count) || 0) > 0,
  );
  const unknown = noonRows(view?.groups).filter(
    (group) => !TYPES[group.key] && (auctionNumber(group.count) || 0) > 0,
  );
  return (
    <Panel
      title="昨日 → 今日：强弱对照"
      subtitle="先看昨日事件，再看今日竞价相对昨收的定价。"
    >
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
            >
              <div className="auction-feedback-heading">
                <strong>{TYPES[group.key].title}</strong>
                <b>{integer(group.count)}只</b>
                <AuctionCopyText
                  label={`复制${TYPES[group.key].title}股票`}
                  text={noonRows(group.items)
                    .map((row) => `${row.instrument_id}\t${row.name || ""}`)
                    .join("\n")}
                />
              </div>
              <p title={group.interpretation}>{group.title}</p>
              <div className="auction-feedback-average">
                竞价均幅{" "}
                <strong
                  className={tone(auctionNumber(group.average_auction_pct))}
                >
                  {percent(auctionNumber(group.average_auction_pct))}
                </strong>
              </div>
              <details>
                <summary>查看本组股票</summary>
                <div className="auction-feedback-stocks">
                  {auctionSortRows(
                    noonRows(group.items),
                    "auction_pct",
                    "desc",
                  ).map((row) => (
                    <div
                      key={row.instrument_id}
                      className="auction-feedback-stock"
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
                        <span className={tone(auctionNumber(row.auction_pct))}>
                          {percent(auctionNumber(row.auction_pct))}
                        </span>
                      </div>
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
            {integer(view.coverage?.classified)}只 · 未分组{" "}
            {integer(view.coverage?.unknown)}只
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
