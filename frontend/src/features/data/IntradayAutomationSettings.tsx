import { useEffect, useRef, useState } from "react";
import { Clock3, RefreshCw } from "lucide-react";
import { api, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { ErrorBox, Spinner } from "../../components/UI";
import "./AutomaticReviewSettings.css";

const PATH = "/market/intraday/automation";
export function intradayAutomationValue(value?: Row | null): Row | null {
  return value &&
    typeof value.enabled === "boolean" &&
    Number.isInteger(value.revision) &&
    value.revision >= 0
    ? value
    : null;
}

export function confirmedIntradayAutomation(before: Row, next?: Row | null) {
  return intradayAutomationValue(next) &&
    next!.enabled === !before.enabled &&
    next!.revision > before.revision
    ? next
    : null;
}

/** The existing independent revision and authorization endpoint are preserved. */
export default function IntradayAutomationSettings() {
  const active = usePageActive();
  const settings = useApi<Row>(PATH);
  const value = intradayAutomationValue(settings.data);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!active) return;
    const followLink = () => {
      if (
        location.hash.startsWith("#data?") &&
        new URLSearchParams(location.hash.split("?")[1]).get("section") ===
          "intraday-automatic"
      )
        document
          .getElementById("intraday-automatic-settings")
          ?.scrollIntoView({ block: "start", inline: "nearest" });
    };
    followLink();
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, [active]);

  async function toggle() {
    if (!active || !value || inFlight.current || settings.loading) return;
    inFlight.current = true;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await api<Row>(PATH, {
        method: "PUT",
        body: JSON.stringify({
          enabled: !value.enabled,
          expected_revision: value.revision,
        }),
      });
      if (!mounted.current) return;
      const next = confirmedIntradayAutomation(value, response);
      if (!next) throw new Error("尚未确认自动采集设置，请重新读取状态。");
      settings.setData(next);
      setNotice(next.enabled ? "盘中自动采集已开启。" : "盘中自动采集已关闭。");
    } catch (problem) {
      if (mounted.current) {
        setError(
          problem instanceof Error ? problem.message : "自动采集设置暂不可用。",
        );
        settings.reload();
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  }
  const slots = Array.isArray(value?.slots)
    ? value.slots.filter(
        (slot: Row) =>
          typeof slot.start === "string" && /^\d{2}:\d{2}$/.test(slot.start),
      )
    : [];
  return (
    <section
      id="intraday-automatic-settings"
      className="intraday-automatic-settings"
    >
      <div className="intraday-automatic-setting-row">
        <label className="automatic-review-toggle">
          <input
            type="checkbox"
            checked={value?.enabled ?? false}
            disabled={!value || settings.loading || saving || !active}
            onChange={toggle}
          />
          <span>
            <strong>自动盘中观察</strong>
            <small>保存盘中快照 · 开关立即生效</small>
          </span>
        </label>
        <button
          type="button"
          className="icon-btn"
          aria-label="刷新盘中观察设置"
          title="刷新盘中观察设置"
          disabled={saving || settings.loading}
          onClick={() => {
            setError("");
            setNotice("");
            settings.reload();
          }}
        >
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="automatic-review-time">
        <Clock3 size={14} aria-hidden="true" />
        {slots.length ? "采集时段" : "采集时段待读取"}
      </div>
      {!!slots.length && (
        <ul
          className="intraday-automatic-slots"
          aria-label="采集时段（北京时间）"
        >
          {slots.map((slot: Row) => (
            <li key={slot.start}>{slot.start}</li>
          ))}
        </ul>
      )}
      <div className="automatic-review-check muted">
        在已核验交易日的有效窗口内保存，错过时段不补采。
      </div>
      {saving && (
        <p className="automatic-review-loading">
          <Spinner />
          正在保存…
        </p>
      )}
      {(error || settings.error) && (
        <ErrorBox message={error || settings.error} retry={settings.reload} />
      )}
      {!value && !settings.loading && !settings.error && (
        <p className="automatic-review-error">设置尚未完整读取，暂不可修改。</p>
      )}
      {notice && (
        <p className="automatic-review-success" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}
