import { useState } from "react";
import CopyStockCode from "../../components/CopyStockCode";
import { Empty, ErrorBox, Loading } from "../../components/UI";
import { useApi } from "../../lib/api";
import { integer, num, percent, tone } from "../../lib/format";
import { isAuctionGeneralAttribute } from "./auctionExplorerPresentation";

type AuctionTheme = {
  topic_code: string;
  name: string;
  kind: "industry" | "concept";
  members_observed_date: string;
};

type ReferenceStock = {
  instrument_id: string;
  name: string;
  auction_pct: number | null;
  opening_reference_price?: number;
  themes?: AuctionTheme[];
  themes_reason?: string;
};

type HeightGroup = {
  board_count: number;
  items: ReferenceStock[];
  up: number;
  down: number;
  flat: number;
  unknown: number;
};

type Reference = {
  date: string;
  previous_date: string | null;
  candidate_count: number;
  verified_height_count: number;
  unknown_height_count: number;
  highest_board: number | null;
  groups: HeightGroup[];
  reason: string | null;
};

function HeightThemes({ stock }: { stock: ReferenceStock }) {
  const [expanded, setExpanded] = useState(true);
  const themes = stock.themes ?? [];
  if (!themes.length) {
    return (
      <small
        className="intraday-height-themes"
        title={stock.themes_reason || "这份竞价快照未取得可核验题材归属"}
      >
        题材待核验
      </small>
    );
  }
  const visibleCount = 3;
  const industry = themes.find((theme) => theme.kind === "industry");
  const concepts = themes.filter(
    (theme) => theme.kind === "concept" && !isAuctionGeneralAttribute(theme),
  );
  const ordered = [
    ...(industry ? [industry] : []),
    ...concepts,
    ...themes.filter((theme) => theme !== industry && !concepts.includes(theme)),
  ];
  return (
    <span className="intraday-height-themes intraday-theme-chips">
      {(expanded ? ordered : ordered.slice(0, visibleCount)).map((theme) => (
        <span
          key={`${theme.kind}:${theme.topic_code}`}
          data-kind={theme.kind}
          title={`${theme.kind === "industry" ? "行业" : "概念"} · 名单留存日 ${theme.members_observed_date}`}
        >
          {theme.name}
        </span>
      ))}
      {themes.length > visibleCount && (
        <button
          type="button"
          className="intraday-height-themes-more"
          aria-expanded={expanded}
          aria-label={`${stock.name} ${expanded ? "收起其余题材" : "查看其余题材"}`}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起" : `+${themes.length - visibleCount}`}
        </button>
      )}
    </span>
  );
}

export default function AuctionHeightReference({
  date,
  onStock,
}: {
  date: string;
  onStock?: (id: string) => void;
}) {
  const { data, error, loading, reload } = useApi<Reference>(
    date
      ? `/market/intraday/auction-reference?date=${encodeURIComponent(date)}`
      : null,
  );
  const reference = data?.date === date ? data : null;
  return (
    <section className="panel intraday-brief" aria-label="前日高度板与竞价反馈">
      <h2>前日高度板与竞价反馈</h2>
      {error ? (
        <ErrorBox message={error} retry={reload} />
      ) : loading || !reference ? (
        <Loading />
      ) : !reference.groups.length ? (
        <Empty
          title="暂无可核验的前日高度板"
          description={reference.reason ?? "这份竞价快照未留存前日板数。"}
        />
      ) : (
        <>
          <p>
            {reference.previous_date} 收盘 · 本份竞价候选内最高{" "}
            {integer(reference.highest_board)} 板 ·{" "}
            {integer(reference.verified_height_count)} /{" "}
            {integer(reference.candidate_count)} 只候选核验到前日板数
            {reference.unknown_height_count > 0 &&
              ` · 另有 ${integer(reference.unknown_height_count)} 只前日涨停的板数待核验`}
          </p>
          <p className="intraday-feedback-note">
            按前一交易日收盘板数分组，涨跌幅来自本次竞价。题材取自竞价时已留存的成员名单。触及常规涨停参考价只表示价格匹配，封板状态待盘中核验。
          </p>
          <div className="intraday-feedback-grid">
            {reference.groups.map((group) => (
              <details
                key={group.board_count}
                className="intraday-feedback-card"
                open
              >
                <summary>
                  <strong>
                    前日 {integer(group.board_count)} 板 ·{" "}
                    {integer(group.items.length)} 只
                  </strong>
                  <span>
                    高开 {integer(group.up)} · 低开 {integer(group.down)} · 平开{" "}
                    {integer(group.flat)}
                    {group.unknown > 0 && ` · 缺报价 ${integer(group.unknown)}`}
                  </span>
                </summary>
                <div className="intraday-feedback-body">
                  <ul className="intraday-feedback-stocks">
                    {group.items.map((stock) => (
                      <li key={stock.instrument_id}>
                        {onStock ? (
                          <button
                            className="stock-link"
                            type="button"
                            onClick={() => onStock(stock.instrument_id)}
                          >
                            {stock.name}
                          </button>
                        ) : (
                          <span>{stock.name}</span>
                        )}
                        <CopyStockCode instrumentId={stock.instrument_id} />
                        <span className={tone(stock.auction_pct)}>
                          {stock.auction_pct === null
                            ? "竞价报价未知"
                            : percent(stock.auction_pct)}
                        </span>
                        {stock.opening_reference_price != null && (
                          <small>
                            触及涨停参考价 {num(stock.opening_reference_price)}{" "}
                            元
                          </small>
                        )}
                        <HeightThemes stock={stock} />
                      </li>
                    ))}
                  </ul>
                </div>
              </details>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
