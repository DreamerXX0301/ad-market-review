import { useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Badge, Empty, ErrorBox, Loading, Panel } from "../../components/UI";
import { integer, percent, tone } from "../../lib/format";
import { noonRows } from "./noonPresentation";
import { auctionNumber, auctionSortRows } from "./auctionPresentation";

export default function AuctionTopicSummary({
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
    `/market/auction/topic-summary?date=${encodeURIComponent(date)}`,
  );
  const view =
    result.data?.date === date &&
    result.data.version === "auction-topic-summary-v1"
      ? result.data
      : null;
  const [kind, setKind] = useState("concept");
  const [sort, setSort] = useState({
    key: "sample_count",
    direction: "desc" as "asc" | "desc",
  });
  const groups = noonRows(view?.groups).filter((row) => row.kind === kind);
  const rows = auctionSortRows(groups, sort.key, sort.direction);
  const names = new Map(
    quotes.map((row) => [row.instrument_id, row.name || row.instrument_id]),
  );
  const symbols = new Set(
    groups.flatMap((row) =>
      Array.isArray(row.instrument_ids) ? row.instrument_ids : [],
    ),
  );
  const dates = [
    ...new Set(groups.map((row) => row.members_observed_date).filter(Boolean)),
  ].sort();
  const label = kind === "concept" ? "题材" : "行业";
  function changeSort(key: string) {
    setSort({
      key,
      direction: sort.key === key && sort.direction === "desc" ? "asc" : "desc",
    });
  }
  return (
    <Panel
      title="题材数量与均幅"
      subtitle="按本次竞价候选分组，股票可归属多个方向。"
      actions={
        <div className="button-group" aria-label="题材汇总分类">
          <button
            className={`btn small${kind === "concept" ? " active" : ""}`}
            aria-pressed={kind === "concept"}
            onClick={() => setKind("concept")}
          >
            题材
          </button>
          <button
            className={`btn small${kind === "industry" ? " active" : ""}`}
            aria-pressed={kind === "industry"}
            onClick={() => setKind("industry")}
          >
            行业
          </button>
        </div>
      }
    >
      {result.loading && !view && <Loading />}
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {view && rows.length > 0 ? (
        <>
          <div className="auction-topic-meta">
            <Badge>
              {integer(rows.length)}个{label}
            </Badge>
            <span>
              已关联 {integer(symbols.size)}/{integer(view.coverage?.requested)}{" "}
              只
            </span>
            <span>
              成员档案 {dates.length === 1 ? dates[0] : "多日期，逐项查看"} ·
              竞价 {date}
            </span>
          </div>
          <div className="auction-topic-scroll">
            <table className="auction-topic-table">
              <thead>
                <tr>
                  <th>{label}</th>
                  {[
                    ["sample_count", "样本股数"],
                    ["average_auction_pct", "竞价均幅"],
                  ].map(([key, text]) => (
                    <th
                      key={key}
                      aria-sort={
                        sort.key === key
                          ? sort.direction === "desc"
                            ? "descending"
                            : "ascending"
                          : "none"
                      }
                    >
                      <button
                        className="auction-sort"
                        onClick={() => changeSort(key)}
                      >
                        {text}
                        {sort.key === key &&
                          (sort.direction === "desc" ? (
                            <ArrowDown size={12} />
                          ) : (
                            <ArrowUp size={12} />
                          ))}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={`${row.source}:${row.topic_code}:${row.members_observed_date}`}
                  >
                    <td
                      title={`${row.source} · ${row.topic_code} · 成员 ${row.members_observed_date}`}
                    >
                      {row.name}
                    </td>
                    <td>
                      <details className="auction-topic-stocks">
                        <summary>
                          {row.members_complete === false ? "≥" : ""}
                          {integer(row.sample_count)}只
                        </summary>
                        <div>
                          {(Array.isArray(row.instrument_ids)
                            ? row.instrument_ids
                            : []
                          ).map((id: string) => (
                            <button
                              key={id}
                              className="auction-stock"
                              onClick={() => onStock?.(id)}
                              disabled={!onStock || !chartDate}
                            >
                              {names.get(id) || id}
                            </button>
                          ))}
                        </div>
                      </details>
                    </td>
                    <td
                      className={tone(auctionNumber(row.average_auction_pct))}
                    >
                      {percent(auctionNumber(row.average_auction_pct))}
                      {row.valid_count !== row.sample_count && (
                        <small className="auction-muted">
                          有效{integer(row.valid_count)}/
                          {integer(row.sample_count)}
                        </small>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="auction-topic-note">
            均幅为已关联样本的等权平均，不是板块指数涨幅。数量可点开股票，各组股数不可相加。
          </p>
        </>
      ) : (
        view && (
          <Empty
            title={
              view.state === "unknown"
                ? "题材归属暂不可用"
                : `暂无可核验${label}分组`
            }
            description={view.reason || "原采集代际缺少可核验成员，保持未知。"}
          />
        )
      )}
      {view?.state === "partial" && (
        <p className="auction-topic-note">
          {view.reason || "成员资料部分覆盖，仅展示已确认关联。"}
        </p>
      )}
    </Panel>
  );
}
