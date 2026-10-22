import { useState } from "react";
import {
  Clock3,
  GitBranch,
  Plus,
  Trash2,
  ArrowDown,
  CalendarRange,
  WandSparkles,
  X,
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
  historyMatchOptions,
  historyMatchDescription,
  historyEventPreset,
  historyPresetWhere,
  appendTodayCondition,
  appendTodayCrossAlternative,
  applyPreviousCrossPreset,
  newPreviousCrossRule,
  previousCrossConfigurationError,
} from "./historicalRules";
import type { RuleNode, HistoryWindowMode } from "./historicalRules";
import type { ConditionScope } from "./conditionScope";
import { periodLabel } from "./conditionPeriod";
import {
  availableTechnicalPresets,
  createTechnicalPreset,
  appendTechnicalPreset,
  technicalPresetInsertionError,
  kdRangeStartingPoints,
  validateKdRange,
  ruleReplacementError,
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
  stageRoot?: boolean;
  onRemove?: () => void;
};

function TechnicalPresetControls({
  value,
  onChange,
  rootValue,
  fields,
  period,
  depth,
  scope,
}: {
  value: RuleNode;
  onChange: (next: RuleNode) => void;
  rootValue: RuleNode;
  fields: Row[];
  period: string;
  depth: number;
  scope?: ConditionScope;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<TechnicalPresetId | "">("");
  const [added, setAdded] = useState<{
    text: string;
    definition: string;
  } | null>(null);
  const [upper, setUpper] = useState<number | "">(20);
  const [lower, setLower] = useState<number | "">(80);
  const [rangeMin, setRangeMin] = useState<number | "">(0);
  const [rangeMax, setRangeMax] = useState<number | "">(100);
  const available = availableTechnicalPresets(fields, period, scope);
  const preset = available.find((item) => item.id === selected);
  if (!available.length) return null;
  const invalidUpper =
    preset?.id === "kd-low-cross" &&
    (upper === "" || !Number.isFinite(upper) || upper < 0 || upper > 100);
  const invalidLower =
    preset?.id === "kd-high-dead" &&
    (lower === "" || !Number.isFinite(lower) || lower < 0 || lower > 100);
  const isRange = preset?.id === "kd-cross-range" || preset?.id === "kd-range";
  const rangeError = isRange ? validateKdRange(rangeMin, rangeMax) : null;
  const rule =
    !preset || invalidUpper || invalidLower || rangeError
      ? null
      : createTechnicalPreset(
          preset.id,
          period,
          preset?.id === "kd-high-dead"
            ? Number(lower)
            : upper === ""
              ? 20
              : upper,
          {
            min: Number(rangeMin),
            max: Number(rangeMax),
          },
        );
  const error = invalidUpper
    ? "KDJ低位上限请输入0—100的有限数值"
    : invalidLower
      ? "KDJ高位下限请输入0—100的有限数值"
      : rangeError ||
        (rule
          ? technicalPresetInsertionError(rootValue, value, rule, depth)
          : null);
  const feedback =
    added?.definition === JSON.stringify(value) ? added.text : null;
  const close = () => {
    setOpen(false);
    setSelected("");
  };
  return (
    <div className={"technical-preset-entry" + (open ? " open" : "")}>
      <div className="technical-preset-launcher">
        <button
          type="button"
          className="btn small technical-preset-trigger"
          aria-expanded={open}
          onClick={() => {
            setOpen(!open);
            setSelected("");
            setAdded(null);
          }}
        >
          <WandSparkles size={13} /> {open ? "收起快捷添加" : "快捷添加条件"}
        </button>
        {!open && feedback && (
          <span className="technical-preset-feedback" role="status">
            {feedback}
          </span>
        )}
      </div>
      {open && (
        <div
          className="technical-preset-controls"
          role="region"
          aria-label="快捷添加条件面板"
        >
          <header className="technical-preset-heading">
            <div>
              <strong>选择条件 → 加入条件组 → 保存策略</strong>
              <small>
                选择模板不会自动生效。点击“加入条件”后，会出现在本组规则中；保存策略后生效。
              </small>
            </div>
            <button
              type="button"
              className="icon-btn"
              aria-label="取消快捷添加"
              onClick={close}
            >
              <X size={14} />
            </button>
          </header>
          <div className="technical-preset-fields">
            <label>
              选择要添加的条件
              <select
                aria-label="快捷条件模板"
                value={preset?.id || ""}
                onChange={(event) =>
                  setSelected(event.target.value as TechnicalPresetId)
                }
              >
                <option value="">请选择条件</option>
                {available.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {preset?.id === "kd-low-cross" && (
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
                      event.target.value === ""
                        ? ""
                        : Number(event.target.value),
                    )
                  }
                />
              </label>
            )}
            {preset?.id === "kd-high-dead" && (
              <label className="technical-preset-upper">
                K、D高位下限
                <input
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  aria-label="KDJ高位下限"
                  value={lower}
                  onChange={(event) =>
                    setLower(
                      event.target.value === ""
                        ? ""
                        : Number(event.target.value),
                    )
                  }
                />
              </label>
            )}
            {isRange && (
              <>
                <label className="technical-preset-upper">
                  K值下限（含）
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step="any"
                    aria-label="K值范围下限"
                    value={rangeMin}
                    onChange={(event) =>
                      setRangeMin(
                        event.target.value === ""
                          ? ""
                          : Number(event.target.value),
                      )
                    }
                  />
                </label>
                <label className="technical-preset-upper">
                  K值上限（含）
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step="any"
                    aria-label="K值范围上限"
                    value={rangeMax}
                    onChange={(event) =>
                      setRangeMax(
                        event.target.value === ""
                          ? ""
                          : Number(event.target.value),
                      )
                    }
                  />
                </label>
              </>
            )}
            <span className="technical-preset-period">
              {periodLabel(period)}
            </span>
          </div>
          {isRange && (
            <div className="technical-range-starts">
              <span>可编辑起点</span>
              {kdRangeStartingPoints.map((point) => (
                <button
                  type="button"
                  className="btn small"
                  key={point.label}
                  onClick={() => {
                    setRangeMin(point.min);
                    setRangeMax(point.max);
                  }}
                >
                  {point.label} {point.min}—{point.max}
                </button>
              ))}
              <small>
                边界含等号，20与80可重合；不是互斥分类。KDJ仍使用原9、3、3算法。
              </small>
            </div>
          )}
          {preset && (
            <div className="technical-preset-preview">
              <span>将加入本组</span>
              <strong>
                {rule ? historyRuleSummary(rule, fieldLabel) : preset.label}
              </strong>
              <small>{preset.description}</small>
            </div>
          )}
          {error && (
            <small className="technical-preset-error" role="alert">
              {error}
            </small>
          )}
          <div className="technical-preset-actions">
            <button type="button" className="btn small" onClick={close}>
              取消添加
            </button>
            <button
              type="button"
              className="btn small primary"
              disabled={!rule || !!error}
              onClick={() => {
                if (!preset || !rule || error) return;
                const next = appendTechnicalPreset(value, rule);
                onChange(next);
                setAdded({
                  text: `已加入“${preset.label}”，保存策略后生效。`,
                  definition: JSON.stringify(next),
                });
                close();
              }}
            >
              <Plus size={13} /> 加入条件
            </button>
          </div>
        </div>
      )}
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
function HistoryRuleAddControl({
  rootValue,
  value,
  onChange,
  fields,
  depth,
}: {
  rootValue: RuleNode;
  value: RuleNode;
  onChange: (value: RuleNode) => void;
  fields: Row[];
  depth: number;
}) {
  const [template, setTemplate] = useState("standard");
  const supported = (name: string) =>
    conditionFields(fields, "where").some((item) => item.field === name);
  const choices = [
    { value: "standard", label: "普通历史事件＋今日条件" },
    ...(supported("macd_cross") && fields.some((item) => item.field === "close")
      ? [{ value: "macd", label: "MACD本次金叉价高于上次" }]
      : []),
    ...(supported("kd_cross") && fields.some((item) => item.field === "close")
      ? [{ value: "kdj", label: "KDJ本次金叉价高于上次" }]
      : []),
  ];
  const selected = choices.some((item) => item.value === template)
    ? template
    : "standard";
  const next =
    selected === "standard"
      ? newHistoryRule()
      : newPreviousCrossRule(selected as "macd" | "kdj");
  const error = technicalPresetInsertionError(rootValue, value, next, depth);
  return (
    <div className="history-add-control">
      <select
        aria-label="新增历史规则模板"
        value={selected}
        onChange={(e) => setTemplate(e.target.value)}
      >
        {choices.map((item) => (
          <option value={item.value} key={item.value}>
            {item.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn small history-add"
        disabled={!!error}
        title={error || undefined}
        onClick={() => {
          if (!error) onChange(appendTechnicalPreset(value, next));
        }}
      >
        <Clock3 size={13} />{" "}
        {selected === "standard" ? "历史出现过＋今日满足" : "添加上次金叉模板"}
      </button>
      {selected !== "standard" && (
        <small>
          日K · 窗口内最近一次 ·
          默认60日；只含金叉、周期与事件价比较，不附加今日涨幅。
        </small>
      )}
      {error && <small className="technical-preset-error">{error}</small>}
    </div>
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
  const [templateError, setTemplateError] = useState<string | null>(null);
  const exact = windowMode === "exact";
  const preset = customPreset ? "custom" : historyEventPreset(event.where);
  const update = (patch: RuleNode) => {
    setTemplateError(null);
    onChange({ ...value, history_event: { ...event, ...patch } });
  };
  const commitTemplate = (next: RuleNode) => {
    const nextNode = { ...value, history_event: next };
    const error = ruleReplacementError(rootValue, value, nextNode, depth);
    setTemplateError(error);
    if (!error) {
      onChange(nextNode);
      return true;
    }
    return false;
  };
  const supported = (name: string) =>
    conditionFields(fields, "where").some((item) => item.field === name);
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
          事件匹配方式
          <select
            aria-label="历史事件匹配顺序"
            value={event.match || "latest"}
            onChange={(e) => update({ match: e.target.value })}
          >
            {historyMatchOptions.map((item) => (
              <option value={item.value} key={item.value}>
                {item.label}
              </option>
            ))}
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
          <br />
          {historyMatchDescription(event.match || "latest")}
        </span>
      </p>
      <div className="history-window-quick">
        <span>回看起点</span>
        {[30, 60].map((sessions) => (
          <button
            type="button"
            className="btn small"
            key={sessions}
            onClick={() => {
              setWindowMode("recent");
              update({ window: { from: 1, to: sessions } });
            }}
          >
            近{sessions}个交易日
          </button>
        ))}
      </div>
      {templateError && (
        <p className="history-price-error" role="alert">
          {templateError}
        </p>
      )}
      {previousCrossConfigurationError(event) && (
        <p className="history-price-error" role="alert">
          {previousCrossConfigurationError(event)}
        </p>
      )}
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
                if (next === "macd-cross" || next === "kdj-cross") {
                  if (
                    commitTemplate(
                      applyPreviousCrossPreset(
                        event,
                        next === "macd-cross" ? "macd" : "kdj",
                      ),
                    )
                  ) {
                    setCustomPreset(false);
                    setWindowMode("recent");
                  }
                } else {
                  if (next === "custom") setCustomPreset(true);
                  if (
                    next === "volume" ||
                    next === "limit" ||
                    next === "change" ||
                    next === "any-signal"
                  ) {
                    if (
                      commitTemplate({
                        ...event,
                        where: historyPresetWhere(next),
                      })
                    )
                      setCustomPreset(false);
                  }
                }
              }}
            >
              <option value="volume">倍量事件 · 倍数可修改</option>
              {conditionFields(fields, "where").some(
                (item) => item.field === "limit_up",
              ) && <option value="limit">收盘涨停事件</option>}
              {supported("change_pct") && (
                <option value="change">涨幅事件 · 默认 &gt;5%，可修改</option>
              )}
              {supported("volume_ratio") &&
                supported("limit_up") &&
                supported("change_pct") && (
                  <option value="any-signal">
                    倍量 / 涨停 / 涨幅任一（OR）
                  </option>
                )}
              {supported("macd_cross") && (
                <option value="macd-cross">MACD本次金叉价高于上次 · 日K</option>
              )}
              {supported("kd_cross") && (
                <option value="kdj-cross">KDJ本次金叉价高于上次 · 日K</option>
              )}
              <option value="custom">自定义事件条件</option>
            </select>
          </label>
          <small>
            普通模板只修改历史事件；上次金叉模板设为前1—60日/最近纯金叉，并追加今日同类金叉与事件价高于比较。已有今日条件全部保留（包括涨幅），请核对是否需要移除。
          </small>
        </div>
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
          stageRoot
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
          {fields.some((item) => item.field === "macd_cross") &&
            fields.some((item) => item.field === "kd_cross") && (
              <button
                type="button"
                className="btn small"
                onClick={() =>
                  commitTemplate(appendTodayCrossAlternative(event))
                }
              >
                <Plus size={12} /> KD或MACD金叉（OR）
              </button>
            )}
        </div>
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
          stageRoot
        />
      </div>
      <details className="history-rule-method">
        <summary>事件匹配口径与未知处理</summary>
        <p>
          距离按市场交易日日历计算，范围1—750日，默认前1—30日且不含今天。
          {historyMatchDescription(event.match || "latest")}
          涨停仅使用同日已核验来源收盘池与原始成交报价，缺来源不按涨幅猜测。历史题材、身份、跟踪与滚动涨停次数暂不开放。事件价比较跨越未核验公司行动或缺报时保持待核验。
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
        <>
          <Conditions
            conditions={[value as any]}
            onChange={(next) =>
              next.length ? onChange(next[0]) : onRemove?.()
            }
            fields={candidates}
            topics={topics}
            scope={context === "where" ? "selection" : props.scope}
            allowTrackingAlerts={
              context !== "where" && props.allowTrackingAlerts
            }
            allowTrackingConditions={
              context !== "where" && props.allowTrackingConditions
            }
            allowEventReferences={context === "then"}
            period={context === "where" ? "D" : period}
            dailyContext={context === "where"}
            hidePeriodPicker={context !== "current"}
            showAddButton={false}
          />
          {props.stageRoot && (
            <div className="visual-rule-actions">
              <TechnicalPresetControls
                value={value}
                onChange={onChange}
                rootValue={props.rootValue}
                fields={candidates}
                period="D"
                depth={depth}
              />
            </div>
          )}
        </>
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
              stageRoot={false}
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
        <TechnicalPresetControls
          value={value}
          onChange={onChange}
          rootValue={props.rootValue}
          fields={candidates}
          period={context === "current" ? period : "D"}
          depth={depth}
          scope={context === "current" ? props.scope : undefined}
        />
        {context === "current" && depth < 4 && (
          <HistoryRuleAddControl
            rootValue={props.rootValue}
            value={value}
            onChange={onChange}
            fields={fields}
            depth={depth}
          />
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
