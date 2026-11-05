import { stockExtraMetrics } from "./stockExtraMetrics";
import { stockHeaderQuote } from "./stockQuotePresentation";
import { num, percent, tone } from "../lib/format";

/** The fixed header reuses the existing dated summary request, independently of hover. */
export default function StockCompactMetrics({
  summary,
  detail,
  instrumentId,
  asOf,
  isIndex = false,
}: {
  summary: Record<string, any> | null;
  detail: Record<string, any> | null;
  instrumentId: string;
  asOf?: string;
  isIndex?: boolean;
}) {
  const sameSnapshot =
    typeof summary?.snapshot_id === "string" &&
    summary.snapshot_id === detail?.snapshot_id;
  const view = stockExtraMetrics(
    sameSnapshot ? summary : null,
    instrumentId,
    asOf,
  );
  const quote = stockHeaderQuote(detail, instrumentId, asOf);
  const suspended = !isIndex && view.suspended;
  const close =
    !suspended && quote.quoteDate === quote.observed ? quote.price : null;
  const dailyChange = quote.quoteDate === quote.observed ? quote.change : null;
  const daily =
    view.daily.known || suspended
      ? view.daily
      : {
          value: dailyChange == null ? "待核验" : percent(dailyChange),
          tone: dailyChange == null ? "muted" : tone(dailyChange),
          reason: quote.stale
            ? "观察日没有可核验报价，不用最近旧报价涨幅冒充今日涨幅"
            : quote.reason,
          known: dailyChange != null,
        };
  const items = [
    {
      label: "今日收盘",
      value:
        close == null
          ? suspended
            ? "—"
            : "待核验"
          : `${num(close)} ${isIndex ? "点" : "元"}`,
      tone: "",
      reason: suspended
        ? view.daily.reason
        : quote.stale
          ? "观察日缺少收盘报价"
          : quote.reason,
      known: close != null,
    },
    { label: "今日涨幅", ...daily },
    { label: "近30交易日", ...view.thirty },
    {
      label: `${view.recent.qualifier || "最近"}涨停`,
      ...view.recent,
      tone: "muted",
    },
    { label: "年内涨停", ...view.annual, tone: "muted" },
  ];
  const displayed = isIndex ? items.slice(0, 2) : items;
  return (
    <div className="entry-header-metrics" aria-label="观察日行情摘要">
      {displayed.map((item) => (
        <div
          key={item.label}
          className="entry-header-metric"
          title={item.reason}
        >
          <span>{item.label}</span>
          {suspended && item.label === "今日涨幅" ? (
            <span className="badge warn">停牌</span>
          ) : (
            <b className={item.known ? item.tone : "unknown"}>{item.value}</b>
          )}
          {"carried" in item &&
            item.carried?.map((part) => <small key={part}>{part}</small>)}
        </div>
      ))}
      {isIndex && (
        <span className="entry-header-not-applicable">指数无涨停统计</span>
      )}
    </div>
  );
}
