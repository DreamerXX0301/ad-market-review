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
import HistoryPricePathEditor from "./HistoryPricePathEditor";
import {
  historyFieldsFallback,
  historyRuleSummary,
  hasHiddenConditionRule,
  HIDDEN_CONDITION_TEXT,
  isHiddenConditionField,
  newHistoryRule,
  normalizeRuleTree,
  historyWindowMode,
  historyWindowForMode,
  historyWindowText,
  historyEventPreset,
  historyPresetWhere,
  appendTodayCondition,
} from "./historicalRules";
import type { RuleNode, HistoryWindowMode } from "./historicalRules";
import type { ConditionScope } from "./conditionScope";
import { periodLabel } from "./conditionPeriod";
import {
  availableTechnicalPresets,
  createTechnicalPreset,
  appendTechnicalPreset,
  technicalPresetInsertionError,
} from "./technicalConditionPresets";
import type { TechnicalPresetId } from "./technicalConditionPresets";
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
  rootValue: RuleNode;
  context: "current" | "where" | "then";
  depth: number;
  onRemove?: () => void;
};

function TechnicalPresetControls({
  value,
  onChange,
  rootValue,
  fields,
  period,
  depth,
}: {
  value: RuleNode;
  onChange: (next: RuleNode) => void;
  rootValue: RuleNode;
  fields: Row[];
  period: string;
  depth: number;
}) {
  const [selected, setSelected] =
    useState<TechnicalPresetId>("macd-above-zero");
  const [upper, setUpper] = useState<number | "">(20);
  const available = availableTechnicalPresets(fields, period);
  const preset = available.find((item) => item.id === selected) || available[0];
  if (!preset) return null;
  const invalidUpper =
    preset.id === "kd-low-cross" &&
    (upper === "" || !Number.isFinite(upper) || upper < 0 || upper > 100);
  const rule = invalidUpper
    ? null
    : createTechnicalPreset(preset.id, period, upper === "" ? 20 : upper);
  const error = invalidUpper
    ? "KDJ低位上限请输入0—100的有限数值"
    : technicalPresetInsertionError(rootValue, value, rule!, depth);
  return (
    <div className="technical-preset-controls">
      <div className="technical-preset-fields">
        <label>
          技术组合
          <select
            aria-label="技术组合"
            value={preset.id}
            onChange={(event) =>
              setSelected(event.target.value as TechnicalPresetId)
            }
          >
            {available.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {preset.id === "kd-low-cross" && (
          <label className="technical-preset-upper">
            K、D低位上限
            <input
              type="number"
              min={0}
              max={100}
              step="any"
              aria-label="KDJ低位上限"
              value={upper}
              onChange={(event) =>
                setUpper(
                  event.target.value === "" ? "" : Number(event.target.value),
                )
              }
            />
          </label>
        )}
        <span className="technical-preset-period">{periodLabel(period)}</span>
        <button
          type="button"
          className="btn small"
          disabled={!!error}
          onClick={() => {
            if (rule && !error) onChange(appendTechnicalPreset(value, rule));
          }}
        >
          <Plus size={13} /> 添加技术组合
        </button>
      </div>
      <small className={error ? "technical-preset-error" : ""}>
        {error || `${preset.description} 添加后可逐项修改，不改变已有条件。`}
      </small>
    </div>
  );
}

function conditionFields(fields: Row[], context: NodeProps["context"]) {
  fields = fields.filter((field) => !isHiddenConditionField(field.field));
  if (context !== "where") return fields;
  const hasMetadata = fields.some(
    (field) => "historical_event_supported" in field,
  );
  return fields
    .filter((field) =>
      hasMetadata
        ? field.historical_event_supported === true
        : historyFieldsFallback.has(field.field),
    )
    .map((field) => ({
      ...field,
      label:
        field.field === "limit_up"
          ? "事件日是否涨停（收盘）"
          : field.field === "change_pct"
            ? "事件日涨幅 (%)"
            : field.label,
    }));
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
  rootValue,
}: NodeProps) {
  const event = value.history_event;
  const [selectedWindowMode, setWindowMode] = useState<HistoryWindowMode>(
    historyWindowMode(event.window),
  );
  const windowMode =
    selectedWindowMode === "recent" && event.window?.from === 1
      ? "recent"
      : selectedWindowMode === "exact" &&
          event.window?.from === event.window?.to
        ? "exact"
        : selectedWindowMode === "range"
          ? "range"
          : historyWindowMode(event.window);
  const [customPreset, setCustomPreset] = useState(false);
  const exact = windowMode === "exact";
  const preset = customPreset ? "custom" : historyEventPreset(event.where);
  const update = (patch: RuleNode) =>
    onChange({ ...value, history_event: { ...event, ...patch } });
  return (
    <section className="history-event-editor">
      <div className="history-event-heading">
        <span className="history-event-symbol">
          <Clock3 size={17} />
        </span>
        <div>
          <strong>历史出现过 ＋ 今日满足</strong>
          <small>
            例如：前1—30个交易日出现过3倍量，且今日涨幅大于3%或出现技术金叉
          </small>
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
            value={windowMode}
            onChange={(e) => {
              const mode = e.target.value as HistoryWindowMode;
              setWindowMode(mode);
              update({ window: historyWindowForMode(event.window, mode) });
            }}
          >
            <option value="recent">近N个交易日内任一天</option>
            <option value="range">自定义距今日范围</option>
            <option value="exact">指定前N个交易日</option>
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
        ) : windowMode === "recent" ? (
          <label>
            从昨日回看
            <input
              type="number"
              min={1}
              max={750}
              step={1}
              aria-label="近几个交易日内任一天"
              value={event.window?.to ?? ""}
              onChange={(e) =>
                update({
                  window: {
                    from: 1,
                    to: e.target.value === "" ? "" : Number(e.target.value),
                  },
                })
              }
            />
          </label>
        ) : (
          <>
            <label>
              从前几个交易日
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
              到前几个交易日
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
      <p className="history-window-explanation">
        <CalendarRange size={14} />
        <span>
          {exact
            ? historyWindowText(event.window)
            : `前${event.window?.from ?? "?"}—${event.window?.to ?? "?"}个交易日内任一天`}
          <strong>不含今天</strong> · 昨日为前1日，前日为前2日，两端均包含
        </span>
      </p>
      <div className="event-stage event-stage-history">
        <header>
          <span>1</span>
          <div>
            <strong>历史事件条件</strong>
            <small>在上面的历史范围内寻找；事件当日按日K计算</small>
          </div>
        </header>
        <div className="history-event-presets">
          <label>
            事件模板
            <select
              aria-label="历史事件模板"
              value={preset}
              onChange={(e) => {
                const next = e.target.value;
                setCustomPreset(next === "custom");
                if (next === "volume" || next === "limit")
                  update({ where: historyPresetWhere(next) });
              }}
            >
              <option value="volume">倍量事件 · 倍数可修改</option>
              {conditionFields(fields, "where").some(
                (item) => item.field === "limit_up",
              ) && <option value="limit">收盘涨停事件</option>}
              <option value="custom">自定义事件条件</option>
            </select>
          </label>
          <small>模板只修改历史事件；今日条件保持独立。</small>
        </div>
        <TechnicalPresetControls
          value={event.where || { all: [] }}
          onChange={(where) => {
            setCustomPreset(true);
            update({ where });
          }}
          rootValue={rootValue}
          fields={conditionFields(fields, "where")}
          period="D"
          depth={depth + 1}
        />
        <RuleNodeEditor
          rootValue={rootValue}
          value={event.where || { all: [] }}
          onChange={(where) => {
            setCustomPreset(true);
            update({ where });
          }}
          fields={fields}
          topics={topics}
          period="D"
          context="where"
          depth={depth + 1}
        />
      </div>
      <div className="event-stage-connector">
        <ArrowDown size={16} />
        <span>同一次事件 · 可选检查期间价格过程</span>
      </div>
      <HistoryPricePathEditor
        event={event}
        onChange={(next) => onChange({ ...value, history_event: next })}
        allowRebound={fields.some((field) => field.field === "change_pct")}
      />
      <div className="event-stage-connector">
        <ArrowDown size={16} />
        <span>同时满足（AND）· 再检查今日涨幅与技术条件</span>
      </div>
      <div className="event-stage event-stage-current">
        <header>
          <span>3</span>
          <div>
            <strong>今日条件</strong>
            <small>
              涨幅、MACD /
              KD金叉等按观察日核验；不要求必须比较事件日价格。新条件默认日K，旧条件保留原周期。
            </small>
          </div>
        </header>
        <div className="history-today-shortcuts">
          <span>快速添加</span>
          {[
            { field: "change_pct", op: ">", value: 3, label: "今日涨幅 > 3%" },
            { field: "macd_cross", op: "==", value: true, label: "MACD金叉" },
            { field: "kd_cross", op: "==", value: true, label: "KD金叉" },
            {
              field: "close",
              op: ">",
              right_event_field: "close",
              label: "高于事件日收盘",
            },
          ].map(({ label, ...leaf }) => (
            <button
              key={label}
              className="btn small"
              onClick={() =>
                update({ then: appendTodayCondition(event.then, leaf) })
              }
            >
              <Plus size={12} />
              {label}
            </button>
          ))}
        </div>
        <TechnicalPresetControls
          value={event.then || { all: [] }}
          onChange={(then) => update({ then })}
          rootValue={rootValue}
          fields={conditionFields(fields, "then")}
          period="D"
          depth={depth + 1}
        />
        <RuleNodeEditor
          rootValue={rootValue}
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
          距离按市场交易日日历计算，范围1—750日，默认前1—30日且不含今天。多个历史日期满足时，按所选顺序绑定最近或最早一次；选定后今日条件不满足，不会改选另一个事件。更优先日期若待核验，保留未知。涨停仅使用同日已核验来源收盘池与原始成交报价，缺来源不按涨幅猜测。历史题材、身份、跟踪与滚动涨停次数暂不开放。事件价比较跨越未核验公司行动或缺报时保持待核验。
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
        <code>
          {hasHiddenConditionRule(value)
            ? HIDDEN_CONDITION_TEXT
            : JSON.stringify(value)}
        </code>
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
            历史出现过＋今日满足
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
      <TechnicalPresetControls
        value={group}
        onChange={onChange}
        rootValue={group}
        fields={conditionFields(fields, "current")}
        period={period}
        depth={0}
      />
      <RuleNodeEditor
        rootValue={group}
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
