import type { Row } from "../../lib/api";
import { Plus, Trash2 } from "lucide-react";
import { sectorDefaultLeaf, sectorLeafEditorKind } from "./sectorPresentation";
import "./SectorWorkspace.css";

export default function SectorRuleBuilder({
  value,
  onChange,
  fields,
  period,
  depth = 0,
}: {
  value: Row;
  onChange: (value: Row) => void;
  fields: Row[];
  period: string;
  depth?: number;
}) {
  const group = Array.isArray(value.all)
    ? "all"
    : Array.isArray(value.any)
      ? "any"
      : null;
  if (group) {
    const children: Row[] = value[group];
    return (
      <section className="sector-rule-group">
        <header>
          <select
            aria-label="板块条件组逻辑"
            value={group}
            onChange={(event) => onChange({ [event.target.value]: children })}
          >
            <option value="all">全部满足</option>
            <option value="any">任一满足</option>
          </select>
          <span>各条件可独立选择日 / 周 / 月</span>
        </header>
        {children.map((child, index) => (
          <div className="sector-rule-child" key={index}>
            <SectorRuleBuilder
              value={child}
              onChange={(next) =>
                onChange({
                  [group]: children.map((old, i) => (i === index ? next : old)),
                })
              }
              fields={fields}
              period={period}
              depth={depth + 1}
            />
            <button
              className="icon-btn danger-text"
              aria-label="删除板块条件"
              onClick={() =>
                onChange({ [group]: children.filter((_, i) => i !== index) })
              }
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        <div className="button-group">
          <button
            className="btn small"
            disabled={!fields.length}
            onClick={() =>
              onChange({
                [group]: [...children, sectorDefaultLeaf(fields, period)],
              })
            }
          >
            <Plus size={13} />
            添加条件
          </button>
          {depth < 7 && (
            <button
              className="btn small"
              disabled={!fields.length}
              onClick={() =>
                onChange({
                  [group]: [
                    ...children,
                    { any: [sectorDefaultLeaf(fields, period)] },
                  ],
                })
              }
            >
              添加条件组
            </button>
          )}
        </div>
      </section>
    );
  }
  const spec = fields.find((item) => item.field === value.field);
  const selectedPeriod = value.period || spec?.fixed_period || period;
  const rightField = value.right_field != null;
  const lookback = sectorLeafEditorKind(spec) === "lookback";
  const numericFields = fields.filter(
    (field) =>
      field.type === "number" &&
      !field.constant_only &&
      (!field.fixed_period || field.fixed_period === selectedPeriod),
  );
  const changeField = (field: string) => {
    const next = fields.find((item) => item.field === field);
    if (next)
      onChange({
        field,
        ...(next.fixed_period
          ? { period: next.fixed_period }
          : value.period
            ? { period: selectedPeriod }
            : {}),
        op: next.operators[0],
        value: next.type === "boolean" ? true : (next.default ?? 3),
      });
  };
  return (
    <div className="sector-rule-leaf">
      <label>
        条件周期
        <select
          aria-label="板块条件周期"
          disabled={!!spec?.fixed_period}
          value={spec?.fixed_period || value.period || "inherit"}
          onChange={(event) => {
            const next = { ...value };
            if (event.target.value === "inherit") delete next.period;
            else next.period = event.target.value;
            onChange(next);
          }}
        >
          {!spec?.fixed_period && (
            <option value="inherit">
              沿用默认 ·{" "}
              {period === "W" ? "周K" : period === "M" ? "月K" : "日K"}
            </option>
          )}
          {(spec?.periods || ["D", "W", "M"]).map((key: string) => (
            <option key={key} value={key}>
              {key === "D" ? "日K" : key === "W" ? "完整周K" : "完整月K"}
            </option>
          ))}
        </select>
      </label>
      <label>
        板块条件
        <select
          aria-label="板块指标条件"
          value={value.field || ""}
          onChange={(event) => changeField(event.target.value)}
        >
          {!spec && (
            <option value={value.field || ""}>
              {value.field ? `不支持：${value.field}` : "请选择条件"}
            </option>
          )}
          {fields.map((field) => (
            <option key={field.field} value={field.field}>
              {field.label}
            </option>
          ))}
        </select>
      </label>
      {lookback ? (
        <label>
          回看交易日
          <input
            aria-label="板块新高回看交易日"
            type="number"
            min={spec?.min ?? 1}
            max={spec?.max ?? 750}
            step={1}
            value={value.value ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                period: "D",
                value:
                  event.target.value === "" ? "" : Number(event.target.value),
              })
            }
          />
        </label>
      ) : (
        <>
          <label>
            比较
            <select
              aria-label="板块比较符"
              value={value.op || ""}
              onChange={(event) =>
                onChange({ ...value, op: event.target.value })
              }
            >
              {(spec?.operators || [value.op])
                .filter(Boolean)
                .map((operator: string) => (
                  <option key={operator} value={operator}>
                    {operator === "=="
                      ? "等于"
                      : operator === "!="
                        ? "不等于"
                        : operator}
                  </option>
                ))}
            </select>
          </label>
          {spec?.type === "boolean" ? (
            <label>
              状态
              <select
                aria-label="板块信号状态"
                value={String(value.value)}
                onChange={(event) =>
                  onChange({ ...value, value: event.target.value === "true" })
                }
              >
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
          ) : (
            <>
              <label>
                比较对象
                <select
                  aria-label="板块比较对象类型"
                  value={rightField ? "field" : "value"}
                  onChange={(event) => {
                    const next = { ...value };
                    delete next.value;
                    delete next.right_field;
                    onChange(
                      event.target.value === "field"
                        ? {
                            ...next,
                            right_field: numericFields[0]?.field,
                          }
                        : { ...next, value: 0 },
                    );
                  }}
                >
                  <option value="value">数值</option>
                  <option value="field">同周期指标</option>
                </select>
              </label>
              <label>
                {rightField ? "指标" : "阈值"}
                {rightField ? (
                  <select
                    aria-label="板块比较指标"
                    value={value.right_field}
                    onChange={(event) =>
                      onChange({ ...value, right_field: event.target.value })
                    }
                  >
                    {numericFields.map((field) => (
                      <option key={field.field} value={field.field}>
                        {field.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    aria-label="板块数值阈值"
                    type="number"
                    step={spec?.step || "any"}
                    min={spec?.min}
                    max={spec?.max}
                    value={value.value ?? ""}
                    onChange={(event) =>
                      onChange({
                        ...value,
                        value:
                          event.target.value === ""
                            ? ""
                            : Number(event.target.value),
                      })
                    }
                  />
                )}
              </label>
            </>
          )}
        </>
      )}
      {lookback && (
        <small>固定日K，严格突破前N个市场交易日最高价，不含当日。</small>
      )}
    </div>
  );
}
