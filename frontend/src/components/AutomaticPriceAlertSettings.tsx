import { useEffect, useRef, useState } from "react";
import { BellRing, Settings2 } from "lucide-react";
import { api, notifyReadChanges, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import {
  AUTOMATIC_PRICE_ALERTS_CHANGED,
  LINE_ALERT_CHECK_FINISHED,
} from "../lib/workbenchEvents";
import { LINE_ALERTS_OPEN } from "../features/watchlist/lineAlertNotification";
import { ErrorBox, Loading, Modal, useToast } from "./UI";
import { num } from "../lib/format";
import { refreshTaskActivity } from "./taskActivity";
import {
  automaticAlertConfig,
  automaticAlertDraft,
  automaticAlertDraftMatches,
  automaticAlertPayload,
  automaticPriceAlertModes,
  quickAutomaticAlert,
} from "./automaticPriceAlertPresentation";
import type {
  AutomaticAlertDraft,
  AutomaticAlertMode,
} from "./automaticPriceAlertPresentation";
import "./AutomaticPriceAlertSettings.css";

export default function AutomaticPriceAlertSettings({
  instrumentId,
  name,
  onViewAlerts,
  initialOpen = false,
}: {
  instrumentId: string;
  name?: string;
  onViewAlerts?: () => void;
  initialOpen?: boolean;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <>
      <button type="button" className="btn small" onClick={() => setOpen(true)}>
        <Settings2 size={13} />
        提醒设置
      </button>
      <button
        type="button"
        className="btn small"
        onClick={() => {
          onViewAlerts?.();
          window.location.hash = "watchlist?alerts=today";
          window.dispatchEvent(new Event(LINE_ALERTS_OPEN));
        }}
      >
        <BellRing size={13} />
        查看提醒
      </button>
      {open && (
        <AutomaticPriceAlertEditor
          key={instrumentId}
          instrumentId={instrumentId}
          name={name}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

function AutomaticPriceAlertEditor({
  instrumentId,
  name,
  onClose,
}: {
  instrumentId: string;
  name?: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const path = `/instruments/${encodeURIComponent(instrumentId)}/automatic-price-alerts`;
  const result = useApi<Row>(path);
  const [saved, setSaved] = useState<Row | null>(null);
  const [draft, setDraft] = useState<AutomaticAlertDraft | null>(null);
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const live = useRef(true);
  const inFlight = useRef(false);
  const config = automaticAlertConfig(saved || result.data, instrumentId);
  const quick =
    !customOpen &&
    config &&
    draft &&
    automaticAlertDraftMatches(draft, config.rule)
      ? quickAutomaticAlert(config.rule)
      : null;
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    const received = automaticAlertConfig(result.data, instrumentId);
    if (received) {
      setSaved(null);
      setDraft((old) => old || automaticAlertDraft(received.rule));
      setDraftRevision((old) => old ?? received.rule.revision);
    }
  }, [result.data, instrumentId]);
  useEffect(() => {
    const refresh = () => {
      setSaved(null);
      result.reload();
    };
    window.addEventListener(LINE_ALERT_CHECK_FINISHED, refresh);
    window.addEventListener(AUTOMATIC_PRICE_ALERTS_CHANGED, refresh);
    return () => {
      window.removeEventListener(LINE_ALERT_CHECK_FINISHED, refresh);
      window.removeEventListener(AUTOMATIC_PRICE_ALERTS_CHANGED, refresh);
    };
  }, [result.reload]);
  const save = async (
    nextDraft: AutomaticAlertDraft | null,
    resetMonitoring = false,
  ) => {
    if (!nextDraft || !config || result.loading || inFlight.current) return;
    if (draftRevision !== config.rule.revision) {
      setError("配置已在其他位置更新，请重新读取后再保存");
      return;
    }
    let payload;
    try {
      payload = automaticAlertPayload(nextDraft, config);
      if (
        resetMonitoring &&
        !automaticAlertDraftMatches(nextDraft, config.rule)
      ) {
        throw new Error("请先保存或重新读取表单改动，再重新设定短线基准");
      }
    } catch (problem) {
      setError((problem as Error).message);
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await api<Row>(path, {
        method: "PUT",
        body: JSON.stringify(
          resetMonitoring ? { ...payload, reset_monitoring: true } : payload,
        ),
      });
      const next = automaticAlertConfig(response, instrumentId);
      if (!next) throw new Error("配置回执与当前股票不匹配，请重新读取");
      if (next.rule.enabled !== nextDraft.enabled)
        throw new Error("提醒开关状态未更新，请重新读取后再试");
      notifyReadChanges([{ scope: "price_alerts" }]);
      window.dispatchEvent(
        new CustomEvent(AUTOMATIC_PRICE_ALERTS_CHANGED, {
          detail: instrumentId,
        }),
      );
      refreshTaskActivity();
      if (live.current) {
        setSaved(next);
        setDraft(automaticAlertDraft(next.rule));
        setDraftRevision(next.rule.revision);
        const successMessage = !next.rule.enabled
          ? "已关闭自动量价提醒。"
          : next.task
            ? "配置已保存，下一交易日参考位已安排准备。"
            : "自动量价提醒配置已保存。";
        if (resetMonitoring) {
          setMessage(successMessage);
        } else {
          toast(successMessage);
          onClose();
        }
      }
    } catch (problem) {
      if (live.current) setError((problem as Error).message);
    } finally {
      inFlight.current = false;
      if (live.current) setBusy(false);
    }
  };
  const evaluation = config?.latest_evaluation;
  const baseline = config?.latest_baseline;
  const modeSummary = (settings: AutomaticAlertDraft) =>
    settings.modes
      .map((mode) => {
        if (mode === "stop_breakdown" && settings.profile === "short_week_v1")
          return "短线风险线跌破";
        if (mode === "close_breakout" && settings.require_volume)
          return "放量收盘突破";
        return automaticPriceAlertModes[mode].title;
      })
      .join("、");
  return (
    <Modal title={`${name || instrumentId} · 自动量价提醒`} onClose={onClose}>
      <div className="automatic-price-alert-settings">
        <p className="automatic-price-alert-intro">
          按日 K 收盘确认。启用后从下一交易日起检查。
        </p>
        {result.loading && !config ? (
          <Loading />
        ) : config && draft ? (
          <>
            <div className="automatic-price-alert-status-row">
              <div className="automatic-price-alert-status" role="status">
                <strong>{config.rule.enabled ? "已启用" : "当前关闭"}</strong>
                {(config.rule.enabled || config.rule.modes.length > 0) && (
                  <span>
                    {config.rule.profile === "short_week_v1"
                      ? `一周短线 · 距离上限 ${num(config.rule.max_risk_pct)}%`
                      : "历史结构"}
                    {config.rule.modes.length > 0 &&
                      ` · ${modeSummary(automaticAlertDraft(config.rule))}`}
                  </span>
                )}
                {evaluation && (
                  <span>
                    {evaluation.state === "prepared"
                      ? "已准备次日参考位"
                      : evaluation.state === "completed"
                        ? "最近核验完成"
                        : "最近核验状态待确认"}{" "}
                    · {evaluation.target_date} · {evaluation.reason}
                  </span>
                )}
              </div>
              {config.rule.enabled && (
                <button
                  type="button"
                  className="btn small"
                  title="关闭后保留已保存的条件"
                  disabled={busy || result.loading}
                  onClick={() =>
                    void save({
                      ...automaticAlertDraft(config.rule),
                      enabled: false,
                    })
                  }
                >
                  关闭自动提醒
                </button>
              )}
            </div>
            {quick && (
              <div className="automatic-price-alert-quick">
                {!quick.usesSavedRule && (
                  <div>
                    <strong>一周短线 · {quick.draft.max_risk_pct}%</strong>
                    <small>{modeSummary(quick.draft)}</small>
                  </div>
                )}
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || result.loading}
                  onClick={() => void save(quick.draft)}
                >
                  {busy ? "保存中…" : quick.label}
                </button>
              </div>
            )}
            <details
              className="automatic-price-alert-custom"
              onToggle={(event) => setCustomOpen(event.currentTarget.open)}
            >
              <summary>自定义提醒</summary>
              <div className="automatic-price-alert-fields">
                <label className="automatic-price-alert-enabled">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    disabled={busy}
                    onChange={(event) =>
                      setDraft({ ...draft, enabled: event.target.checked })
                    }
                  />
                  <span>
                    <strong>启用这只股票的自动提醒</strong>
                  </span>
                </label>
                <div className="automatic-price-alert-profile">
                  <label>
                    <span>提醒参考模式</span>
                    <select
                      aria-label="提醒参考模式"
                      value={draft.profile}
                      disabled={busy}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          profile: event.target
                            .value as AutomaticAlertDraft["profile"],
                        })
                      }
                    >
                      <option value="short_week_v1">一周短线</option>
                      <option value="structure">历史结构</option>
                    </select>
                  </label>
                  {draft.profile === "short_week_v1" ? (
                    <>
                      <label>
                        <span>风险距离上限（%）</span>
                        <input
                          type="number"
                          aria-label="风险距离上限百分比"
                          min={0.5}
                          max={20}
                          step={0.01}
                          value={
                            Number.isFinite(draft.max_risk_pct)
                              ? draft.max_risk_pct
                              : ""
                          }
                          disabled={busy}
                          onChange={(event) =>
                            setDraft({
                              ...draft,
                              max_risk_pct:
                                event.target.value === ""
                                  ? NaN
                                  : Number(event.target.value),
                            })
                          }
                        />
                      </label>
                      <div
                        className="automatic-price-alert-risk-presets"
                        role="group"
                        aria-label="风险距离快捷选择"
                      >
                        {[3, 5, 8].map((value) => (
                          <button
                            key={value}
                            type="button"
                            className="btn small"
                            disabled={busy}
                            aria-pressed={draft.max_risk_pct === value}
                            onClick={() =>
                              setDraft({ ...draft, max_risk_pct: value })
                            }
                          >
                            {value}%
                          </button>
                        ))}
                      </div>
                      <small>
                        从启用或重新设定时的收盘准备基准，后续风险线只抬高不下调；失守后保持，需重新设定才重置。这是收盘监控参考，不是个人成本止损，也不保证成交损失。
                      </small>
                    </>
                  ) : (
                    <small>
                      沿用历史结构和ATR缓冲，参考位可能较远；已有历史规则不会自动转换成短线模式。
                    </small>
                  )}
                </div>
                <div
                  className="automatic-price-alert-modes"
                  role="group"
                  aria-label="自动量价提醒条件"
                >
                  {(
                    Object.keys(
                      automaticPriceAlertModes,
                    ) as AutomaticAlertMode[]
                  ).map((mode) => (
                    <label key={mode} data-risk={mode.endsWith("breakdown")}>
                      <input
                        type="checkbox"
                        checked={draft.modes.includes(mode)}
                        disabled={busy}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            modes: event.target.checked
                              ? [...draft.modes, mode]
                              : draft.modes.filter((item) => item !== mode),
                          })
                        }
                      />
                      <span>
                        <strong>
                          {mode === "stop_breakdown"
                            ? draft.profile === "short_week_v1"
                              ? "短线风险线跌破"
                              : "结构失效参考跌破"
                            : automaticPriceAlertModes[mode].title}
                        </strong>
                        <small>
                          {mode === "stop_breakdown"
                            ? "收盘跌破已保存的事前风险参考位"
                            : automaticPriceAlertModes[mode].hint}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
                <label className="automatic-price-alert-volume">
                  <input
                    type="checkbox"
                    checked={draft.require_volume}
                    disabled={busy || !draft.modes.includes("close_breakout")}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        require_volume: event.target.checked,
                      })
                    }
                  />
                  <span>
                    突破需放量确认
                    <small>
                      仅用于收盘突破，成交量至少为此前20日均量的1.5倍；不影响失守和止损提醒
                    </small>
                  </span>
                </label>
              </div>
            </details>
            {baseline && (
              <details className="automatic-price-alert-basis">
                <summary>已保存的下一交易日参考位 · {baseline.as_of}</summary>
                <p>
                  来源：已发布个股日K · 未复权原价 · 截至 {baseline.as_of} ·{" "}
                  {baseline.algorithm}
                </p>
                <div>
                  {(
                    [
                      ["support", "支撑"],
                      ["breakout", "突破"],
                      [
                        "stop",
                        config.rule.profile === "short_week_v1"
                          ? "短线风险线"
                          : "结构失效参考",
                      ],
                    ] as const
                  ).map(([key, label]) => (
                    <span key={key}>
                      {label}{" "}
                      <strong>
                        {typeof baseline.levels?.[key]?.price === "number"
                          ? `${num(baseline.levels[key].price)} 元`
                          : "暂无确认价位"}
                      </strong>
                    </span>
                  ))}
                </div>
                <small>
                  {config.rule.profile === "short_week_v1"
                    ? "这组已保存价位才是提醒核验依据。图表短线风险线按观察日收盘及默认5%上限重新展示，与本规则的逐股上限及只上移监控基准分别读取。"
                    : "这组价位用于后续交易日核验，不替代图表当前参考线。没有新增确认拐点时可沿用原价位。"}
                </small>
                {baseline.levels?.stop?.state === "breached" && (
                  <>
                    <p className="automatic-price-alert-breached">
                      短线风险线已失守，保留原基准；需明确重新设定后才开始新一轮监控。
                    </p>
                    {config.rule.enabled &&
                      config.rule.profile === "short_week_v1" && (
                        <button
                          type="button"
                          className="btn small"
                          disabled={busy}
                          onClick={() => void save(draft, true)}
                        >
                          重新设定短线基准
                        </button>
                      )}
                  </>
                )}
              </details>
            )}
          </>
        ) : (
          !result.error && (
            <ErrorBox message="自动提醒配置尚未核验" retry={result.reload} />
          )
        )}
        {(error || result.error) && (
          <ErrorBox
            message={error || result.error}
            retry={() => {
              setSaved(null);
              setDraft(null);
              setDraftRevision(null);
              result.reload();
            }}
          />
        )}
        {message && (
          <p className="automatic-price-alert-message" role="status">
            {message}
          </p>
        )}
        <div className="automatic-price-alert-actions">
          <button type="button" className="btn" onClick={onClose}>
            关闭
          </button>
          <button
            type="button"
            className="btn primary"
            hidden={!!quick}
            disabled={!!quick || busy || !config || !draft || result.loading}
            onClick={() => void save(draft)}
          >
            {busy ? "保存中…" : "保存设置"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
