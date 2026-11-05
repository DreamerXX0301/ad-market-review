import { useState } from "react";
import CopyStockCode from "../../components/CopyStockCode";
import { Empty, ErrorBox, Loading } from "../../components/UI";
import { useApi } from "../../lib/api";
import { integer, num, percent, tone } from "../../lib/format";
import { isAuctionGeneralAttribute } from "./auctionExplorerPresentation";
import "./AuctionHeightReference.css";

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
  const [expanded, setExpanded] = useState(false);
  const themes = stock.themes ?? [];
  if (!themes.length) {
    return (
      <small
        className="ahr-themes-unknown"
        title={stock.themes_reason || "这份竞价快照未取得可核验题材归属"}
      >
        题材待核验
      </small>
    );
  }
  const industry = themes.find((theme) => theme.kind === "industry");
  const concepts = themes.filter(
    (theme) => theme.kind === "concept" && !isAuctionGeneralAttribute(theme),
  );
  const ordered = [
    ...(industry ? [industry] : []),
    ...concepts,
    ...themes.filter(
      (theme) => theme !== industry && !concepts.includes(theme),
    ),
  ];
  const preview = [...(industry ? [industry] : []), ...concepts.slice(0, 2)];
  const compact = preview.length ? preview : ordered.slice(0, 2);
  const hiddenCount = themes.length - compact.length;
  return (
    <div className="ahr-themes" aria-label={`${stock.name}的行业与题材`}>
      {(expanded ? ordered : compact).map((theme) => (
        <span
          key={`${theme.kind}:${theme.topic_code}`}
          data-kind={theme.kind}
          title={`${theme.kind === "industry" ? "行业" : "概念"} · 名单留存日 ${theme.members_observed_date}`}
        >
          {theme.name}
        </span>
      ))}
      {hiddenCount > 0 && (
        <button
          type="button"
          className="ahr-themes-more"
          aria-expanded={expanded}
          aria-label={`${stock.name} ${expanded ? "收起其余题材" : "查看其余题材"}`}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起" : `+${hiddenCount} 更多`}
        </button>
      )}
    </div>
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
    <section
      className="panel auction-height-reference"
      aria-label="前日高度板与竞价反馈"
    >
      <header className="ahr-header">
        <div className="ahr-heading">
          <h2>前日高度板与竞价反馈</h2>
          <span>前日队列 · 本次竞价</span>
        </div>
        {reference && (
          <div className="ahr-summary" aria-label="前日高度核验摘要">
            <span>
              前收 <time>{reference.previous_date ?? "待核验"}</time>
            </span>
            <span>
              候选最高 <b>{integer(reference.highest_board)}</b> 板
            </span>
            <span>
              已核验{" "}
              <b>
                {integer(reference.verified_height_count)} /{" "}
                {integer(reference.candidate_count)}
              </b>{" "}
              只
            </span>
            {reference.unknown_height_count > 0 && (
              <span className="ahr-summary-unknown">
                板数待核验 {integer(reference.unknown_height_count)} 只
              </span>
            )}
          </div>
        )}
      </header>
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
          <details className="ahr-method">
            <summary>口径说明</summary>
            <p>
              按前一交易日收盘板数分组，最高板数仅限本份竞价候选，涨跌幅来自本次竞价。
              题材取自竞价时已留存的成员名单。触及常规涨停参考价只表示价格匹配，封板状态待盘中核验。
            </p>
          </details>
          <div className="ahr-groups">
            {reference.groups.map((group) => (
              <details key={group.board_count} className="ahr-group" open>
                <summary>
                  <strong>
                    前日 {integer(group.board_count)} 板 ·{" "}
                    <small>{integer(group.items.length)} 只</small>
                  </strong>
                  <span className="ahr-group-stats">
                    高开 {integer(group.up)} · 低开 {integer(group.down)} · 平开{" "}
                    {integer(group.flat)}
                    {group.unknown > 0 && ` · 缺报价 ${integer(group.unknown)}`}
                  </span>
                </summary>
                <div className="ahr-group-body">
                  <ul className="ahr-stocks">
                    {group.items.map((stock) => (
                      <li key={`${date}:${stock.instrument_id}`}>
                        <div className="ahr-stock-head">
                          {onStock ? (
                            <button
                              className="ahr-stock-name"
                              type="button"
                              title={`查看 ${stock.name}`}
                              onClick={() => onStock(stock.instrument_id)}
                            >
                              {stock.name}
                            </button>
                          ) : (
                            <span className="ahr-stock-name">{stock.name}</span>
                          )}
                          <CopyStockCode instrumentId={stock.instrument_id} />
                          <strong
                            className={`ahr-stock-change ${tone(stock.auction_pct)}`}
                            title="本次留存竞价涨幅"
                          >
                            {stock.auction_pct === null
                              ? "报价待核验"
                              : percent(stock.auction_pct)}
                          </strong>
                        </div>
                        <div className="ahr-stock-info">
                          {stock.opening_reference_price != null && (
                            <small className="ahr-opening-reference">
                              触及涨停参考价{" "}
                              {num(stock.opening_reference_price)} 元
                            </small>
                          )}
                          <HeightThemes stock={stock} />
                        </div>
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
