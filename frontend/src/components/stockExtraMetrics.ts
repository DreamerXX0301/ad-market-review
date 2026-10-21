type Summary = Record<string, any>;
const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
export function stockMetricDate(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return null;
  const date = new Date(value + "T00:00:00Z");
  return Number.isFinite(date.getTime()) &&
    date.toISOString().slice(0, 10) === value
    ? value
    : null;
}
export function stockSummaryEndpoint(id: string, asOf?: string): string | null {
  if (!/^\d{6}\.(SH|SZ|BJ)$/.test(id.toUpperCase())) return null;
  if (asOf && !stockMetricDate(asOf)) return null;
  return `/instruments/${encodeURIComponent(id.toUpperCase())}/summary${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`;
}
export function stockSummaryMatches(
  data: Summary | null | undefined,
  id: string,
  asOf?: string,
): boolean {
  const date = stockMetricDate(data?.data_date);
  return (
    !!data &&
    data.instrument_id === id.toUpperCase() &&
    !!date &&
    (!asOf || date === asOf)
  );
}
export function stockExtraMetrics(
  data: Summary | null | undefined,
  id: string,
  asOf?: string,
) {
  const valid = stockSummaryMatches(data, id, asOf),
    date = valid ? data!.data_date : asOf;
  const row = valid ? data! : {};
  const exact =
    count(row.limit_up_ytd) && row.limit_up_ytd_coverage?.complete !== false;
  const observed =
    count(row.limit_up_ytd_observed) && row.limit_up_ytd_observed > 0;
  const annual = {
    value: exact
      ? `${row.limit_up_ytd} 次`
      : observed
        ? `已确认≥${row.limit_up_ytd_observed}次`
        : "待核验",
    note: exact
      ? "已核验年内收盘涨停"
      : observed
        ? "来源覆盖不足 · 保留已确认下界"
        : "来源覆盖不足 · 未确认次数",
    reason:
      row.limit_up_ytd_reason ||
      "仅统计已留存、逐日核验的收盘涨停，不等于盘中触板次数",
    coverage: row.limit_up_ytd_coverage,
    known: exact || observed,
  };
  const candidate = stockMetricDate(row.last_limit_up_date),
    latest = candidate && date && candidate <= date ? candidate : null;
  const recent = {
    value: latest || "待核验",
    note: latest
      ? count(row.sessions_since_limit_up)
        ? `距观察日 ${row.sessions_since_limit_up} 个交易日`
        : "交易日距离待核验"
      : "未有可确认日期",
    qualifier: latest
      ? row.last_limit_up_complete === true
        ? "最近一次"
        : "最近已确认"
      : "",
    reason:
      candidate && !latest
        ? "事件日期晚于观察日，已隔离"
        : row.last_limit_up_reason ||
          "仅依据已留存涨停事件；未知不等于从未涨停",
    coverage: row.last_limit_up_coverage,
    known: !!latest,
  };
  const historicalReturn = (prefix: string, label: string) => {
    const value = row[prefix + "_pct"],
      coverage = row[prefix + "_coverage"] || {};
    const end = stockMetricDate(coverage.end),
      safeEnd = !end || (!!date && end <= date);
    const known =
      typeof value === "number" && Number.isFinite(value) && safeEnd;
    const basis =
      coverage.price_basis === "forward_event_affine"
        ? "已核验本地公司行动连续价格"
        : coverage.price_basis === "none"
          ? "未复权可比端点"
          : "价格口径以服务端核验为准";
    return {
      value: known
        ? `${value > 0 ? "+" : ""}${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
        : "待核验",
      tone: known ? (value > 0 ? "up" : value < 0 ? "down" : "muted") : "muted",
      known,
      note: known
        ? coverage.start
          ? `${coverage.start} → ${date}`
          : label
        : "基准、报价或公司行动口径待核验",
      reason: !safeEnd
        ? "收益端点晚于观察日，已隔离"
        : [
            row[prefix + "_reason"],
            basis,
            "已实现历史涨幅，不是预测或账户含分红收益",
          ]
            .filter(Boolean)
            .join("；"),
    };
  };
  return {
    date,
    valid,
    annual,
    recent,
    ytd: historicalReturn("return_ytd", "当年涨幅"),
    thirty: historicalReturn("change_30d", "前30个市场交易日端点"),
  };
}
