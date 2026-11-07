import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { Row } from "../../lib/api";
import { num, textValue } from "../../lib/format";
import TopicSelect from "./TopicSelect";
import BollConditionEditor from "./BollConditionEditor";
import { BOLL_DEFAULTS } from "./historicalRules";
import { yearlyLimitCountText } from "./conditionEvidence";
import {
  conditionFieldGroups,
  trackingConditionAllowed,
} from "./conditionScope";
import type { ConditionScope } from "./conditionScope";
import {
  historyCheckText,
  historyRuleSummary,
  conditionReasonText,
  HIDDEN_CONDITION_TEXT,
  isHiddenConditionField,
  isHiddenConditionRule,
  conditionComparisonObject,
  withConditionComparison,
  eventPriceTargetText,
  eventPriceOffsetError,
} from "./historicalRules";
import type { ComparisonObject } from "./historicalRules";
import {
  HIGHER_LOWS_FIELD,
  HIGHER_LOWS_OPERATOR,
  HIGHER_LOWS_DEFAULT,
  HIGHER_LOWS_MIN,
  HIGHER_LOWS_MAX,
  higherLowsSummary,
  higherLowsWindowUnit,
  higherLowsWindowError,
} from "./historicalRules";
import {
  conditionPeriodView,
  conditionPeriodSummary,
  periodLabel,
  withConditionPeriod,
  metricCycleLabel,
} from "./conditionPeriod";
export function ruleValue(field: string, value: any): string {
  if (isHiddenConditionField(field)) return "—";
  if (field === "close_breakout_high") return `${textValue(value)}个交易日`;
  if (field === "boll_signal") return historyRuleSummary({ field, value });
  if (field === "board") {
    const boardNames: Record<string, string> = {
      main: "主板",
      chinext: "创业板",
      star: "科创板",
      beijing: "北交所",
      unknown: "板块待核验",
    };
    return Array.isArray(value)
      ? value.map((v) => boardNames[v] || v).join("、")
      : boardNames[value] || textValue(value);
  }
  return typeof value === "boolean" ? (value ? "是" : "否") : textValue(value);
}
export const ruleOperator = (op: string) =>
  (
    ({
      in: "仅包含",
      not_in: "剔除",
      contains: "包含",
      not_contains: "不包含",
      "==": "等于",
      "!=": "不等于",
      new_high: "回看",
      matches: "满足",
    }) as Record<string, string>
  )[op] || op;
export function fieldLabel(field: string, provided?: string): string {
  if (!field) return "组合条件";
  if (isHiddenConditionField(field)) return "已隐藏的指标";
  if (field === "kd_cross") return "KDJ金叉（K线上穿D线）";
  if (field === "kd_dead_cross") return "KDJ死叉（K线下穿D线）";
  if (field === "close_breakout_high") return "收盘价突破前N日最高价";
  if (field === HIGHER_LOWS_FIELD) return "底部抬高（确认后未失守）";
  if (field === "boll_signal") return "BOLL 收口与方向";
  if (field === "volume_expand") return "成交量大于前一周期（放量）";
  if (field === "volume_shrink") return "成交量小于前一周期（缩量）";
  if (provided && provided !== field) return provided;
  const labels: Record<string, string> = {
    volume: "成交量",
    amount: "成交额",
    turnover_ratio_pct: "换手率 (%)",
    macd_dif: "MACD DIF",
    macd_dea: "MACD DEA",
    macd_hist: "MACD 柱",
    kdj_k: "KDJ K",
    kdj_d: "KDJ D",
    volume_ratio: "较前一可比周期量能倍数",
    close: "收盘价",
    open: "开盘价",
    high: "最高价",
    low: "最低价",
    change_pct: "周期涨跌幅 (%)",
    intraday_high_change_pct: "当日最高涨幅（相对前收，%）",
    macd_cross: "MACD 金叉",
    macd_dead_cross: "MACD 死叉",
    kd_dead_cross: "KDJ死叉（K线下穿D线）",
    bullish_ma_alignment: "均线多头排列（5>10>20>30>60）",
    kd_cross: "KDJ金叉（K线上穿D线）",
    limit_up: "今日是否涨停（收盘）",
    prev_limit_up: "昨日是否涨停（前一市场交易日）",
    limit_up_1y: "截至昨日近一年曾涨停",
    limit_up_count_1y: "截至昨日近一年涨停次数",
    volume_expand: "今日放量",
    volume_shrink: "今日缩量",
    close_below_entry: "跌破入池事件日收盘",
    close_below_entry_low: "收盘跌破本轮事件日最低价",
    close_above_entry_high: "收盘价高于本轮事件日最高价",
    trading_age: "本轮跟踪交易日",
    tracking_change_pct: "本轮跟踪涨幅 (%)",
    topic_ids: "题材归属",
    board: "上市板块",
    is_st: "当前 ST 标识",
  };
  if (labels[field]) return labels[field];
  const ma = field.match(/^ma(\d+)$/);
  if (ma) return "MA" + ma[1];
  const cross = field.match(/^(close_above|close_below|cross_above)_ma(\d+)$/);
  if (cross)
    return (
      (
        {
          close_above: "收盘价大于 ",
          close_below: "收盘价低于 ",
          cross_above: "收盘上穿 ",
        } as Record<string, string>
      )[cross[1]] +
      "MA" +
      cross[2]
    );
  return field;
}
export function entryHighEvidenceText(check: any): string | null {
  if (check.field !== "close_above_entry_high") return null;
  const proof = check.evidence?.entry_high;
  const high = proof?.raw_high;
  const actual =
    check.actual === true ? "是" : check.actual === false ? "否" : "待核验";
  return (
    `实际${actual}；` +
    (proof?.state === "ready" &&
    typeof high === "number" &&
    Number.isFinite(high) &&
    high > 0
      ? `事件日 ${proof.date || "待核验"} 最高价 ${num(high)}`
      : "事件日最高价待核验")
  );
}
export function entryLowEvidenceText(check: any): string | null {
  if (check.field !== "close_below_entry_low") return null;
  const proof = check.evidence?.entry_low;
  const low = proof?.raw_low;
  const actual =
    check.actual === true ? "是" : check.actual === false ? "否" : "待核验";
  return (
    `实际${actual}；` +
    (proof?.state === "ready" &&
    typeof low === "number" &&
    Number.isFinite(low) &&
    low > 0
      ? `事件日 ${proof.date || "待核验"} 最低价 ${num(low)}`
      : "事件日最低价待核验")
  );
}
export function summarizeChecks(
  checks: any,
  onlySatisfied = false,
  limit = 6,
): string {
  const flatten = (node: any): any[] =>
    Array.isArray(node)
      ? node.flatMap(flatten)
      : node?.type === "history_event" ||
          node?.type === "hot_universe" ||
          node?.history_event
        ? [node]
        : node?.checks
          ? flatten(node.checks)
          : node?.all
            ? flatten(node.all)
            : node?.any
              ? flatten(node.any)
              : node?.conditions
                ? flatten(node.conditions)
                : node?.field
                  ? [node]
                  : [];
  const all = flatten(checks);
  const leaves = onlySatisfied ? all.filter((c) => c.result === true) : all;
  if (!leaves.length)
    return conditionReasonText(checks?.reason) || "未提供逐项依据";
  return leaves
    .slice(0, limit)
    .map((c) => {
      if (c.type === "hot_universe")
        return `近期热点题材 / 行业 · ${c.result === true ? "满足" : c.result === false ? "未满足" : "待核验"}${c.reason ? "；" + c.reason : ""}`;
      if (isHiddenConditionRule(c)) return HIDDEN_CONDITION_TEXT;
      if (c.type === "history_event") return historyCheckText(c);
      if (c.field === HIGHER_LOWS_FIELD) {
        const state =
          c.result === true ? "满足" : c.result === false ? "未满足" : "待核验";
        return `${higherLowsSummary({ ...c, period: c.indicator_period || c.period })} · ${state}${c.reason ? "；" + conditionReasonText(c.reason) : ""}`;
      }
      if (
        c.field === "trendline_pullback" ||
        c.field === "trendline_breakout"
      ) {
        const label =
          c.field === "trendline_pullback" ? "回踩上升支撑" : "收盘突破压力";
        const state =
          c.result === true ? "满足" : c.result === false ? "未满足" : "待核验";
        const line = c.evidence?.line;
        return [
          label,
          state,
          c.observed_date,
          line ? `线价 ${num(line.projected_price, 4)}` : null,
          c.reason,
        ]
          .filter(Boolean)
          .join(" · ");
      }
      if (c.field === "boll_signal") {
        const state =
          c.result === true ? "满足" : c.result === false ? "未满足" : "待核验";
        return `${historyRuleSummary({ ...c, period: c.indicator_period || c.period })} · ${state}${c.observed_date ? " · " + c.observed_date : ""}${c.reason ? "；" + conditionReasonText(c.reason) : ""}`;
      }
      if (c.history_event) return historyRuleSummary(c, fieldLabel);
      if (c.field === "close_breakout_high") {
        const window = Number.isInteger(c.lookback_sessions)
          ? c.lookback_sessions
          : c.value;
        const state =
          c.result === true ? "是" : c.result === false ? "否" : "待核验";
        const covered = Number.isInteger(c.covered_sessions)
          ? `；已覆盖${c.covered_sessions}/${window}日`
          : "";
        return `收盘价突破前${window ?? "N"}个交易日最高价（${state}${covered}${c.result == null && c.reason ? "；" + c.reason : ""}）`;
      }
      if (c.right_event_field) {
        const state =
          c.result === true ? "✓" : c.result === false ? "×" : "待核验";
        if (Object.hasOwn(c, "right_event_change_pct")) {
          const threshold =
            typeof c.threshold_decimal === "string"
              ? c.threshold_decimal
              : num(c.right_actual, 6);
          return `${state} 观察日收盘价（日K） ${num(c.actual)} ${c.op} ${eventPriceTargetText(c, fieldLabel)}（阈值 ${threshold}；实际相对事件价涨跌 ${c.event_change_pct_decimal ?? (c.event_change_pct == null ? "—" : String(c.event_change_pct))}%）${c.event_date ? ` · 事件 ${c.event_date}` : ""}${c.result == null && c.reason ? "；" + conditionReasonText(c.reason) : ""}`;
        }
        return `${state} 观察日收盘价（日K） ${num(c.actual)} ${c.op} 事件日${fieldLabel(c.right_event_field)} ${num(c.right_actual)}${c.event_date ? `（事件 ${c.event_date}）` : ""}${c.result == null && c.reason ? "；" + c.reason : ""}`;
      }
      const value = c.right_event_field
        ? "事件日" + fieldLabel(c.right_event_field)
        : c.right_field
          ? fieldLabel(c.right_field)
          : ruleValue(c.field, c.value);
      const prefix =
        c.result === true
          ? "✓ "
          : c.result === false
            ? "× "
            : c.result === null
              ? "待核验 "
              : "";
      return (
        prefix +
        conditionPeriodSummary({
          ...c,
          period: c.indicator_period || c.period,
        }) +
        fieldLabel(c.field) +
        " " +
        ruleOperator(c.op) +
        " " +
        value +
        (entryHighEvidenceText(c)
          ? "（" + entryHighEvidenceText(c) + "）"
          : entryLowEvidenceText(c)
            ? "（" + entryLowEvidenceText(c) + "）"
            : yearlyLimitCountText(c)
              ? "（" + yearlyLimitCountText(c) + "）"
              : "actual" in c
                ? "（实际 " +
                  (typeof c.actual === "number"
                    ? num(c.actual)
                    : ruleValue(c.field, c.actual)) +
                  "）"
                : "") +
        (c.signal_date || c.observed_date
          ? ` · ${c.signal_date || c.observed_date}`
          : "")
      );
    })
    .join("；");
}
export type Condition = {
  field: string;
  op: string;
  value?: any;
  right_field?: string;
  right_event_field?: string;
  right_event_change_pct?: number | "";
  period?: string;
};
export const fallbackFields: Row[] = [
  ...["open", "high", "low"].map((field) => ({
    field,
    type: "number",
    operators: [">", ">=", "<", "<=", "==", "!="],
  })),
  {
    field: "volume_ratio",
    label: "相对前日量能倍数",
    type: "number",
    operators: [">=", ">", "<=", "<"],
  },
  {
    field: "volume_expand",
    label: "成交量大于前一周期（放量）",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "volume_shrink",
    label: "成交量小于前一周期（缩量）",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "close",
    label: "收盘价",
    type: "number",
    operators: [">", ">=", "<", "<="],
  },
  ...[5, 10, 20, 30, 60, 250].map((n) => ({
    field: "ma" + n,
    label: n + "日均线",
    type: "number",
    operators: [">", ">=", "<", "<="],
    hidden_in_builder: true,
  })),
  {
    field: "change_pct",
    label: "今日涨幅 (%)",
    type: "number",
    operators: [">=", ">", "<=", "<"],
  },
  {
    field: "intraday_high_change_pct",
    label: "当日最高涨幅（相对前收，%）",
    type: "number",
    operators: [">=", ">", "<=", "<", "==", "!="],
    default: 3,
  },
  {
    field: "limit_up",
    label: "今日是否涨停（收盘）",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "prev_limit_up",
    label: "昨日是否涨停（前一市场交易日）",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "limit_up_count_1y",
    label: "截至昨日近一年涨停次数",
    type: "number",
    operators: [">=", ">", "==", "<=", "<", "!="],
  },
  {
    field: "macd_cross",
    label: "MACD 金叉",
    type: "boolean",
    operators: ["=="],
  },
  {
    field: "macd_dead_cross",
    label: "MACD 死叉",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "kd_dead_cross",
    label: "KDJ死叉（K线下穿D线）",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "close_breakout_high",
    label: "收盘价突破前N日最高价",
    type: "number",
    operators: ["new_high"],
    parameter_kind: "lookback_sessions",
    constant_only: true,
    default: 60,
    min: 1,
    max: 750,
  },
  {
    field: HIGHER_LOWS_FIELD,
    label: "底部抬高（确认后未失守）",
    type: "number",
    operators: [HIGHER_LOWS_OPERATOR],
    parameter_kind: "lookback_periods",
    constant_only: true,
    category: "趋势状态",
    default: HIGHER_LOWS_DEFAULT,
    min: HIGHER_LOWS_MIN,
    max: HIGHER_LOWS_MAX,
    periods: ["D", "W", "M"],
    historical_event_supported: false,
  },
  {
    field: "boll_signal",
    label: "BOLL 收口与方向",
    type: "boll_config",
    default: { ...BOLL_DEFAULTS },
    operators: ["matches"],
    constant_only: true,
    category: "BOLL收口与方向",
    periods: ["D", "W", "M"],
    historical_event_supported: false,
  },
  {
    field: "bullish_ma_alignment",
    label: "均线多头排列（5>10>20>30>60）",
    type: "boolean",
    operators: ["==", "!="],
  },
  {
    field: "kd_cross",
    label: "KDJ金叉（K线上穿D线）",
    type: "boolean",
    operators: ["=="],
  },
  {
    field: "topic_ids",
    label: "题材归属",
    type: "topic",
    operators: ["contains"],
  },
];
export function conditionsOf(def: Row | undefined): Condition[] {
  const leaves = Array.isArray(def?.conditions)
    ? def.conditions
    : Array.isArray(def?.all)
      ? def.all
      : Array.isArray(def?.any)
        ? def.any
        : def?.field
          ? [def]
          : [];
  return leaves.map((c: Condition) => {
    const alias = c.field?.match(/^(close_above|close_below)_ma(\d+)$/);
    if (alias && ["==", "!="].includes(c.op) && typeof c.value === "boolean") {
      const positive = c.op === "==" ? c.value : !c.value;
      const op =
        alias[1] === "close_above"
          ? positive
            ? ">"
            : "<="
          : positive
            ? "<"
            : ">=";
      return { field: "close", op, right_field: "ma" + alias[2] };
    }
    return c;
  });
}
export default function Conditions({
  conditions,
  onChange,
  fields = fallbackFields,
  topics = [],
  allowTrackingAlerts = false,
  allowTrackingConditions = false,
  scope = "selection",
  allowEventReferences = false,
  showAddButton = true,
  period = "D",
  dailyContext = false,
  hidePeriodPicker = false,
}: {
  conditions: Condition[];
  onChange: (v: Condition[]) => void;
  fields?: Row[];
  topics?: Row[];
  allowTrackingAlerts?: boolean;
  allowTrackingConditions?: boolean;
  scope?: ConditionScope;
  allowEventReferences?: boolean;
  showAddButton?: boolean;
  period?: string;
  dailyContext?: boolean;
  hidePeriodPicker?: boolean;
}) {
  const [showMore, setShowMore] = useState(false);
  const update = (i: number, p: Partial<Condition>) =>
    onChange(conditions.map((c, n) => (n === i ? { ...c, ...p } : c)));
  const candidateFields = fields.filter(
    (f) =>
      !isHiddenConditionField(f.field) &&
      (scope === "selection" || !f.selection_only) &&
      (!f.hidden_in_builder ||
        ["volume_expand", "volume_shrink"].includes(f.field)) &&
      trackingConditionAllowed(
        f.field,
        allowTrackingAlerts,
        allowTrackingConditions,
      ) &&
      !/^(close_above|close_below)_ma\d+$/.test(f.field) &&
      !/^ma\d+$/.test(f.field) &&
      !["board", "is_st"].includes(f.field),
  );
  const groups = conditionFieldGroups(
    candidateFields,
    scope,
    conditions.map((condition) => condition.field),
    showMore || !showAddButton,
  );
  const visibleFields = groups.flatMap((group) => group.fields);
  const grouped = new Map(groups.map((group) => [group.label, group.fields]));
  return (
    <div className="conditions">
      {conditions.map((c, i) => {
        if (isHiddenConditionRule(c))
          return (
            <div className="condition-row" key={i}>
              <span className="condition-index">{i + 1}</span>
              <span className="muted">{HIDDEN_CONDITION_TEXT}</span>
              <button
                className="icon-btn danger-text"
                aria-label="删除已隐藏的指标条件"
                onClick={() => onChange(conditions.filter((_, n) => n !== i))}
              >
                <Trash2 size={15} />
              </button>
            </div>
          );
        const isYearlyLimitCount = c.field === "limit_up_count_1y";
        const isNewHigh = c.field === "close_breakout_high";
        const isBoll = c.field === "boll_signal";
        const isHigherLows = c.field === HIGHER_LOWS_FIELD;
        const field = fields.find((f) => f.field === c.field) || {
          type: "number",
          operators: [">=", ">", "<=", "<", "==", "!="],
        };
        const ops = field.operators?.length
          ? field.operators
          : [">=", ">", "<=", "<", "==", "!="];
        const periodView = conditionPeriodView(c, field, period, dailyContext);
        return (
          <div
            className={`condition-row${isBoll ? " boll-condition-row" : ""}`}
            key={i}
          >
            <span className="condition-index">{i + 1}</span>
            {!periodView.identity && !hidePeriodPicker && (
              <select
                className="condition-period"
                aria-label={`条件${i + 1}判断周期`}
                disabled={!!periodView.fixed}
                value={periodView.fixed || c.period || "inherit"}
                onChange={(event) =>
                  onChange(
                    conditions.map((item, index) =>
                      index === i
                        ? (withConditionPeriod(
                            item,
                            event.target.value,
                          ) as Condition)
                        : item,
                    ),
                  )
                }
              >
                {!periodView.fixed && (
                  <option value="inherit">
                    沿用默认 · {periodLabel(period)}
                  </option>
                )}
                {(periodView.fixed
                  ? [periodView.fixed]
                  : field.periods || ["D", "W", "M"]
                ).map((key: string) => (
                  <option key={key} value={key}>
                    {periodLabel(key)}
                    {periodView.fixed ? " · 固定" : ""}
                  </option>
                ))}
              </select>
            )}
            <select
              aria-label={"条件" + (i + 1) + "字段"}
              value={c.field}
              disabled={!!c.right_event_field}
              onChange={(e) => {
                const f = fields.find((x) => x.field === e.target.value);
                const next: Condition = {
                  field: e.target.value,
                  op: f?.operators?.[0] || ">=",
                  value:
                    e.target.value === "boll_signal"
                      ? { ...BOLL_DEFAULTS }
                      : ["close_breakout_high", HIGHER_LOWS_FIELD].includes(
                            e.target.value,
                          )
                        ? 60
                        : e.target.value === "intraday_high_change_pct"
                          ? 3
                          : f?.type === "boolean"
                            ? true
                            : f?.type === "topic"
                              ? ""
                              : (f?.default ?? 0),
                };
                if (
                  c.period &&
                  !conditionPeriodView(next, f || {}, period, dailyContext)
                    .fixed
                )
                  next.period = c.period;
                onChange(conditions.map((v, n) => (n === i ? next : v)));
              }}
            >
              {!visibleFields.some((x) => x.field === c.field) && (
                <option value={c.field}>{fieldLabel(c.field)}</option>
              )}
              {Array.from(grouped, ([group, choices]) => (
                <optgroup label={group} key={group}>
                  {choices.map((f) => (
                    <option key={f.field} value={f.field}>
                      {c.right_event_field && f.field === "close"
                        ? "观察日收盘价（日K）"
                        : metricCycleLabel(
                            f.field,
                            fieldLabel(f.field, f.label),
                            conditionPeriodView(
                              {
                                ...c,
                                field: f.field,
                                right_field: undefined,
                                right_event_field: undefined,
                              },
                              f,
                              period,
                              dailyContext,
                            ).effective,
                          )}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            {!isNewHigh &&
              !isHigherLows &&
              !isBoll &&
              !field.selection_only && (
                <select
                  aria-label="比较关系"
                  className="operator"
                  value={c.op}
                  onChange={(e) => update(i, { op: e.target.value })}
                >
                  {!ops.includes(c.op) && <option>{c.op}</option>}
                  {ops.map((op: string) => (
                    <option key={op} value={op}>
                      {(
                        {
                          "==": "等于",
                          "!=": "不等于",
                          contains: "包含",
                          not_contains: "不包含",
                        } as Row
                      )[op] || op}
                    </option>
                  ))}
                </select>
              )}
            {isBoll ? (
              <BollConditionEditor
                value={c.value}
                onChange={(value) => update(i, { value })}
                label={`条件${i + 1}`}
                period={periodView.effective}
              />
            ) : isHigherLows ? (
              <>
                <input
                  type="number"
                  aria-label={`条件${i + 1}底部抬高回看周期数`}
                  min={HIGHER_LOWS_MIN}
                  max={HIGHER_LOWS_MAX}
                  step={1}
                  placeholder="默认60"
                  value={c.value ?? ""}
                  onChange={(event) =>
                    update(i, {
                      value:
                        event.target.value === ""
                          ? ""
                          : Number(event.target.value),
                    })
                  }
                />
                <span
                  className="muted"
                  style={{ whiteSpace: "nowrap", fontSize: 11 }}
                >
                  {higherLowsWindowUnit(periodView.effective)}
                </span>
              </>
            ) : isNewHigh ? (
              <>
                <input
                  type="number"
                  aria-label={`条件${i + 1}新高回看交易日数`}
                  min={1}
                  max={750}
                  step={1}
                  placeholder="例如60"
                  value={c.value ?? ""}
                  onChange={(event) =>
                    update(i, {
                      value:
                        event.target.value === ""
                          ? ""
                          : Number(event.target.value),
                    })
                  }
                />
                <span
                  className="muted"
                  style={{ whiteSpace: "nowrap", fontSize: 11 }}
                >
                  个交易日
                </span>
              </>
            ) : field.type === "boolean" ? (
              <select
                aria-label="条件值"
                value={String(c.value)}
                onChange={(e) =>
                  update(i, { value: e.target.value === "true" })
                }
              >
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            ) : field.type === "topic" ? (
              <TopicSelect
                topics={topics}
                label={`条件${i + 1}题材`}
                value={c.value || ""}
                onChange={(value) => update(i, { value })}
              />
            ) : (
              <>
                {!isYearlyLimitCount && !field.constant_only && (
                  <select
                    className="rhs-type"
                    aria-label="比较对象"
                    value={conditionComparisonObject(c)}
                    onChange={(e) => {
                      const next = withConditionComparison(
                        c,
                        e.target.value as ComparisonObject,
                      ) as Condition;
                      onChange(conditions.map((v, n) => (n === i ? next : v)));
                    }}
                  >
                    <option value="value">数值</option>
                    <option value="field">字段</option>
                    {allowEventReferences && c.field === "close" && (
                      <>
                        <option value="event">事件日价格</option>
                        <option value="event-percentage">事件价偏移 (%)</option>
                      </>
                    )}
                  </select>
                )}
                {c.right_event_field && !isYearlyLimitCount ? (
                  <select
                    aria-label="历史事件价格字段"
                    value={c.right_event_field}
                    onChange={(e) =>
                      update(i, { right_event_field: e.target.value })
                    }
                  >
                    {["close", "high", "low"].map((name) => (
                      <option value={name} key={name}>
                        事件日{fieldLabel(name)}
                      </option>
                    ))}
                  </select>
                ) : c.right_field && !isYearlyLimitCount ? (
                  <select
                    aria-label="右值字段"
                    value={c.right_field}
                    onChange={(e) => update(i, { right_field: e.target.value })}
                  >
                    {fields
                      .filter(
                        (f) =>
                          !isHiddenConditionField(f.field) &&
                          f.type === "number" &&
                          f.field !== "limit_up_count_1y" &&
                          f.field !== "close_breakout_high" &&
                          (!conditionPeriodView(
                            { field: f.field },
                            f,
                            periodView.effective,
                          ).fixed ||
                            periodView.effective === "D") &&
                          !f.constant_only,
                      )
                      .map((f) => (
                        <option key={f.field} value={f.field}>
                          {metricCycleLabel(
                            f.field,
                            fieldLabel(f.field, f.label),
                            periodView.effective,
                          )}
                        </option>
                      ))}
                  </select>
                ) : (
                  <input
                    aria-label={
                      isYearlyLimitCount
                        ? `条件${i + 1}近一年涨停次数`
                        : "条件数值"
                    }
                    type="number"
                    step={field.step ?? "any"}
                    min={field.min}
                    max={field.max}
                    placeholder={
                      isYearlyLimitCount ? "输入次数，例如 3" : undefined
                    }
                    value={c.value ?? ""}
                    onChange={(e) =>
                      update(i, {
                        value:
                          e.target.value === "" ? "" : Number(e.target.value),
                      })
                    }
                  />
                )}
              </>
            )}
            {c.right_event_field &&
              Object.hasOwn(c, "right_event_change_pct") && (
                <label className="event-price-offset-input">
                  事件价上/下移 (%)
                  <input
                    type="number"
                    min={-100}
                    max={10000}
                    step="any"
                    aria-label="事件价偏移百分比"
                    value={c.right_event_change_pct ?? ""}
                    onChange={(e) =>
                      update(i, {
                        right_event_change_pct:
                          e.target.value === "" ? "" : Number(e.target.value),
                      })
                    }
                  />
                </label>
              )}
            <button
              className="icon-btn danger-text"
              aria-label="删除条件"
              onClick={() => onChange(conditions.filter((_, n) => n !== i))}
            >
              <Trash2 size={15} />
            </button>
          </div>
        );
      })}
      {conditions.some(
        (condition) =>
          condition.field === "volume" && condition.right_field === "volume",
      ) && (
        <small className="danger-text" role="alert">
          两边的“成交量”都是同一周期的数据，不是昨天的成交量。若只要求比前一期多，请选择“成交量大于前一周期（放量）”并设为“是”；需要指定倍数时，选择“成交量倍数”与数值比较。
        </small>
      )}
      {conditions.some((condition) =>
        ["volume_expand", "volume_shrink"].includes(condition.field),
      ) && (
        <small className="muted">
          放量“是”要求当期成交量严格增加；缩量“是”要求严格减少；无需填写倍数。日K比较前一个市场交易日，周/月K比较前一个完整周/月。相等时两者均为“否”，“不放量”不能替代“缩量”。缺报价、零量停牌或覆盖不足时待核验。
        </small>
      )}
      {conditions.some((condition) => condition.field === "volume_ratio") && (
        <small className="muted">
          成交量倍数 = 当期成交量 ÷
          前一可比周期成交量；日K比较前一市场交易日，周/月K比较前一完整周/月，不是均量。&gt;1才是放量，≥1.5表示至少1.5倍，≥1包含平量。
        </small>
      )}
      {conditions.some((condition) =>
        Object.hasOwn(condition, "right_event_change_pct"),
      ) && (
        <small className="event-price-offset-note">
          比较阈值 = 事件参照价 × (1 + 偏移% /
          100)。+3代表事件价上方3%，−3代表下方3%，不是今日涨幅；可用两条AND条件限定±3%。0保留原价，边界是否包含由比较关系决定。
        </small>
      )}
      {conditions.some(
        (condition) =>
          Object.hasOwn(condition, "right_event_change_pct") &&
          eventPriceOffsetError(condition.right_event_change_pct),
      ) && (
        <small className="danger-text" role="alert">
          事件价偏移请输入大于−100且不超过10000的有限百分比；清空时不能保存。
        </small>
      )}
      {conditions.some((condition) =>
        ["limit_up", "prev_limit_up"].includes(condition.field),
      ) && (
        <small className="muted">
          今日、昨日均按市场日线收盘判断；昨日为前一个市场交易日。缺报价、停牌或来源证据不足时待核验，不判为“否”。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === "bullish_ma_alignment",
      ) && (
        <small className="muted">
          按条件日、周、月判断周期比较 MA5 &gt; MA10 &gt; MA20 &gt; MA30 &gt;
          MA60；不附加收盘价或 MA250 条件，预热不足时待核验。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === "limit_up_count_1y",
      ) && (
        <small className="muted">
          按观察日前一自然年同日至昨日统计，不含今日；输入非负整数次数。覆盖不完整时保留已确认下界，只有足够证明条件成立或不成立时才作判断，其余待核验，不将年内次数冒充近一年次数。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === "close_above_entry_high",
      ) && (
        <small className="muted">
          当前日收盘价严格高于本轮入池事件日的日K最高价时提醒；相等不触发，是状态判断而非今日上穿，也不比较跟踪期间累计最高价。提醒后继续跟踪，缺事件日高价、报价或公司行动口径无法对齐时待核验。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === "close_below_entry_low",
      ) && (
        <small className="muted">
          当前日收盘价严格低于本轮入池事件日的日K最低价才满足；相等不满足，不比较事件日收盘或跟踪期间最低价。用于结束条件时归入结束历史；事件日低价、当前报价或公司行动口径无法核验时保持待核验。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === "close_breakout_high",
      ) && (
        <small className="muted">
          收盘价严格突破前N个市场交易日日K最高价，不含当日；输入1—750的正整数，默认60。无论策略采用日、周还是月线，本条件均按业务观察日的日线窗口判断；缺报价或未复权公司行动时待核验，其他均线等条件仍沿策略自身周期。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === "intraday_high_change_pct",
      ) && (
        <small className="muted">
          使用已完成日K最高价相对前一个市场交易日收盘价的涨幅，不用收盘涨幅替代；周/月策略仍按观察日日线判断。本条件不依赖分钟接口，也不代表盘中实时触发或成交；缺前日报价、零量停牌或未核验公司行动时待核验。
        </small>
      )}
      {conditions.some(
        (condition) => condition.field === HIGHER_LOWS_FIELD,
      ) && (
        <small className="muted">
          取回看窗口内最近两个已确认低点，后底严格高于前底，且后底确认日（含）之后没有收盘跌破后底；左右各2根确认，相等低点不算。另需窗口前2根预热，缺报价或价格不可比时待核验。周/月只用完整K线，可与倍量或金叉独立组合，不自动限定两底发生在历史事件之后。
        </small>
      )}
      {conditions.some(
        (condition) =>
          condition.field === HIGHER_LOWS_FIELD &&
          higherLowsWindowError(condition.value),
      ) && (
        <small className="danger-text" role="alert">
          底部抬高回看窗口请输入8—750的整数；清空时不能保存。
        </small>
      )}
      {scope !== "selection" && showAddButton && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={showMore}
            onChange={(event) => setShowMore(event.target.checked)}
          />
          显示更多跨场景条件<span className="muted">已保存的条件始终保留</span>
        </label>
      )}
      {showAddButton && (
        <button
          className="btn small dashed"
          onClick={() =>
            onChange([
              ...conditions,
              {
                field: visibleFields[0]?.field || "volume_ratio",
                op: visibleFields[0]?.operators?.[0] || ">=",
                value:
                  visibleFields[0]?.field === "boll_signal"
                    ? { ...BOLL_DEFAULTS }
                    : ["close_breakout_high", HIGHER_LOWS_FIELD].includes(
                          visibleFields[0]?.field,
                        )
                      ? 60
                      : visibleFields[0]?.type === "boolean"
                        ? true
                        : 3,
              },
            ])
          }
        >
          <Plus size={13} />
          添加条件
        </button>
      )}
    </div>
  );
}
