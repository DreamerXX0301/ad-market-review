import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Crosshair, Minus, Pencil, Trash2, X, Sparkles } from "lucide-react";
import { api, patch, post, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import { useToast } from "./UI";
import { num } from "../lib/format";
import { refreshTaskActivity } from "./taskActivity";
import { useWatchlistMembership } from "../features/watchlist/watchlistStore";
import { LINE_ALERT_CHECK_FINISHED } from "../lib/workbenchEvents";
import {
  annotationColors,
  selectableAnnotationColors,
  defaultLineColor,
  annotationScope,
  projectChartLine,
  lineTypeLabels,
  lineTypeModes,
  lineAlertRules,
  newLineSettings,
  savedLineSettings,
  quietLineSettings,
  levelOriginLabel,
  levelPreviewMatchesChart,
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
  const isIndex = data?.instrument?.kind === "index";
  const context = `${instrumentId}:${scope?.price_basis}:${scope?.anchor_date}:${data?.period}:${data?.as_of}:${data?.snapshot_id}`;
  const currentContext = useRef(context);
  currentContext.current = context;
  const toast = useToast();
  const membership = useWatchlistMembership(enabled && !isIndex);
  const defaults = (type: ChartLineType = "support") =>
    isIndex ? quietLineSettings(type) : newLineSettings(type);
  const [open, setOpen] = useState(false),
    [drawing, setDrawing] = useState(false),
    [show, setShow] = useState(true),
    [price, setPrice] = useState(""),
    [label, setLabel] = useState(""),
    [color, setColor] = useState(defaultLineColor("support")),
    [settings, setSettings] = useState(() => defaults()),
    [editing, setEditing] = useState<ChartLine | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [lookback, setLookback] = useState(120),
    [preview, setPreview] = useState<Row | null>(null),
    [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const inFlight = useRef(false);
  useEffect(() => {
    setDrawing(false);
    setEditing(null);
    setPrice("");
    setLabel("");
    setError("");
    setSettings(defaults());
    setColor(defaultLineColor("support"));
    setPreview(null);
    setSelectedCandidates([]);
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
    const afterCheck = () => result.reload();
    window.addEventListener(LINE_ALERT_CHECK_FINISHED, afterCheck);
    return () => {
      window.removeEventListener("chart-annotations-changed", refresh);
      window.removeEventListener(LINE_ALERT_CHECK_FINISHED, afterCheck);
    };
  }, [instrumentId, result.reload]);
  const lines = useMemo<ChartLine[]>(
    () =>
      result.data?.instrument_id === instrumentId
        ? result.data.items || []
        : [],
    [result.data, instrumentId],
  );
  const views = useMemo(
    () => lines.map((line) => projectChartLine(line, data)),
    [lines, data],
  );
  const visible = useMemo(
    () =>
      show
        ? [
            ...views.filter((view) => view.price != null),
            ...(
              (levelPreviewMatchesChart(preview, data, instrumentId)
                ? preview?.items
                : []) || []
            )
              .filter(
                (item: Row) =>
                  !item.saved && selectedCandidates.includes(item.candidate_id),
              )
              .map((item: Row) => ({
                line: {
                  ...item,
                  annotation_id: `preview:${item.candidate_id}`,
                  instrument_id: instrumentId,
                  label: item.line_type === "support" ? "候选支撑" : "候选压力",
                  color: item.line_type === "support" ? "green" : "red",
                } as ChartLine,
                price: item.price,
                converted: false,
                anchor: item.anchor_date,
                reason: "尚未保存的本次候选",
              })),
          ]
        : [],
    [views, show, preview, selectedCandidates, instrumentId, data],
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
        setSettings(defaults());
        setColor(defaultLineColor("support"));
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
  const calculateLevels = async () => {
    if (!path || !scope || !data?.snapshot_id || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const requestContext = context;
    try {
      const response = await post<Row>(`${path}/recommendations`, {
        period: data.period,
        as_of: data.as_of,
        lookback,
        price_basis: scope.price_basis === "none" ? "none" : "forward",
        snapshot_id: data.snapshot_id,
      });
      if (currentContext.current !== requestContext) return;
      setPreview(response);
      setSelectedCandidates(
        (response.items || [])
          .filter((item: Row) => !item.saved)
          .map((item: Row) => item.candidate_id),
      );
      setShow(true);
    } catch (error) {
      if (currentContext.current === requestContext)
        setError((error as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const saveLevels = () => {
    if (!path || !preview || !selectedCandidates.length) return;
    const requestContext = context;
    void mutate(async () => {
      await post(`${path}/recommendations/save`, {
        parameters: preview.parameters,
        preview_id: preview.preview_id,
        candidate_ids: selectedCandidates,
      });
      if (currentContext.current === requestContext) {
        setPreview(null);
        setSelectedCandidates([]);
      }
    }, "支撑压力线已保存，未启用提醒或自动自选");
  };
  return {
    enabled,
    isIndex,
    defaults,
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
    views,
    visible,
    picked,
    save,
    mutate,
    lookback,
    setLookback,
    preview: levelPreviewMatchesChart(preview, data, instrumentId)
      ? preview
      : null,
    setPreview,
    selectedCandidates,
    setSelectedCandidates,
    calculateLevels,
    saveLevels,
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
  displayedIds,
  onDisplayChange,
  onDisplayAllChange,
}: {
  state: AnnotationController;
  displayedIds?: ReadonlySet<string>;
  onDisplayChange?: (annotationId: string, visible: boolean) => void;
  onDisplayAllChange?: (visible: boolean) => void;
}) {
  const typeGroup = useId();
  if (!state.enabled || !state.open) return null;
  const activeScope = state.editing || state.scope;
  const available = state.views.filter((view) => view.price != null);
  const availableIds = [
    ...available.map((view) => view.line.annotation_id),
    ...(state.preview?.items || [])
      .filter(
        (item: Row) =>
          !item.saved && state.selectedCandidates.includes(item.candidate_id),
      )
      .map((item: Row) => `preview:${item.candidate_id}`),
  ];
  const anyDisplayed = displayedIds
    ? availableIds.some((id) => displayedIds.has(id))
    : state.show;
  const allDisplayed = displayedIds
    ? anyDisplayed && availableIds.every((id) => displayedIds.has(id))
    : state.show;
  return (
    <section className="chart-annotations" aria-label="支撑压力与水平线工具">
      <div className="chart-annotation-heading">
        <div>
          <strong>{state.isIndex ? "指数划线与支撑压力" : "划线与提醒"}</strong>
          <span>
            {activeScope?.price_basis === "none"
              ? "未复权 · 日／周／月共享"
              : `前复权 · 锚定 ${activeScope?.anchor_date || "待核验"}`}
          </span>
        </div>
        <label>
          <input
            type="checkbox"
            checked={allDisplayed}
            aria-checked={
              anyDisplayed && !allDisplayed ? "mixed" : allDisplayed
            }
            onChange={(e) =>
              (onDisplayAllChange || state.setShow)(e.target.checked)
            }
          />
          显示本图划线
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
      <div className="chart-auto-levels">
        <div className="chart-auto-levels-actions">
          <strong>
            <Sparkles size={14} /> 自动支撑压力
          </strong>
          <label>
            回看
            <select
              aria-label="支撑压力回看范围"
              value={state.lookback}
              disabled={state.busy}
              onChange={(event) => {
                state.setLookback(Number(event.target.value));
                state.setPreview(null);
                state.setSelectedCandidates([]);
              }}
            >
              {[60, 120, 240].map((value) => (
                <option key={value} value={value}>
                  {value}根当前周期K线
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn small"
            disabled={state.busy || !state.scope || state.result.loading}
            onClick={() => void state.calculateLevels()}
          >
            {state.busy
              ? "处理中…"
              : state.preview
                ? "重新计算候选"
                : "计算候选并预览"}
          </button>
        </div>
        <p className="chart-annotation-help">
          按当前图表周期、观察日和价格口径，从本地行情寻找已确认历史分型。先预览勾选，再保存；重算不会改动已保存的线。
        </p>
        {state.preview && (
          <>
            <p className="chart-annotation-help">
              观察 {state.preview.parameters.as_of} ·{" "}
              {
                (
                  { D: "日K", W: "完整周K", M: "完整月K" } as Record<
                    string,
                    string
                  >
                )[state.preview.parameters.period]
              }{" "}
              ·
              {state.preview.parameters.price_basis === "none"
                ? "未复权"
                : "本地前复权"}{" "}
              · 完整报价 {state.preview.complete_bars}根
              {state.preview.reference_price != null && (
                <>
                  {" "}
                  · 参考收盘 {num(state.preview.reference_price, 4)}（
                  {state.preview.reference_date}）
                </>
              )}
            </p>
            <div className="chart-auto-levels-list">
              {(state.preview.items || []).map((item: Row) => (
                <label key={item.candidate_id} data-type={item.line_type}>
                  <input
                    type="checkbox"
                    disabled={state.busy || item.saved}
                    checked={
                      item.saved ||
                      state.selectedCandidates.includes(item.candidate_id)
                    }
                    onChange={(event) =>
                      state.setSelectedCandidates((old) =>
                        event.target.checked
                          ? [...old, item.candidate_id]
                          : old.filter((id) => id !== item.candidate_id),
                      )
                    }
                  />
                  <span>
                    {item.line_type === "support" ? "支撑" : "压力"}{" "}
                    <strong>{num(item.price, 4)}</strong>
                  </span>
                  <small>
                    {item.touches}处分型 · 最近 {item.last_pivot_date} ·{" "}
                    {item.confirmed_on}确认{item.saved ? " · 已保存" : ""}
                  </small>
                </label>
              ))}
            </div>
            {state.preview.reason && (
              <p className="chart-annotation-help">{state.preview.reason}</p>
            )}
            <p className="chart-annotation-help">{state.preview.notice}</p>
            {!!state.preview.items?.length && (
              <button
                className="btn small primary"
                disabled={state.busy || !state.selectedCandidates.length}
                onClick={state.saveLevels}
              >
                保存所选支撑压力线（{state.selectedCandidates.length}）
              </button>
            )}
            <button
              className="btn small"
              disabled={state.busy}
              onClick={() => {
                state.setPreview(null);
                state.setSelectedCandidates([]);
              }}
            >
              收起候选
            </button>
          </>
        )}
      </div>
      {state.editing && (
        <p className="chart-annotation-help">
          正在修改原保存线价（
          {state.editing.price_basis === "none"
            ? "未复权"
            : `前复权锚 ${state.editing.anchor_date}`}
          ）；图上换算价仅用于显示，保存不更换原锚。
        </p>
      )}
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
                onChange={() => {
                  state.setSettings(state.defaults(type));
                  state.setColor(defaultLineColor(type));
                }}
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
            if (state.editing) {
              state.setSettings(state.defaults());
              state.setColor(defaultLineColor("support"));
            }
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
          {selectableAnnotationColors.map((value) => (
            <button
              key={value}
              type="button"
              className={state.color === value ? "selected" : ""}
              style={
                {
                  "--line-color": annotationColors[value],
                } as import("react").CSSProperties
              }
              aria-label={`选择${({ blue: "蓝", amber: "金", red: "红", green: "绿", violet: "紫" } as Record<string, string>)[value]}色线`}
              aria-pressed={state.color === value}
              disabled={state.busy}
              onClick={() => state.setColor(value)}
            >
              <span />
            </button>
          ))}
        </div>
        <div className="chart-line-actions">
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
                state.setSettings(state.defaults());
                state.setColor(defaultLineColor("support"));
              }}
            >
              取消修改
            </button>
          )}
        </div>
      </div>
      {!state.isIndex && state.settings.line_type !== "reference" && (
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
      {state.isIndex && (
        <p className="chart-annotation-help">
          指数线仅保存与展示点位，不创建个股提醒或自动加入自选。
        </p>
      )}
      {state.drawing ? (
        <p className="chart-annotation-help">
          点击蜡烛主图选价，再保存；Esc 退出定位。
        </p>
      ) : (
        <details className="chart-annotation-explainer">
          <summary>
            {state.isIndex ? "划线口径与使用说明" : "盘后核验 · 口径与使用说明"}
          </summary>
          <p>
            {state.isIndex ? (
              "指数保存后跨日／周／月共享，仅按原始点位显示，不执行个股提醒。"
            ) : (
              <>
                保存后核验当前已发布完整日K，之后每次手动行情更新继续检查。提醒记录保存在“我的自选池
                ·
                划线提醒”。移出自选后提醒仍保留；取消全部提醒条件可停用。同为前复权时，旧锚线按已核验公司行动换算到当前图；原线价、原锚及提醒证据保留。不同价格口径或缺少区间依据时不显示，并在列表说明原因。
              </>
            )}
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
          {state.views.map((view) => {
            const line = view.line;
            return (
              <li key={line.annotation_id}>
                <i style={{ background: annotationColors[line.color] }} />
                <span
                  className="chart-line-type-label"
                  data-type={savedLineSettings(line).line_type}
                >
                  {lineTypeLabels[savedLineSettings(line).line_type]}
                </span>
                <span>{line.label || "未命名"}</span>
                {line.origin?.algorithm && (
                  <span
                    className="chart-line-origin"
                    title={`${levelOriginLabel(line)} · ${line.origin.algorithm} · ${line.origin.snapshot_id}`}
                  >
                    {levelOriginLabel(line)}
                  </span>
                )}
                <strong title={`原保存线价 · ${line.anchor_date || "未复权"}`}>
                  {num(line.price, 4)}
                </strong>
                <span className="chart-line-rule-summary">
                  {savedLineSettings(line)
                    .alert_modes.map(
                      (mode) =>
                        lineAlertRules[mode as keyof typeof lineAlertRules]
                          .title,
                    )
                    .join(" / ") || "不提醒"}
                  {line.auto_watchlist &&
                  savedLineSettings(line).alert_modes.length > 0
                    ? " · 触发入自选"
                    : ""}
                  {line.first_alert_date
                    ? ` · 首次提醒 ${line.first_alert_date}，此后继续60个交易日`
                    : savedLineSettings(line).alert_modes.length > 0
                      ? " · 未提醒，持续监控"
                      : ""}
                </span>
                <small title={view.reason}>
                  {view.price != null
                    ? (
                        displayedIds
                          ? displayedIds.has(line.annotation_id)
                          : state.show
                      )
                      ? view.converted
                        ? `本图 ${num(view.price, 4)} · 已换算`
                        : "本图可见"
                      : "已隐藏"
                    : view.reason}
                </small>
                {onDisplayChange && (
                  <label
                    className="chart-line-display-control"
                    title="只切换本图显示，不删除原线或更改提醒"
                  >
                    <input
                      type="checkbox"
                      aria-label={`显示水平线 ${line.label || num(line.price)}`}
                      checked={
                        view.price != null &&
                        (displayedIds
                          ? displayedIds.has(line.annotation_id)
                          : state.show)
                      }
                      disabled={view.price == null}
                      onChange={(event) =>
                        onDisplayChange(
                          line.annotation_id,
                          event.target.checked,
                        )
                      }
                    />
                    显示
                  </label>
                )}
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
            );
          })}
        </ul>
      )}
      {!state.result.loading && !state.lines.length && (
        <p className="muted">尚未保存水平线，输入价格或点图定位即可添加。</p>
      )}
    </section>
  );
}
