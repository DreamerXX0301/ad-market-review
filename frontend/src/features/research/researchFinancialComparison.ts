import type { Row } from "../../lib/api";

export type ResearchFinancialComparison = {
  delta: number | null;
  percent: number | null;
  previousDate: string | null;
  reason: string | null;
  label: string | null;
};

const fields = new Set([
  "operating_income",
  "parent_holder_net_profit",
  "net_profit",
  "basic_eps",
  "act_cash_flow_net",
  "invest_cash_flow_net",
  "financing_cash_flow_net",
  "assets_total",
  "total_debt",
  "holder_equity_total",
]);
const profitFields = new Set([
  "parent_holder_net_profit",
  "net_profit",
  "basic_eps",
]);
const scopeFields = [
  "source",
  "thscode",
  "currency",
  "period",
  "fiscal_period",
  "unit",
] as const;
const periodEnds: Record<string, string> = {
  Q1: "03-31",
  Q2: "06-30",
  Q3: "09-30",
  Q4: "12-31",
  FY: "12-31",
};
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const record = (value: unknown): value is Row =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const unknown = (
  reason: string,
  previousDate: string | null = null,
): ResearchFinancialComparison => ({
  delta: null,
  percent: null,
  previousDate,
  reason,
  label: null,
});

function isoDate(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return null;
  const time = Date.parse(value + "T00:00:00Z");
  return Number.isFinite(time) &&
    new Date(time).toISOString().slice(0, 10) === value
    ? value
    : null;
}

function metadataReason(row: Row): string | null {
  const date = isoDate(row.report_period);
  if (!date) return "报告期日期待核验";
  if (
    scopeFields.some((key) => typeof row[key] !== "string" || !row[key].trim())
  )
    return "证券、来源、币种或报表口径元数据不完整";
  if (!/^\d{6}\.(SH|SZ|BJ)$/.test(row.thscode)) return "证券标识待核验";
  if (!/^[A-Z]{3}$/.test(row.currency)) return "报表币种待核验";
  if (
    !Number.isInteger(row.fiscal_year) ||
    row.fiscal_year < 1 ||
    row.fiscal_year > 9999 ||
    row.fiscal_year !== Number(date.slice(0, 4)) ||
    periodEnds[row.fiscal_period] !== date.slice(5)
  )
    return "财年、季度与报告期末不一致";
  // `period` is the source's report filter, not evidence of a single-quarter
  // amount. Never convert cumulative amounts or silently equate FY with Q4.
  if (
    (row.period !== "annual" && row.period !== "quarterly") ||
    (row.period === "annual" && !["FY", "Q4"].includes(row.fiscal_period)) ||
    (row.period === "quarterly" && row.fiscal_period === "FY")
  )
    return "报表期间口径待核验";
  const periodEnd = Date.parse(date + "T00:00:00+08:00");
  if (
    !finite(row.period_end_ms) ||
    !Number.isInteger(row.period_end_ms) ||
    row.period_end_ms !== periodEnd
  )
    return "来源期末时间与报告期日期不一致";
  if (
    !finite(row.report_date_ms) ||
    !Number.isInteger(row.report_date_ms) ||
    !finite(row.published_at_ms) ||
    !Number.isInteger(row.published_at_ms) ||
    row.report_date_ms !== row.published_at_ms ||
    row.report_date_ms < periodEnd ||
    !Number.isFinite(new Date(row.report_date_ms).getTime())
  )
    return "已披露时间元数据待核验";
  return null;
}

/** Compare only the frozen, same-source prior-year reporting period. */
export function researchFinancialComparison(
  rows: Row[],
  current: Row,
  field: string,
): ResearchFinancialComparison {
  if (!fields.has(field)) return unknown("该字段不支持同比比较");
  if (!record(current) || !Array.isArray(rows))
    return unknown("报表记录待核验");
  const currentReason = metadataReason(current);
  if (currentReason) return unknown(currentReason);
  const currentDate = current.report_period as string;
  const sameCurrent = rows.filter(
    (row) => record(row) && row.report_period === currentDate,
  );
  if (sameCurrent.length > 1) return unknown("本期存在重复报告期记录");
  if (
    sameCurrent.length === 1 &&
    (metadataReason(sameCurrent[0]) ||
      scopeFields.some((key) => sameCurrent[0][key] !== current[key]) ||
      sameCurrent[0][field] !== current[field])
  )
    return unknown("本期记录与冻结报表序列不一致");
  const previousDate =
    String(current.fiscal_year - 1).padStart(4, "0") + currentDate.slice(4);
  const previousRows = rows.filter(
    (row) => record(row) && row.report_period === previousDate,
  );
  if (!previousRows.length) return unknown("未取得上年相同报告期");
  if (previousRows.length !== 1) return unknown("上年同期存在重复报告期记录");
  const previous = previousRows[0];
  const previousReason = metadataReason(previous);
  if (previousReason) return unknown("上年同期：" + previousReason);
  if (scopeFields.some((key) => previous[key] !== current[key]))
    return unknown("上年同期证券、来源、币种或报表口径不一致");
  if (previous.report_date_ms > current.report_date_ms)
    return unknown("上年同期披露时间晚于本期，比较版本待核验");
  const value = current[field];
  const prior = previous[field];
  if (!finite(value) || !finite(prior))
    return unknown("本期或上年同期数值待核验", previousDate);
  const difference = value - prior;
  if (!Number.isFinite(difference))
    return unknown("同比差额超出可核验数值范围", previousDate);
  const delta = difference === 0 ? 0 : difference;
  const rawPercent = prior > 0 ? (delta / prior) * 100 : null;
  const percent =
    rawPercent !== null && Number.isFinite(rawPercent)
      ? rawPercent === 0
        ? 0
        : rawPercent
      : null;
  let label: string | null = null;
  if (profitFields.has(field)) {
    if (prior < 0 && value > 0) label = "扭亏为盈";
    else if (prior >= 0 && value < 0) label = "转亏";
    else if (prior < 0 && value <= 0) {
      if (delta > 0) label = "亏损收窄";
      else if (delta < 0) label = "亏损扩大";
      else label = "亏损持平";
    }
  }
  return {
    delta,
    percent,
    previousDate,
    reason:
      prior === 0
        ? "上年同期为零，同比比例不适用"
        : prior < 0
          ? "上年同期为负，同比比例不适用"
          : percent === null
            ? "同比比例超出可核验数值范围"
            : null,
    label,
  };
}
