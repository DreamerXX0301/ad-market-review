import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Badge, Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import { integer, percent, tone } from "../../lib/format";
import { noonRows } from "./noonPresentation";
import { auctionNumber, auctionSortRows } from "./auctionPresentation";
import AuctionStockTiles from "./AuctionStockTiles";
import AuctionCopyText from "./AuctionCopyText";
import "./AuctionTables.css";

export default function AuctionResonance({
  date,
  quotes,
  onStock,
  chartDate,
}: {
  date: string;
  quotes: Row[];
  onStock?: (id: string) => void;
  chartDate?: string;
}) {
  const result = useApi<Row>(
    `/market/auction/resonance?date=${encodeURIComponent(date)}`,
  );
  const view =
    result.data?.date === date && result.data.version === "auction-resonance-v1"
      ? result.data
      : null;
  const [kind, setKind] = useState("all");
  const [expanded, setExpanded] = useState("");
  const [order, setOrder] = useState("concentration_lift");
  const groups = noonRows(view?.groups).filter(
    (row) => (kind === "all" || row.kind === kind) && row.top_count >= 2,
  );
  const rows = auctionSortRows(groups, order, "desc").slice(0, 15);
  const names = new Map(
    quotes.map((row) => [row.instrument_id, row.name || row.instrument_id]),
  );
  const topCount = auctionNumber(view?.strong_sample?.count);
  const valid = auctionNumber(view?.strong_sample?.valid_count);
  const rate = (value: unknown) => {
    const n = auctionNumber(value);
    return n === null ? "—" : `${(n * 100).toFixed(0)}%`;
  };
  return (
    <Panel
      title="题材共振"
      subtitle="竞价涨幅前20只，是否集中在同一方向。"
      actions={
        <div className="button-group" aria-label="共振分组">
          {[
            ["all", "全部"],
            ["concept", "概念"],
            ["industry", "行业"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={`btn small${kind === key ? " active" : ""}`}
              aria-pressed={kind === key}
              onClick={() => setKind(key)}
            >
              {label}
            </button>
          ))}
        </div>
      }
    >
      {result.loading && !view && <Loading />}
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {view && (
        <>
          <div className="auction-topic-meta">
            <Badge>前{integer(topCount)}只</Badge>
            <span>有效竞价 {integer(valid)}只</span>
            <span>每组至少3只样本、命中前列至少2只</span>
          </div>
          <div
            className="button-group auction-resonance-order"
            aria-label="共振排序"
          >
            {[
              ["concentration_lift", "集中度"],
              ["average_auction_pct", "均幅"],
              ["top_count", "命中数"],
            ].map(([key, label]) => (
              <button
                key={key}
                className={`btn small${order === key ? " active" : ""}`}
                aria-pressed={order === key}
                onClick={() => setOrder(key)}
              >
                {label} 高→低
              </button>
            ))}
          </div>
          {rows.length ? (
            <div className="auction-table-scroll">
              <table className="auction-fixed-table auction-resonance-fixed">
                <colgroup>
                  <col style={{ width: "30%" }} />
                  <col style={{ width: "15%" }} />
                  <col style={{ width: "15%" }} />
                  <col style={{ width: "14%" }} />
                  <col style={{ width: "13%" }} />
                  <col style={{ width: "13%" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>概念 / 行业</th>
                    <th>前列命中</th>
                    <th>集中倍数</th>
                    <th>竞价均幅</th>
                    <th>中位数</th>
                    <th>高开占比</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const key = `${date}:${row.source}:${row.kind}:${row.topic_code}:${row.members_observed_date}`;
                    const open = expanded === key;
                    const ids: string[] = Array.isArray(row.instrument_ids)
                      ? row.instrument_ids
                      : [];
                    const topIds = new Set(
                      Array.isArray(row.top_instrument_ids)
                        ? row.top_instrument_ids
                        : [],
                    );
                    const text = `${row.name}\n${ids.map((id) => `${id}\t${names.get(id) || id}`).join("\n")}`;
                    return (
                      <Fragment key={key}>
                        <tr>
                          <td
                            title={`成员档案 ${row.members_observed_date} · ${row.topic_code}`}
                          >
                            <div className="auction-table-group">
                              <span
                                className={`auction-table-kind ${row.kind}`}
                              >
                                {row.kind === "industry" ? "行业" : "概念"}
                              </span>
                              <button
                                type="button"
                                className="auction-table-toggle"
                                aria-expanded={open}
                                onClick={() => setExpanded(open ? "" : key)}
                              >
                                {open ? (
                                  <ChevronDown size={13} />
                                ) : (
                                  <ChevronRight size={13} />
                                )}
                                <span>{row.name}</span>
                              </button>
                              <AuctionCopyText
                                text={String(row.name || row.topic_code)}
                                label={`复制组名 ${row.name}`}
                              />
                              <AuctionCopyText
                                text={String(row.topic_code)}
                                label={`复制${row.kind === "industry" ? "行业" : "概念"}代码 ${row.topic_code}`}
                              />
                            </div>
                          </td>
                          <td>
                            {integer(row.top_count)} /{" "}
                            {integer(row.valid_count)}只
                          </td>
                          <td>
                            {auctionNumber(row.concentration_lift)?.toFixed(
                              2,
                            ) ?? "—"}
                            倍
                          </td>
                          <td
                            className={tone(
                              auctionNumber(row.average_auction_pct),
                            )}
                          >
                            {percent(auctionNumber(row.average_auction_pct))}
                          </td>
                          <td
                            className={tone(
                              auctionNumber(row.median_auction_pct),
                            )}
                          >
                            {percent(auctionNumber(row.median_auction_pct))}
                          </td>
                          <td>
                            {rate(row.up_ratio)}
                            <small className="auction-muted">
                              {integer(row.up_count)}/{integer(row.valid_count)}
                            </small>
                          </td>
                        </tr>
                        {open && (
                          <tr className="auction-table-expanded-row">
                            <td colSpan={6}>
                              <div className="auction-table-expanded">
                                <div className="auction-table-expanded-heading">
                                  <strong>
                                    {row.name} · 本组{integer(ids.length)}
                                    只，前列{integer(row.top_count)}只
                                  </strong>
                                  <AuctionCopyText
                                    text={text}
                                    label="复制本组股票"
                                  />
                                  <span>
                                    成员档案 {row.members_observed_date} · 竞价{" "}
                                    {date}
                                  </span>
                                </div>
                                <AuctionStockTiles
                                  ids={ids}
                                  quotes={quotes}
                                  onStock={onStock}
                                  chartDate={chartDate}
                                  topIds={topIds}
                                />
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="暂无成组共振样本"
              description={
                view.gaps?.[0] || "当前分类未形成至少2只前列股票的同方向样本。"
              }
            />
          )}
          <p className="auction-topic-note">
            集中倍数＝组内前列占比 ÷
            全样本前列占比，大于1表示相对集中。仅统计本次候选，展示前15组；均幅和中位数一起看，避免少数高开股拉高平均。前列按涨幅排名，弱市也可能包含低开。
          </p>
          <div className="auction-resonance-anchor">
            <strong>一字涨停关联题材</strong>
            <Badge>待核验</Badge>
            <p>
              今日快照缺少涨停价与一字证据，暂不能确认锚股或计算剔除后的题材强度。
            </p>
          </div>
        </>
      )}
    </Panel>
  );
}
