import type { Row } from "../../lib/api";
import {
  amountActivityDirections,
  amountActivityView,
} from "./amountActivityPresentation";
import "./AmountActivity.css";

export function AmountActivityCell({ activity }: { activity?: Row }) {
  const view = amountActivityView(activity);
  return (
    <div className="amount-activity-cell">
      <div className="amount-activity-badges">
        <span
          title={view.reason}
          className={
            "amount-activity-count " + (view.repeated ? "repeated" : "")
          }
        >
          {view.label}
        </span>
        {view.repeated && (
          <span
            className="amount-activity-repeat"
            title="在本次已观察榜单中重复出现两次及以上，不表示完整样本或未来走势"
          >
            <span aria-hidden="true">★</span>
            持续活跃
          </span>
        )}
      </div>
      {!view.complete && <small>{view.coverage}</small>}
      {(view.details.length > 0 || view.dates.length > 0) && (
        <details className="amount-activity-dates">
          <summary>上榜日期与名次</summary>
          <ol>
            {view.details.length
              ? view.details.map((row) => (
                  <li key={row.date}>
                    <time>{row.date}</time>
                    <span>第{row.rank}名</span>
                  </li>
                ))
              : view.dates.map((date) => (
                  <li key={date}>
                    <time>{date}</time>
                    <span>名次待核验</span>
                  </li>
                ))}
          </ol>
        </details>
      )}
    </div>
  );
}
export function AmountActivitySummary({
  summary,
  asOf,
}: {
  summary?: Row;
  asOf?: string;
}) {
  const directions = amountActivityDirections(summary, asOf);
  const coverage = amountActivityView(summary);
  const complete =
    summary?.complete === true &&
    summary?.expected_days === 7 &&
    summary?.observed_days === 7;
  const sourceReason =
    typeof summary?.reason === "string"
      ? summary.reason
      : "近7日榜单样本不完整；次数仅说明已观察的本地排名";
  return (
    <div
      className="amount-activity-summary"
      aria-label="成交额TOP10近七日活跃观察"
    >
      <div className="amount-activity-summary-head">
        <strong>活跃方向观察</strong>
        <span>
          {summary?.window_start || "起点待核验"} ～{" "}
          {summary?.window_end || asOf || "观察日待核验"} ·{" "}
          {complete ? "核验范围7/7日" : coverage.coverage}
        </span>
      </div>
      {directions.length ? (
        <>
          <div className="amount-activity-direction-tags">
            {directions.slice(0, 4).map((direction) => (
              <span key={direction.topic_id || direction.name}>
                {direction.name}
                <small>
                  {direction.kind === "industry" ? "行业" : "概念"} ·{" "}
                  {direction.stock_count}只重复上榜
                </small>
              </span>
            ))}
          </div>
          <details className="amount-activity-direction-details">
            <summary>
              查看方向依据
              {directions.length > 4
                ? `与其余${directions.length - 4}个方向`
                : ""}
            </summary>
            <div>
              {directions.map((direction) => (
                <article key={direction.topic_id || direction.name}>
                  <strong>{direction.name}</strong>
                  <small>题材观察日 {direction.observed_date}</small>
                  <ul>
                    {(Array.isArray(direction.items)
                      ? direction.items
                      : []
                    ).map((stock: Row) => (
                      <li key={stock.instrument_id}>
                        <span>{stock.name || stock.instrument_id}</span>
                        <span>{stock.instrument_id}</span>
                        <b>
                          {stock.count == null
                            ? "次数待核验"
                            : `${complete ? "" : "已观察"}${stock.count}次`}
                        </b>
                      </li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </details>
        </>
      ) : (
        <p>
          {summary?.directions?.reason ||
            "本次未取得重复上榜股票的观察日可核验题材归属，暂不汇总方向。"}
        </p>
      )}
      {directions.length > 0 &&
        summary?.directions?.state === "partial" &&
        summary.directions.reason && (
          <p className="amount-activity-scope">{summary.directions.reason}</p>
        )}
      <p className="amount-activity-scope">
        {!complete && sourceReason + "；"}
        仅汇总当日本地TOP10样本中重复观察到的股票及观察日题材，题材之间可重叠；不代表主线、资金净流入或上涨原因。
      </p>
    </div>
  );
}
