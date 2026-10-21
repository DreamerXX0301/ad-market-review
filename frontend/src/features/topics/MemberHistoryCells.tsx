import type { Row } from "../../lib/api";
import { integer, percent, tone } from "../../lib/format";
import { Badge } from "../../components/UI";

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number =>
  finite(value) && Number.isInteger(value) && value >= 0;

export function HistoryReturnCell({
  value,
  reason,
  start,
  end,
  priceBasis,
  missingSessions,
}: {
  value: unknown;
  reason?: string | null;
  start?: string | null;
  end?: string | null;
  priceBasis?: string | null;
  missingSessions?: number | null;
}) {
  const known = finite(value);
  const basis =
    priceBasis === "forward_event_affine"
      ? "已核验本地事件前复权端点"
      : priceBasis === "none"
        ? "未复权可比端点"
        : priceBasis || "以服务端核验口径为准";
  const details = [start && end ? start + " → " + end : null, basis, reason]
    .filter(Boolean)
    .join("；");
  return (
    <div
      className="topic-history-cell"
      title={details || "历史端点或公司行动口径待核验，未知不补0"}
    >
      <span className={known ? tone(value) : "muted"}>
        {known ? percent(value) : "—"}
      </span>
      {!known && <small>待核验</small>}
      {known && count(missingSessions) && missingSessions > 0 && (
        <small>区间缺 {missingSessions} 日报价</small>
      )}
    </div>
  );
}

export function YearLimitCountCell({ row }: { row: Row }) {
  const exact = count(row.limit_up_ytd),
    observed =
      count(row.limit_up_ytd_observed) && row.limit_up_ytd_observed > 0;
  const coverage = row.limit_up_ytd_coverage || {};
  const coverageText =
    count(coverage.covered_days) && count(coverage.expected_days)
      ? `核验覆盖 ${coverage.covered_days}/${coverage.expected_days} 个交易日`
      : "年内来源覆盖待核验";
  return (
    <div
      className="topic-history-cell"
      title={[
        row.limit_up_ytd_reason,
        coverageText,
        "按逐日已核验来源收盘涨停池计数，不等于盘中触板次数",
      ]
        .filter(Boolean)
        .join("；")}
    >
      {exact ? (
        <span>{integer(row.limit_up_ytd)} 次</span>
      ) : (
        <Badge tone="warn">
          {observed
            ? `已确认≥${integer(row.limit_up_ytd_observed)}次`
            : "覆盖不足"}
        </Badge>
      )}
      {!exact && (
        <small>
          {observed ? "覆盖不足" : "未确认次数"}
          {count(coverage.covered_days) && count(coverage.expected_days)
            ? ` · ${coverage.covered_days}/${coverage.expected_days}日`
            : ""}
        </small>
      )}
    </div>
  );
}

export function RecentLimitCell({ row }: { row: Row }) {
  const date =
    typeof row.last_limit_up_date === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(row.last_limit_up_date)
      ? row.last_limit_up_date
      : null;
  const complete = row.last_limit_up_complete === true;
  const sessions = row.sessions_since_limit_up;
  const coverage = row.last_limit_up_coverage || {};
  const reason = row.last_limit_up_reason;
  const scope = complete
    ? "最近事件至业务日的状态已完整核验"
    : "存在历史覆盖缺口，只能确认已留存事件；之后可能仍有未知事件";
  return (
    <div
      className="topic-history-cell topic-recent-limit"
      title={[
        reason,
        scope,
        coverage.since && coverage.until
          ? `核验范围 ${coverage.since} → ${coverage.until}`
          : null,
        "距今按交易日历计算；此列描述股票本身历史，不代表当时属于当前分类",
      ]
        .filter(Boolean)
        .join("；")}
    >
      {date ? (
        <>
          <span>{date}</span>
          {!complete && (
            <small className="topic-history-warning">最近已确认</small>
          )}
          <small>
            {count(sessions)
              ? `距今 ${integer(sessions)} 个交易日`
              : "交易日距离待核验"}
          </small>
        </>
      ) : (
        <>
          <span className="muted">—</span>
          <small>未有可确认日期</small>
        </>
      )}
    </div>
  );
}

export function MemberMetricCoverage({ coverage }: { coverage?: Row | null }) {
  if (!coverage || !count(coverage.total)) return null;
  const fields = [
    ["return_ytd_complete", "年内涨幅", coverage.price_scope],
    ["change_30d_complete", "近30交易日涨幅", coverage.price_scope],
    [
      "limit_up_ytd_complete",
      "年内涨停次数",
      "按当前结果中全年逐日核验完整的股票计数；下界次数不计入完整数。",
    ],
    [
      "last_limit_up_complete",
      "最近涨停状态",
      "最近事件至业务日后缀完整即可确认，年度早期缺口不影响该状态；" +
        (coverage.last_limit_scope || ""),
    ],
  ];
  return (
    <div className="topic-metric-coverage" aria-label="历史指标核验覆盖">
      <span>当前结果可核验</span>
      {fields.map(([key, label, reason]) => (
        <span
          key={key}
          title={reason || "满足完整核验条件的当前结果股票数，未知不补0"}
          className={
            count(coverage[key]) && coverage[key] < coverage.total
              ? "topic-history-warning"
              : ""
          }
        >
          {label} {count(coverage[key]) ? integer(coverage[key]) : "—"}/
          {integer(coverage.total)} 只
        </span>
      ))}
    </div>
  );
}
