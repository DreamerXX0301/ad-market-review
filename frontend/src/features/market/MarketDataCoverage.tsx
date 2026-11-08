import { ChevronDown } from "lucide-react";
import { Badge } from "../../components/UI";
import { describeDataCoverage } from "./marketPresentation";

export default function MarketDataCoverage({
  coverage,
  loading = false,
  compact = false,
}: {
  coverage: unknown;
  loading?: boolean;
  compact?: boolean;
}) {
  const view = describeDataCoverage(coverage);
  const range = (
    <>
      <span className="market-data-coverage-range">
        {loading && !view.known ? "正在核验本页覆盖范围…" : view.range}
      </span>
      {view.selectedAvailable === false && (
        <Badge tone="warn">所选日不可用</Badge>
      )}
      {view.partial && <Badge tone="warn">部分覆盖</Badge>}
    </>
  );
  const basis = view.basis && (
    <p>
      <span>统计口径</span>
      {view.basis}
    </p>
  );
  const notice = view.notice && (!loading || view.known) && (
    <p className="market-data-coverage-notice">{view.notice}</p>
  );
  const retainedDates = !!view.dates.length && (
    <details>
      <summary>
        查看 {view.dates.length} 个已留存日期 · 区间不代表逐日完整
      </summary>
      <div>{view.dates.join("　")}</div>
    </details>
  );

  if (compact) {
    const showNotice = view.selectedAvailable !== true;
    return (
      <section
        className="market-data-coverage market-data-coverage-compact"
        aria-label="本页数据范围"
      >
        <details className="market-data-coverage-details">
          <summary className="market-data-coverage-heading">
            <strong>数据范围</strong>
            {range}
            <span className="market-data-coverage-disclosure">
              查看口径 <ChevronDown size={14} aria-hidden="true" />
            </span>
          </summary>
          <div className="market-data-coverage-explanation">
            <p>{view.label}</p>
            {basis}
            {!showNotice && notice}
            {retainedDates}
          </div>
        </details>
        {showNotice && notice}
      </section>
    );
  }

  return (
    <section className="market-data-coverage" aria-label="本页数据范围">
      <div className="market-data-coverage-heading">
        <strong>数据范围</strong>
        <span>{view.label}</span>
        {range}
      </div>
      {basis}
      {notice}
      {retainedDates}
    </section>
  );
}
