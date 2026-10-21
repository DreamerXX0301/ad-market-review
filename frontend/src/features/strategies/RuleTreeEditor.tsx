import { useState } from "react";
import {
  Clock3,
  GitBranch,
  Plus,
  Trash2,
  ArrowDown,
  CalendarRange,
} from "lucide-react";
import type { Row } from "../../lib/api";
import Conditions, { fallbackFields, fieldLabel } from "./Conditions";
import {
  historyFieldsFallback,
  historyRuleSummary,
  newHistoryRule,
  normalizeRuleTree,
} from "./historicalRules";
import type { RuleNode } from "./historicalRules";
import type { ConditionScope } from "./conditionScope";
import "./RuleTreeEditor.css";

type EditorProps = {
  value: RuleNode;
  onChange: (value: RuleNode) => void;
  fields: Row[];
  topics?: Row[];
  period: string;
  scope?: ConditionScope;
  allowTrackingAlerts?: boolean;
  allowTrackingConditions?: boolean;
};
type NodeProps = EditorProps & {
  context: "current" | "where" | "then";
  depth: number;
  onRemove?: () => void;
};

function conditionFields(fields: Row[], context: NodeProps["context"]) {
  if (context !== "where") return fields;
  const hasMetadata = fields.some(
    (field) => "historical_event_supported" in field,
  );
  return fields.filter((field) =>
    hasMetadata
      ? field.historical_event_supported === true
      : historyFieldsFallback.has(field.field),
  );
}
function defaultLeaf(fields: Row[]): RuleNode {
  const field =
    fields.find((item) => item.field === "volume_ratio") ||
    fields.find((item) => !item.hidden_in_builder) ||
    fallbackFields[0];
  return {
    field: field.field,
    op: field.operators?.[0] || ">=",
    value: field.type === "boolean" ? true : (field.default ?? 3),
  };
}
function HistoryEventNode({
  value,
  onChange,
  fields,
  topics,
  period,
  scope,
  allowTrackingAlerts,
  allowTrackingConditions,
  depth,
  onRemove,
}: NodeProps) {
  const event = value.history_event;
  const [windowMode, setWindowMode] = useState(
    event.window?.from === event.window?.to ? "exact" : "range",
  );
  const exact =
    windowMode === "exact" && event.window?.from === event.window?.to;
  const update = (patch: RuleNode) =>
    onChange({ ...value, history_event: { ...event, ...patch } });
  return (
    <section className="history-event-editor">
      <div className="history-event-heading">
        <span className="history-event-symbol">
          <Clock3 size={17} />
        </span>
        <div>
          <strong>历史事件 ＋ 当前条件</strong>
          <small>先锁定事件日，再使用该事件的价格核对当前条件</small>
        </div>
        {onRemove && (
          <button
            className="icon-btn danger-text"
            aria-label="删除历史事件条件"
            onClick={onRemove}
          >
            <Trash2 size={15} />
          </button>
        )}
      </div>
      <div className="history-window-controls">
        <label>
          事件日期
          <select
            aria-label="历史事件日期方式"
            value={exact ? "exact" : "range"}
            onChange={(e) => {
              setWindowMode(e.target.value);
              update({
                window:
                  e.target.value === "exact"
                    ? {
                        from: event.window?.from || 5,
                        to: event.window?.from || 5,
                      }
                    : { from: 1, to: Math.max(2, event.window?.to || 30) },
              });
            }}
          >
            <option value="exact">恰好N个交易日前</option>
            <option value="range">过去交易日区间</option>
          </select>
        </label>
        {exact ? (
          <label>
            距离当前
            <input
              type="number"
              min={1}
              max={750}
              step={1}
              aria-label="恰好几个交易日前"
              value={event.window?.from ?? ""}
              onChange={(e) => {
                const n = e.target.value === "" ? "" : Number(e.target.value);
                update({ window: { from: n, to: n } });
              }}
            />
          </label>
        ) : (
          <>
            <label>
              最近距离
              <input
                type="number"
                min={1}
                max={750}
                step={1}
                aria-label="历史区间最近交易日距离"
                value={event.window?.from ?? ""}
                onChange={(e) =>
                  update({
                    window: {
                      ...event.window,
                      from: e.target.value === "" ? "" : Number(e.target.value),
                    },
                  })
                }
              />
            </label>
            <label>
              最远距离
              <input
                type="number"
                min={1}
                max={750}
                step={1}
                aria-label="历史区间最远交易日距离"
                value={event.window?.to ?? ""}
                onChange={(e) =>
                  update({
                    window: {
                      ...event.window,
                      to: e.target.value === "" ? "" : Number(e.target.value),
                    },
                  })
                }
              />
            </label>
          </>
        )}
        <label>
          多个事件匹配时
          <select
            aria-label="历史事件匹配顺序"
            value={event.match || "latest"}
            onChange={(e) => update({ match: e.target.value })}
          >
            <option value="latest">选择最近一次</option>
            <option value="earliest">选择最早一次</option>
          </select>
        </label>
        <span className="history-session-unit">市场交易日 · 不含今日</span>
      </div>
      <div className="event-stage event-stage-history">
        <header>
          <span>1</span>
          <div>
            <strong>历史事件条件</strong>
            <small>事件日按日K独立计算，支持组合“全部 / 任一”</small>
          </div>
        </header>
        <RuleNodeEditor
          value={event.where || { all: [] }}
          onChange={(where) => update({ where })}
          fields={fields}
          topics={topics}
          period="D"
          context="where"
          depth={depth + 1}
        />
      </div>
      <div className="event-stage-connector">
        <ArrowDown size={16} />
        <span>绑定同一个历史事件日的收盘 / 最高 / 最低价</span>
      </div>
      <div className="event-stage event-stage-current">
        <header>
          <span>2</span>
          <div>
            <strong>当前条件</strong>
            <small>
              新增历史事件默认日K；旧条件保持已保存周期。事件价格比较固定使用观察日日K收盘。
            </small>
          </div>
        </header>
        <RuleNodeEditor
          value={event.then || { all: [] }}
          onChange={(then) => update({ then })}
          fields={fields}
          topics={topics}
          period={period}
          scope={scope}
          allowTrackingAlerts={allowTrackingAlerts}
          allowTrackingConditions={allowTrackingConditions}
          context="then"
          depth={depth + 1}
        />
      </div>
      <details className="history-rule-method">
        <summary>事件匹配口径与未知处理</summary>
        <p>
          距离按市场交易日日历计算，范围1—750日。先按顺序判断历史事件；更优先的日期若待核验，不会跳过它去选择更早或更晚的已知事件。事件内只使用已留存日行情及技术指标；历史题材、身份、跟踪基准与涨停来源类条件暂不开放。比较跨越未复权公司行动、报价或覆盖不足时保持待核验，不以当前价格回填历史。
        </p>
      </details>
    </section>
  );
}
function RuleNodeEditor(props: NodeProps) {
  const { value, onChange, fields, topics, period, context, depth, onRemove } =
    props;
  if (value.history_event) return <HistoryEventNode {...props} />;
  const group = value.all || value.any;
  const candidates = conditionFields(fields, context);
  if (!Array.isArray(group)) {
    if (value.field)
      return (
        <Conditions
          conditions={[value as any]}
          onChange={(next) => (next.length ? onChange(next[0]) : onRemove?.())}
          fields={candidates}
          topics={topics}
          scope={context === "where" ? "selection" : props.scope}
          allowTrackingAlerts={context !== "where" && props.allowTrackingAlerts}
          allowTrackingConditions={
            context !== "where" && props.allowTrackingConditions
          }
          allowEventReferences={context === "then"}
          period={context === "where" ? "D" : period}
          dailyContext={context === "where"}
          hidePeriodPicker={context !== "current"}
          showAddButton={false}
        />
      );
    return (
      <div className="unsupported-rule">
        <p>此规则保留了原结构，请切换“结构化规则”核对后编辑。</p>
        <code>{JSON.stringify(value)}</code>
      </div>
    );
  }
  const logic = value.any ? "any" : "all";
  const replace = (index: number, next: RuleNode) =>
    onChange({
      [logic]: group.map((item: RuleNode, at: number) =>
        at === index ? next : item,
      ),
    });
  return (
    <div className={"visual-rule-group " + (depth > 0 ? "nested" : "")}>
      <div className="visual-group-heading">
        <GitBranch size={14} />
        <select
          aria-label={
            depth === 0 && props.scope === "alert"
              ? "提醒条件逻辑"
              : depth === 0 && props.scope === "exit"
                ? "提前结束条件逻辑"
                : "条件组逻辑"
          }
          value={logic}
          onChange={(e) => onChange({ [e.target.value]: group })}
        >
          <option value="all">以下全部满足</option>
          <option value="any">以下任一满足</option>
        </select>
        <small>{group.length} 项</small>
        {onRemove && (
          <button
            className="icon-btn danger-text"
            aria-label="删除条件组"
            onClick={onRemove}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>
      <div className="visual-group-children">
        {group.map((child: RuleNode, index: number) => (
          <div className="visual-rule-child" key={index}>
            <RuleNodeEditor
              {...props}
              value={child}
              onChange={(next) => replace(index, next)}
              context={context}
              depth={depth + 1}
              onRemove={() =>
                onChange({
                  [logic]: group.filter(
                    (_: RuleNode, at: number) => at !== index,
                  ),
                })
              }
            />
          </div>
        ))}
      </div>
      <div className="visual-rule-actions">
        <button
          className="btn small dashed"
          onClick={() =>
            onChange({
              [logic]: [
                ...group,
                {
                  ...defaultLeaf(candidates),
                  ...(context !== "current" ? { period: "D" } : {}),
                },
              ],
            })
          }
        >
          <Plus size={13} />
          添加条件
        </button>
        {depth < 5 && (
          <button
            className="btn small"
            onClick={() =>
              onChange({
                [logic]: [
                  ...group,
                  {
                    all: [
                      {
                        ...defaultLeaf(candidates),
                        ...(context !== "current" ? { period: "D" } : {}),
                      },
                    ],
                  },
                ],
              })
            }
          >
            <GitBranch size={13} />
            添加条件组
          </button>
        )}
        {context === "current" && depth < 4 && (
          <button
            className="btn small history-add"
            onClick={() => onChange({ [logic]: [...group, newHistoryRule()] })}
          >
            <Clock3 size={13} />
            历史事件＋当前条件
          </button>
        )}
      </div>
    </div>
  );
}
export default function RuleTreeEditor({
  value,
  onChange,
  fields,
  topics,
  period,
  scope,
  allowTrackingAlerts,
  allowTrackingConditions,
}: EditorProps) {
  const normalized = normalizeRuleTree(value);
  const group =
    normalized.all || normalized.any ? normalized : { all: [normalized] };
  return (
    <div className="rule-tree-editor">
      <RuleNodeEditor
        value={group}
        onChange={onChange}
        fields={fields}
        topics={topics}
        period={period}
        scope={scope}
        allowTrackingAlerts={allowTrackingAlerts}
        allowTrackingConditions={allowTrackingConditions}
        context="current"
        depth={0}
      />
      <div className="visual-rule-preview">
        <CalendarRange size={16} />
        <div>
          <strong>规则预览</strong>
          <p>
            {historyRuleSummary(group, fieldLabel) ||
              "添加条件后将在此生成摘要"}
          </p>
        </div>
      </div>
    </div>
  );
}
