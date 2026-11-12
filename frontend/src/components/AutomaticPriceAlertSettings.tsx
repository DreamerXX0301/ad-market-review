import { useEffect, useRef, useState } from "react";
import { BellRing, Settings2 } from "lucide-react";
import { api, notifyReadChanges, useApi } from "../lib/api";
import type { Row } from "../lib/api";
import {
  AUTOMATIC_PRICE_ALERTS_CHANGED,
  LINE_ALERT_CHECK_FINISHED,
} from "../lib/workbenchEvents";
import { LINE_ALERTS_OPEN } from "../features/watchlist/lineAlertNotification";
import { ErrorBox, Loading, Modal } from "./UI";
import { num } from "../lib/format";
import { refreshTaskActivity } from "./taskActivity";
import {
  automaticAlertConfig,
  automaticAlertDraft,
  automaticAlertPayload,
  automaticPriceAlertModes,
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
}: {
  instrumentId: string;
  name?: string;
  onViewAlerts?: () => void;
}) {
  const [open, setOpen] = useState(false);
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
  const path = `/instruments/${encodeURIComponent(instrumentId)}/automatic-price-alerts`;
  const result = useApi<Row>(path);
  const [saved, setSaved] = useState<Row | null>(null);
  const [draft, setDraft] = useState<AutomaticAlertDraft | null>(null);
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const live = useRef(true);
  const inFlight = useRef(false);
  const config = automaticAlertConfig(saved || result.data, instrumentId);
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
  const save = async () => {
    if (!draft || !config || inFlight.current) return;
    if (draftRevision !== config.rule.revision) {
      setError("配置已在其他位置更新，请重新读取后再保存");
      return;
    }
    let payload;
    try {
      payload = automaticAlertPayload(draft, config);
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
        body: JSON.stringify(payload),
      });
      const next = automaticAlertConfig(response, instrumentId);
      if (!next) throw new Error("配置回执与当前股票不匹配，请重新读取");
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
        setMessage(
          !next.rule.enabled
            ? "已关闭自动量价提醒。"
            : next.task
              ? "配置已保存，正在准备下一交易日参考位。"
              : "自动量价提醒配置已保存。",
        );
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
  return (
    <Modal title={`${name || instrumentId} · 自动量价提醒`} onClose={onClose}>
      <div className="automatic-price-alert-settings">
        <p className="automatic-price-alert-intro">
          盘后数据发布后按收盘确认；使用前一市场交易日已确定价位，保存后新交易日起生效；与手动画线独立。
        </p>
        {result.loading && !config ? (
          <Loading />
        ) : config && draft ? (
          <>
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
                <small>每日准备次日价位，结构未变时价位可能不变</small>
              </span>
            </label>
            <div
              className="automatic-price-alert-modes"
              role="group"
              aria-label="自动量价提醒条件"
            >
              {(
                Object.keys(automaticPriceAlertModes) as AutomaticAlertMode[]
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
                    <strong>{automaticPriceAlertModes[mode].title}</strong>
                    <small>{automaticPriceAlertModes[mode].hint}</small>
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
                  setDraft({ ...draft, require_volume: event.target.checked })
                }
              />
              <span>
                突破需放量确认
                <small>
                  仅用于收盘突破，成交量至少为此前20日均量的1.5倍；不影响失守和止损提醒
                </small>
              </span>
            </label>
            <div className="automatic-price-alert-status" role="status">
              <strong>
                {config.rule.enabled ? "已启用" : "当前关闭"} · 日K收盘确认
              </strong>
              <span>
                {config.rule.effective_after_date
                  ? `保存业务日 ${config.rule.effective_after_date}，后续新交易日起生效`
                  : "尚未启用，不会自动创建提醒"}
              </span>
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
                      ["stop", "参考止损"],
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
                  这组价位用于后续交易日核验，不替代图表当前参考线。没有新增确认拐点时可沿用原价位。
                </small>
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
            disabled={busy || !config || !draft || result.loading}
            onClick={() => void save()}
          >
            {busy ? "保存中…" : "保存设置"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
