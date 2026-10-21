import { Badge } from "../../components/UI";
import { describeDataCoverage } from "./marketPresentation";

export default function MarketDataCoverage({
  coverage,
  loading = false,
}: {
  coverage: unknown;
  loading?: boolean;
}) {
  const view = describeDataCoverage(coverage);
  return (
    <section className="market-data-coverage" aria-label="本页数据范围">
      <div className="market-data-coverage-heading">
        <strong>数据范围</strong>
        <span>{view.label}</span>
        <span className="market-data-coverage-range">
          {loading && !view.known ? "正在核验本页覆盖范围…" : view.range}
        </span>
        {view.selectedAvailable === false && (
          <Badge tone="warn">所选日不可用</Badge>
        )}
        {view.partial && <Badge tone="warn">部分覆盖</Badge>}
      </div>
      {view.basis && (
        <p>
          <span>统计口径</span>
          {view.basis}
        </p>
      )}
      {view.notice && (!loading || view.known) && (
        <p className="market-data-coverage-notice">{view.notice}</p>
      )}
      {!!view.dates.length && (
        <details>
          <summary>
            查看 {view.dates.length} 个已留存日期 · 区间不代表逐日完整
          </summary>
          <div>{view.dates.join("　")}</div>
        </details>
      )}
    </section>
  );
}
