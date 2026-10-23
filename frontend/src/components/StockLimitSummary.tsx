import {
  CalendarClock,
  Flame,
  RefreshCw,
  TrendingUp,
  ChartNoAxesCombined,
  Activity,
} from "lucide-react";
import { useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { stockExtraMetrics, stockSummaryEndpoint } from "./stockExtraMetrics";
import "./StockLimitSummary.css";

export default function StockLimitSummary({
  instrumentId,
  asOf,
}: {
  instrumentId: string;
  asOf?: string;
}) {
  const result = useApi<Row>(stockSummaryEndpoint(instrumentId, asOf));
  const view = stockExtraMetrics(result.data, instrumentId, asOf);
  const coverage = view.annual.coverage;
  const annualCoverage =
    coverage &&
    Number.isInteger(coverage.covered_days) &&
    Number.isInteger(coverage.expected_days)
      ? `核验覆盖 ${coverage.covered_days}/${coverage.expected_days} 个交易日`
      : null;
  return (
    <div className="stock-limit-summary-container">
      <div className="stock-limit-summary" aria-label="个股行情与涨停历史摘要">
        <div className="stock-limit-metric daily" title={view.daily.reason}>
          <span className="stock-limit-symbol daily">
            <Activity size={17} />
          </span>
          <div>
            <span>今日涨幅</span>
            <strong className={view.daily.known ? view.daily.tone : "unknown"}>
              {result.loading && !view.valid ? "核验中…" : view.daily.value}
            </strong>
            <small>{view.daily.note}</small>
          </div>
        </div>
        <div className="stock-limit-metric return" title={view.thirty.reason}>
          <span className="stock-limit-symbol recent">
            <ChartNoAxesCombined size={17} />
          </span>
          <div>
            <span>近30交易日涨幅</span>
            <strong
              className={view.thirty.known ? view.thirty.tone : "unknown"}
            >
              {result.loading && !view.valid ? "核验中…" : view.thirty.value}
            </strong>
            <small className="stock-limit-range">
              {view.thirty.note
                .split(" → ")
                .map((part: string, index: number) => (
                  <span key={index}>
                    {index > 0 && "→ "}
                    {part}
                  </span>
                ))}
            </small>
          </div>
        </div>
        <div className="stock-limit-metric" title={view.recent.reason}>
          <span className="stock-limit-symbol recent">
            <CalendarClock size={17} />
          </span>
          <div>
            <span>{view.recent.qualifier || "最近一次"}涨停日期</span>
            <strong className={view.recent.known ? "" : "unknown"}>
              {result.loading && !view.valid ? "核验中…" : view.recent.value}
            </strong>
            <small>{view.recent.note}</small>
          </div>
        </div>
        <div className="stock-limit-metric return" title={view.ytd.reason}>
          <span className="stock-limit-symbol recent">
            <TrendingUp size={17} />
          </span>
          <div>
            <span>
              年内涨幅 <small>{view.date?.slice(0, 4) || "当年"}年</small>
            </span>
            <strong className={view.ytd.known ? view.ytd.tone : "unknown"}>
              {result.loading && !view.valid ? "核验中…" : view.ytd.value}
            </strong>
            <small className="stock-limit-range">
              {view.ytd.note.split(" → ").map((part: string, index: number) => (
                <span key={index}>
                  {index > 0 && "→ "}
                  {part}
                </span>
              ))}
            </small>
          </div>
        </div>
        <div
          className="stock-limit-metric"
          title={[view.annual.reason, annualCoverage]
            .filter(Boolean)
            .join("；")}
        >
          <span className="stock-limit-symbol">
            <Flame size={17} />
          </span>
          <div>
            <span>
              年内涨停次数{" "}
              <small>{view.date ? `截至 ${view.date}` : "观察日待核验"}</small>
            </span>
            <strong className={view.annual.known ? "" : "unknown"}>
              {result.loading && !view.valid ? "核验中…" : view.annual.value}
            </strong>
            <small>
              {annualCoverage
                ? `覆盖 ${coverage.covered_days}/${coverage.expected_days} 日${coverage.expected_days > coverage.covered_days ? ` · ${coverage.expected_days - coverage.covered_days} 日待核验` : ""}`
                : view.annual.note}
            </small>
          </div>
        </div>
        {result.error && (
          <button
            className="stock-limit-retry"
            title={result.error}
            onClick={result.reload}
          >
            <RefreshCw size={12} />
            统计暂不可用 · 重试
          </button>
        )}
        {result.data && !view.valid && (
          <span className="stock-limit-error">
            统计身份或日期不匹配，未显示旧值。
          </span>
        )}
      </div>
    </div>
  );
}
