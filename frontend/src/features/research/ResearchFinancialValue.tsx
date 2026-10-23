import type { Row } from "../../lib/api";
import { amount, num } from "../../lib/format";
import { researchFinancialComparison } from "./researchFinancialComparison";

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const direction = (value: unknown) =>
  !finite(value) || value === 0 ? "flat" : value > 0 ? "increase" : "decrease";
const signedFields = new Set([
  "parent_holder_net_profit",
  "net_profit",
  "basic_eps",
  "act_cash_flow_net",
  "invest_cash_flow_net",
  "financing_cash_flow_net",
]);

/** Report values and changes have separate color meanings; never infer growth from a positive balance. */
export function ResearchFinancialValue({
  rows,
  row,
  field,
}: {
  rows: Row[];
  row: Row;
  field: string;
}) {
  const value = row[field];
  const format = (value: number) =>
    field === "basic_eps" ? num(value) + " 元/股" : amount(value);
  const change = researchFinancialComparison(rows, row, field);
  const delta = change.delta;
  const title =
    delta === null
      ? change.reason || "未取得可比的上年同期数据"
      : [
          "较上年同期 " + change.previousDate,
          (delta > 0 ? "+" : "") + format(delta),
          change.reason,
        ]
          .filter(Boolean)
          .join(" · ");
  const comparison =
    delta === null
      ? "同比待核验"
      : delta === 0
        ? "同比持平"
        : finite(change.percent)
          ? "同比 " +
            (change.percent > 0 ? "+" : "") +
            num(change.percent) +
            "%"
          : "较同期 " + (delta > 0 ? "+" : "") + format(delta);
  return (
    <span className="research-financial-value">
      <span
        className={
          signedFields.has(field)
            ? "research-change " + direction(value)
            : undefined
        }
      >
        {finite(value) ? format(value) : "—"}
      </span>
      <small
        className={
          "research-financial-comparison research-change " + direction(delta)
        }
        title={title}
      >
        {[change.label, comparison].filter(Boolean).join(" · ")}
      </small>
    </span>
  );
}

export function ResearchFinancialColorGuide() {
  return (
    <p className="research-financial-color-guide">
      同比：<span className="research-change increase">红增</span>
      <span className="research-change decrease">绿减</span>；利润与净现金流：
      <span className="research-change increase">红正</span>
      <span className="research-change decrease">绿负</span>
      ；持平或待核验为灰色。 增减仅比较同口径上年同期，颜色不代表利好或利空。
    </p>
  );
}
