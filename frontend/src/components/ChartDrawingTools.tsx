import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Crosshair, Minus, Pencil, Trash2, X } from "lucide-react";
import { api, patch, post, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { useToast } from "./UI";
import { num } from "../lib/format";
import { refreshTaskActivity } from "./taskActivity";
import { useWatchlistMembership } from "../features/watchlist/watchlistStore";
import {
  annotationColors,
  annotationScope,
  lineMatchesScope,
  lineTypeLabels,
  lineTypeModes,
  lineAlertRules,
  newLineSettings,
  savedLineSettings,
} from "./chartAnnotations";
import type { ChartLine, ChartLineType } from "./chartAnnotations";
import "./ChartAnnotations.css";

export function useChartAnnotations(
  instrumentId: string,
  data: Row | null,
  enabled: boolean,
) {
  const path = enabled
    ? `/instruments/${encodeURIComponent(instrumentId)}/annotations`
    : null;
  const result = useApi<Row>(path);
  const scope = annotationScope(data);
  const context = `${instrumentId}:${scope?.price_basis}:${scope?.anchor_date}`;
  const currentContext = useRef(context);
  currentContext.current = context;
  const toast = useToast();
  const membership = useWatchlistMembership();
  const [open, setOpen] = useState(false),
    [drawing, setDrawing] = useState(false),
    [show, setShow] = useState(true),
    [price, setPrice] = useState(""),
    [label, setLabel] = useState(""),
    [color, setColor] = useState("blue"),
    [settings, setSettings] = useState(newLineSettings),
    [editing, setEditing] = useState<ChartLine | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const inFlight = useRef(false);
  useEffect(() => {
    setDrawing(false);
    setEditing(null);
    setPrice("");
    setLabel("");
    setError("");
    setSettings(newLineSettings());
  }, [context]);
  useEffect(() => {
    if (!drawing) return;
    const cancel = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setDrawing(false);
      }
    };
    document.addEventListener("keydown", cancel, true);
    return () => document.removeEventListener("keydown", cancel, true);
  }, [drawing]);
  useEffect(() => {
    const refresh = (event: Event) => {
      if ((event as CustomEvent).detail === instrumentId) result.reload();
    };
    window.addEventListener("chart-annotations-changed", refresh);
    return () =>
      window.removeEventListener("chart-annotations-changed", refresh);
  }, [instrumentId, result.reload]);
  const lines = useMemo<ChartLine[]>(
    () =>
      result.data?.instrument_id === instrumentId
        ? result.data.items || []
        : [],
    [result.data, instrumentId],
  );
  const visible = useMemo(
    () => (show ? lines.filter((line) => lineMatchesScope(line, scope)) : []),
    [lines, show, scope?.price_basis, scope?.anchor_date],
  );
  const picked = (value: number) => {
    if (!enabled || !scope || busy || !Number.isFinite(value) || value <= 0)
      return;
    setPrice(String(Number(value.toFixed(4))));
    setEditing(null);
    setDrawing(false);
    setOpen(true);
  };
  const mutate = async (operation: () => Promise<unknown>, message: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const requestContext = context;
    try {
      await operation();
      refreshTaskActivity();
      window.dispatchEvent(
        new CustomEvent("chart-annotations-changed", { detail: instrumentId }),
      );
      if (currentContext.current === requestContext) {
        setEditing(null);
        setLabel("");
        setPrice("");
        setSettings(newLineSettings());
        toast(message);
      }
    } catch (error) {
      if (currentContext.current === requestContext)
        setError((error as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const save = () => {
    const value = Number(price);
    if (!price.trim() || !Number.isFinite(value) || value <= 0) {
      setError("请输入大于0的有效价格");
      return;
    }
    if (!path || !scope) return;
    const rules = savedLineSettings(settings);
    void mutate(
      () =>
        editing
          ? patch(`${path}/${encodeURIComponent(editing.annotation_id)}`, {
              price: value,
              label,
              color,
              ...rules,
            })
          : post(path, { price: value, label, color, ...scope, ...rules }),
      editing ? "水平线已更新" : "水平线已保存",
    );
  };
  return {
    enabled,
    scope,
    open,
    setOpen,
    drawing,
    setDrawing,
    show,
    setShow,
    price,
    setPrice,
    label,
    setLabel,
    color,
    setColor,
    settings,
    setSettings,
    isWatchlisted: membership.ids.has(instrumentId),
    editing,
    setEditing,
    busy,
    error,
    result,
    lines,
    visible,
    picked,
    save,
    mutate,
  };
}

export type AnnotationController = ReturnType<typeof useChartAnnotations>;
export function ChartAnnotationButton({
  state,
}: {
  state: AnnotationController;
}) {
  if (!state.enabled) return null;
  return (
    <button
      className={`btn small chart-annotation-toggle${state.open ? " active" : ""}`}
      aria-expanded={state.open}
      onClick={() => {
        state.setOpen(!state.open);
        state.setDrawing(false);
      }}
    >
      <Minus size={14} />
      划线{state.lines.length > 0 && <span>{state.lines.length}</span>}
    </button>
  );
}

export function ChartAnnotationPanel({
  state,
}: {
  state: AnnotationController;
}) {
  const typeGroup = useId();
  if (!state.enabled || !state.open) return null;
  const activeScope = state.editing || state.scope;
  return (
    <section className="chart-annotations" aria-label="个股水平线工具">
      <div className="chart-annotation-heading">
        <div>
          <strong>划线与提醒</strong>
          <span>
            {activeScope?.price_basis === "none"
              ? "未复权 · 日／周／月共享"
              : `前复权 · 锚定 ${activeScope?.anchor_date || "待核验"}`}
          </span>
        </div>
        <label>
          <input
            type="checkbox"
            checked={state.show}
            onChange={(e) => state.setShow(e.target.checked)}
          />
          显示标记
        </label>
        <button
          className="icon-btn"
          aria-label="收起水平线工具"
          onClick={() => {
            state.setOpen(false);
            state.setDrawing(false);
          }}
        >
          <X size={15} />
        </button>
      </div>
      <fieldset className="chart-line-types" disabled={state.busy}>
        <legend>线的用途</legend>
        {(["support", "resistance", "reference"] as ChartLineType[]).map(
          (type) => (
            <label
              key={type}
              className={state.settings.line_type === type ? "selected" : ""}
              data-type={type}
            >
              <input
                type="radio"
                name={typeGroup}
                value={type}
                checked={state.settings.line_type === type}
                onChange={() => state.setSettings(newLineSettings(type))}
              />
              <span>{lineTypeLabels[type]}</span>
            </label>
          ),
        )}
      </fieldset>
      <div className="chart-annotation-form">
        <label className="chart-line-price-input">
          线价
          <input
            aria-label="水平线价格"
            type="number"
            min="0.0001"
            step="any"
            value={state.price}
            onChange={(e) => state.setPrice(e.target.value)}
            placeholder="输入价格"
            disabled={state.busy}
          />
        </label>
        <button
          className={`btn small chart-line-pick${state.drawing ? " primary" : ""}`}
          disabled={state.busy || !state.scope}
          onClick={() => {
            state.setDrawing(!state.drawing);
            state.setEditing(null);
            if (state.editing) state.setSettings(newLineSettings());
          }}
        >
          <Crosshair size={14} />
          {state.drawing ? "点击主图选价" : "点图选价"}
        </button>
        <label className="chart-line-name-input">
          备注（选填）
          <input
            aria-label="水平线名称"
            maxLength={40}
            value={state.label}
            onChange={(e) => state.setLabel(e.target.value)}
            placeholder="给这条线起个名字"
            disabled={state.busy}
          />
        </label>
        <div className="chart-line-colors" role="group" aria-label="水平线颜色">
          {Object.entries(annotationColors).map(([value, color]) => (
            <button
              key={value}
              type="button"
              className={state.color === value ? "selected" : ""}
              style={{ "--line-color": color } as import("react").CSSProperties}
              aria-label={`选择${({ blue: "蓝", amber: "金", red: "红", green: "绿", violet: "紫" } as Record<string, string>)[value]}色线`}
              aria-pressed={state.color === value}
              disabled={state.busy}
              onClick={() => state.setColor(value)}
            >
              <span />
            </button>
          ))}
        </div>
        <button
          className="btn small primary chart-line-save"
          onClick={state.save}
          disabled={state.busy || !state.scope || state.result.loading}
        >
          {state.busy ? "保存中…" : state.editing ? "保存修改" : "保存划线"}
        </button>
        {state.editing && (
          <button
            className="btn small"
            disabled={state.busy}
            onClick={() => {
              state.setEditing(null);
              state.setPrice("");
              state.setLabel("");
              state.setSettings(newLineSettings());
            }}
          >
            取消修改
          </button>
        )}
      </div>
      {state.settings.line_type !== "reference" && (
        <div className="chart-line-alert-settings">
          <div className="chart-line-alert-rules">
            {lineTypeModes[state.settings.line_type].map((mode) => {
              const rule = lineAlertRules[mode as keyof typeof lineAlertRules];
              return (
                <label key={mode} title={rule.hint}>
                  <input
                    type="checkbox"
                    checked={state.settings.alert_modes.includes(mode)}
                    disabled={state.busy}
                    onChange={(event) =>
                      state.setSettings((old) => ({
                        ...old,
                        alert_modes: event.target.checked
                          ? [...old.alert_modes, mode]
                          : old.alert_modes.filter((item) => item !== mode),
                      }))
                    }
                  />
                  <span>
                    <strong>
                      {mode === "support_breakdown"
                        ? "支撑失守提醒（风险）"
                        : rule.title}
                    </strong>
                    <small>
                      {
                        (
                          {
                            support_reclaim: "回踩后收盘不低于支撑",
                            support_breakdown: "收盘跌破，不自动入自选",
                            pressure_close_breakout: "从未站上到收盘严格站上",
                            pressure_touch: "日内最低至最高价包含线价",
                          } as Record<string, string>
                        )[mode]
                      }
                    </small>
                  </span>
                </label>
              );
            })}
          </div>
          <label className="chart-line-auto-watchlist">
            <input
              type="checkbox"
              checked={state.settings.auto_watchlist}
              disabled={state.busy || !state.settings.alert_modes.length}
              onChange={(event) =>
                state.setSettings((old) => ({
                  ...old,
                  auto_watchlist: event.target.checked,
                }))
              }
            />
            触发后自动加入自选
            <small>
              {state.isWatchlisted
                ? "已在自选，保留原加入日与基准"
                : "保存后核验当前行情日，命中才加入；失守不加入"}
            </small>
          </label>
          {!state.settings.alert_modes.length && (
            <small className="muted">
              未勾选提醒条件，这条线仅显示在图上。
            </small>
          )}
        </div>
      )}
      {state.drawing ? (
        <p className="chart-annotation-help">
          点击蜡烛主图选价，再保存；Esc 退出定位。
        </p>
      ) : (
        <details className="chart-annotation-explainer">
          <summary>盘后核验 · 口径与使用说明</summary>
          <p>
            保存后核验当前已发布完整日K，之后每次手动行情更新继续检查。提醒记录保存在“我的自选池
            ·
            划线提醒”。移出自选后提醒仍保留；取消全部提醒条件可停用。缺少报价或复权依据时待核验。不同价格口径或锚定日的线独立保留。
          </p>
        </details>
      )}
      {(state.error || state.result.error) && (
        <p className="error-text" role="alert">
          {state.error || state.result.error}
          {state.result.error && (
            <button className="btn small" onClick={state.result.reload}>
              重试
            </button>
          )}
        </p>
      )}
      {state.lines.length > 0 && (
        <ul className="chart-annotation-list">
          {state.lines.map((line) => (
            <li key={line.annotation_id}>
              <i style={{ background: annotationColors[line.color] }} />
              <span
                className="chart-line-type-label"
                data-type={savedLineSettings(line).line_type}
              >
                {lineTypeLabels[savedLineSettings(line).line_type]}
              </span>
              <span>{line.label || "未命名"}</span>
              <strong>{num(line.price, 4)}</strong>
              <span className="chart-line-rule-summary">
                {savedLineSettings(line)
                  .alert_modes.map(
                    (mode) =>
                      lineAlertRules[mode as keyof typeof lineAlertRules].title,
                  )
                  .join(" / ") || "不提醒"}
                {line.auto_watchlist &&
                savedLineSettings(line).alert_modes.length > 0
                  ? " · 触发入自选"
                  : ""}
              </span>
              <small>
                {lineMatchesScope(line, state.scope)
                  ? state.show
                    ? "本图可见"
                    : "已隐藏"
                  : `${line.price_basis === "none" ? "未复权" : "前复权 " + line.anchor_date} · 本图隐藏`}
              </small>
              <button
                className="icon-btn"
                aria-label={`编辑水平线 ${line.label || num(line.price)}`}
                disabled={state.busy}
                onClick={() => {
                  state.setEditing(line);
                  state.setDrawing(false);
                  state.setPrice(String(line.price));
                  state.setLabel(line.label);
                  state.setColor(line.color);
                  state.setSettings(savedLineSettings(line));
                }}
              >
                <Pencil size={13} />
              </button>
              <button
                className="icon-btn danger-text"
                aria-label={`删除水平线 ${line.label || num(line.price)}`}
                disabled={state.busy}
                onClick={() =>
                  void state.mutate(
                    () =>
                      api(
                        `/instruments/${encodeURIComponent(line.instrument_id)}/annotations/${encodeURIComponent(line.annotation_id)}`,
                        { method: "DELETE" },
                      ),
                    "水平线已移除",
                  )
                }
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {!state.result.loading && !state.lines.length && (
        <p className="muted">尚未保存水平线，输入价格或点图定位即可添加。</p>
      )}
    </section>
  );
}
