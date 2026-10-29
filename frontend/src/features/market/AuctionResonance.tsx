import { Fragment, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
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
  const [sort, setSort] = useState({
    key: "concentration_lift",
    direction: "desc" as "asc" | "desc",
  });
  const groups = noonRows(view?.groups).filter(
    (row) => (kind === "all" || row.kind === kind) && row.top_count >= 2,
  );
  const sorted =
    sort.key === "name"
      ? [...groups].sort((a, b) => {
          const an =
            typeof a.name === "string" && a.name.trim() ? a.name.trim() : null;
          const bn =
            typeof b.name === "string" && b.name.trim() ? b.name.trim() : null;
          if (an === null || bn === null)
            return an === bn ? 0 : an === null ? 1 : -1;
          return (
            an.localeCompare(bn, "zh-CN") * (sort.direction === "asc" ? 1 : -1)
          );
        })
      : auctionSortRows(groups, sort.key, sort.direction);
  const rows = sorted.slice(0, 15);
  function changeSort(key: string) {
    setSort({
      key,
      direction:
        sort.key === key
          ? sort.direction === "desc"
            ? "asc"
            : "desc"
          : key === "name"
            ? "asc"
            : "desc",
    });
  }
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
                    {[
                      ["name", "概念 / 行业"],
                      ["top_count", "前列命中"],
                      ["concentration_lift", "集中倍数"],
                      ["average_auction_pct", "竞价均幅"],
                      ["median_auction_pct", "中位数"],
                      ["up_ratio", "高开占比"],
                    ].map(([key, label]) => (
                      <th
                        key={key}
                        aria-sort={
                          sort.key === key
                            ? sort.direction === "asc"
                              ? "ascending"
                              : "descending"
                            : "none"
                        }
                      >
                        <button
                          type="button"
                          className="auction-sort"
                          aria-label={`按${label}排序`}
                          title={`点击按${label}${(sort.key === key && sort.direction === "desc") || (sort.key !== key && key === "name") ? "升序" : "降序"}`}
                          onClick={() => changeSort(key)}
                        >
                          {label}
                          {sort.key === key ? (
                            sort.direction === "desc" ? (
                              <ArrowDown size={12} />
                            ) : (
                              <ArrowUp size={12} />
                            )
                          ) : (
                            <ArrowUpDown size={12} />
                          )}
                        </button>
                      </th>
                    ))}
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
                        <tr
                          className={`auction-table-group-row${open ? " is-expanded" : ""}`}
                          tabIndex={0}
                          aria-expanded={open}
                          onClick={(event) => {
                            if (
                              !(event.target as Element).closest(
                                "button,a,input",
                              )
                            )
                              setExpanded(open ? "" : key);
                          }}
                          onKeyDown={(event) => {
                            if (
                              event.target === event.currentTarget &&
                              (event.key === "Enter" || event.key === " ")
                            ) {
                              event.preventDefault();
                              setExpanded(open ? "" : key);
                            }
                          }}
                        >
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
                                  key={`${date}:${row.source}:${row.kind}:${row.topic_code}`}
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
            全样本前列占比，大于1表示相对集中。仅统计本次候选，按当前排序展示前15组；均幅和中位数一起看，避免少数高开股拉高平均。前列按涨幅排名，弱市也可能包含低开。
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
