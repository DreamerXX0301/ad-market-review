import type { Row } from "../../lib/api";
import { amountActivityView } from "./amountActivityPresentation";
import "./AmountActivity.css";

const metrics = [
  ["change_pct", "上涨前10"],
  ["amount", "成交额前10"],
] as const;
export function SectorActivityCell({ activity }: { activity?: Row }) {
  const entries = metrics.map(([metric, label]) => ({
    metric,
    label,
    raw: activity?.[metric],
    view: amountActivityView(activity?.[metric]),
  }));
  return (
    <div className="sector-activity-cell">
      {entries.map(({ metric, label, raw, view }) => (
        <div
          key={metric}
          className="sector-activity-metric"
          title={raw?.basis || view.reason}
        >
          <span>{label}</span>
          <b className={view.repeated ? "repeated" : ""}>
            {view.count === null
              ? "待核验"
              : view.count === 0 && !view.complete
                ? "已观察未见"
                : `${view.complete ? "" : "已观察"}${view.count}次`}
          </b>
          {view.repeated && (
            <span className="amount-activity-repeat">
              <span aria-hidden="true">★</span>
              持续活跃
            </span>
          )}
          {!view.complete && <small>{view.coverage}</small>}
        </div>
      ))}
      {entries.some(({ view }) => view.details.length || view.dates.length) && (
        <details className="amount-activity-dates">
          <summary>上榜日期与名次</summary>
          {entries.map(({ metric, label, view }) => (
            <div key={metric} className="sector-activity-date-list">
              <strong>{label}</strong>
              {view.details.length ? (
                <ol>
                  {view.details.map((row) => (
                    <li key={row.date}>
                      <time>{row.date}</time>
                      <span>第{row.rank}名</span>
                    </li>
                  ))}
                </ol>
              ) : view.dates.length ? (
                <ol>
                  {view.dates.map((date) => (
                    <li key={date}>
                      <time>{date}</time>
                      <span>名次待核验</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>本次未观察到上榜日期</p>
              )}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}
export function SectorActivityCoverage({
  summary,
  kind,
  level,
}: {
  summary?: Row;
  kind: string;
  level: string;
}) {
  const groups: Row[] = Array.isArray(summary?.groups)
    ? summary.groups.filter(
        (group: Row) =>
          group.kind === kind &&
          (kind !== "industry" ||
            level === "all" ||
            String(group.level ?? "unknown") === level),
      )
    : [];
  return (
    <div
      className="sector-activity-coverage"
      aria-label="板块近七日活跃统计范围"
    >
      <strong>近7日榜单活跃</strong>
      <span>
        固定观察同类{kind === "industry" ? "、同行业层级" : ""}
        的上涨前10与成交额前10；含观察日，不随下方排序改变。
      </span>
      <div>
        {groups.length ? (
          groups.map((group, index) => (
            <span key={index} title={group.reason || summary?.scope}>
              {kind === "industry"
                ? group.level == null || group.level === "unknown"
                  ? "未知层级 · "
                  : `${group.level}级 · `
                : ""}
              {group.metric === "amount" ? "成交额前10" : "上涨前10"}：
              {typeof group.observed_days === "number"
                ? group.complete === true && group.observed_days === 7
                  ? "核验范围7/7日"
                  : `已观察${group.observed_days}/7日 · 样本不完整`
                : "观察范围待核验"}
            </span>
          ))
        ) : (
          <span>本次未取得可核验的完整榜单序列，次数保持未知。</span>
        )}
      </div>
      {groups.some((group) => group.reason) && (
        <small>
          {[...new Set(groups.map((group) => group.reason).filter(Boolean))]
            .slice(0, 2)
            .join("；")}
        </small>
      )}
      <small>
        只比较同来源指数行情；归档成员样本不混入。缺日不补0，不代表主线或资金净流入。
      </small>
    </div>
  );
}
