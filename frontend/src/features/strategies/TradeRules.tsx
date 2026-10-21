import { ArrowDownRight, ArrowUpRight, FlaskConical } from "lucide-react";
import type { Row } from "../../lib/api";
import { fieldLabel, ruleOperator, ruleValue } from "./Conditions";
import RuleTreeEditor from "./RuleTreeEditor";
import { historyRuleSummary } from "./historicalRules";
import "./TradeRules.css";

export type TradeRulesValue = { entry: Row | null; exit: Row | null };
const unavailable = new Set([
  "topic_ids",
  "limit_up",
  "prev_limit_up",
  "limit_up_count_1y",
  "limit_up_1y",
  "turnover_ratio_pct",
  "trading_age",
  "tracking_change_pct",
  "close_below_entry",
  "close_above_entry_high",
  "close_below_entry_low",
]);
export const emptyTradeRules = (): TradeRulesValue => ({
  entry: null,
  exit: null,
});

export function tradeRuleText(node: Row | null | undefined): string {
  if (!node) return "未设置";
  if (node.history_event) return historyRuleSummary(node, fieldLabel);
  const children = node.all || node.any || node.conditions;
  if (Array.isArray(children)) {
    const join = node.any || node.logic === "OR" ? " 或 " : " 且 ";
    return children.map((item: Row) => `(${tradeRuleText(item)})`).join(join);
  }
  return `${fieldLabel(node.field)} ${ruleOperator(node.op)} ${node.right_field ? fieldLabel(node.right_field) : ruleValue(node.field, node.value)}`;
}

export function TradeRuleSummary({
  value,
  compact = false,
  config,
}: {
  value?: TradeRulesValue;
  compact?: boolean;
  config?: Row;
}) {
  const count = Number(!!value?.entry) + Number(!!value?.exit);
  const risks = config
    ? [
        config.stop_loss_pct > 0 ? `止损 ${config.stop_loss_pct}%` : null,
        config.take_profit_pct > 0 ? `止盈 ${config.take_profit_pct}%` : null,
        config.exit_ma > 0 ? `跌破 MA${config.exit_ma}` : null,
        config.hold_days > 0 ? `持有 ${config.hold_days} 日` : null,
      ].filter(Boolean)
    : [];
  const content = (
    <div className="trade-overview-content">
      <div className="trade-overview-grid">
        <div className="trade-overview-side entry">
          <header>
            <ArrowUpRight size={17} />
            <strong>买入信号</strong>
            <span>{value?.entry ? "自定义" : "跟随选股"}</span>
          </header>
          <p>
            {value?.entry
              ? tradeRuleText(value.entry)
              : "选股条件满足时生成信号"}
          </p>
          <small>
            {value?.entry ? "同一期选股条件也须满足" : "未启用额外买入条件"}
          </small>
        </div>
        <div className="trade-overview-side exit">
          <header>
            <ArrowDownRight size={17} />
            <strong>离场规则</strong>
            <span>{value?.exit ? "自定义 + 风控" : "回测风控"}</span>
          </header>
          <p>
            {value?.exit
              ? tradeRuleText(value.exit)
              : "由本次回测的风控参数决定"}
          </p>
          <small>
            {value?.exit
              ? "或任一已启用风控规则触发"
              : "可在策略编辑中添加自定义卖出条件"}
          </small>
        </div>
      </div>
      <footer>
        <span>收盘确认信号</span>
        <b>→</b>
        <span>下一交易日开盘尝试撮合</span>
        <span className="trade-overview-risk">
          {risks.length ? risks.join(" · ") : "风控参数在回测中设置"}
        </span>
      </footer>
    </div>
  );
  const heading = (
    <>
      <span className="trade-overview-heading">
        <FlaskConical size={15} />
        <strong>回测交易规则</strong>
      </span>
      <span className="trade-overview-badge">
        {count ? `${count} 项自定义信号` : "默认信号"}
      </span>
    </>
  );
  return compact ? (
    <details className="trade-overview compact">
      <summary>
        {heading}
        <span className="trade-overview-expand">展开规则</span>
      </summary>
      {content}
    </details>
  ) : (
    <section className="trade-overview">
      <div className="trade-overview-head">{heading}</div>
      {content}
    </section>
  );
}

export default function TradeRulesEditor({
  value,
  onChange,
  fields,
  period,
}: {
  value: TradeRulesValue;
  onChange: (value: TradeRulesValue) => void;
  fields: Row[];
  period: string;
}) {
  const allowed = fields.filter(
    (field) => !unavailable.has(field.field) && !field.tracking_only,
  );
  return (
    <section className="trade-rules-editor">
      <div className="trade-rules-heading">
        <div>
          <span className="eyebrow">交易规则</span>
          <h3>买入与卖出条件</h3>
        </div>
        <span className="badge neutral">用于研究回测</span>
      </div>
      <p className="trade-rules-intro">
        选股决定观察哪些股票，买卖条件决定何时产生交易信号。修改规则会保存为新的策略版本，策略跟踪池的提醒和结束规则单独设置。
      </p>
      {(["entry", "exit"] as const).map((side) => {
        const tree = value[side];
        const update = (next: Row | null) =>
          onChange({ ...value, [side]: next });
        return (
          <div className={`trade-rule-card ${side}`} key={side}>
            <div className="trade-rule-card-heading">
              <div className="trade-rule-side">
                {side === "entry" ? (
                  <ArrowUpRight size={18} />
                ) : (
                  <ArrowDownRight size={18} />
                )}
                <strong>{side === "entry" ? "买入条件" : "卖出条件"}</strong>
              </div>
              <label className="trade-rule-toggle">
                <input
                  type="checkbox"
                  checked={!!tree}
                  onChange={(event) =>
                    update(
                      event.target.checked
                        ? {
                            all: [
                              side === "entry"
                                ? { field: "macd_cross", op: "==", value: true }
                                : {
                                    field: "close",
                                    op: "<",
                                    right_field: "ma20",
                                  },
                            ],
                          }
                        : null,
                    )
                  }
                />
                {tree ? "已启用" : "未启用"}
              </label>
            </div>
            {!tree ? (
              <p className="trade-rule-placeholder">
                {side === "entry"
                  ? "默认：选股命中即可产生买入信号。启用后，还须同时满足这里的条件。"
                  : "默认：使用回测中的止盈、止损和持有期限。启用后，自定义卖出条件也可独立触发离场。"}
              </p>
            ) : (
              <RuleTreeEditor
                value={tree}
                onChange={update}
                fields={allowed}
                period={period}
                scope={side === "entry" ? "trade-entry" : "trade-exit"}
              />
            )}
          </div>
        );
      })}
      <div className="trade-rule-execution">
        <strong>
          {period === "M"
            ? "完整月收盘判断条件"
            : period === "W"
              ? "完整周收盘判断条件"
              : "每日收盘判断条件"}{" "}
          → 下一市场交易日开盘尝试成交
        </strong>
        <span>
          止盈、止损和持有期限仍按每日收盘检查；卖出条件与这些规则任一触发即可离场。缺数不触发，一字涨跌停等不能成交时保留拒单记录；这里不会提交实盘委托。
        </span>
      </div>
    </section>
  );
}
