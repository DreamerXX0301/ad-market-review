import { useRef, useState } from "react";
import {
  CheckCircle2,
  Clock3,
  Power,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { post } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Badge, ErrorBox, useToast } from "../../components/UI";
import { noonClock } from "./noonPresentation";
import { auctionAutomationView, auctionCodes } from "./auctionPresentation";

type Mode = "off" | "once" | "daily";
const RUN_STATES: Record<string, string> = {
  queued: "已排队",
  running: "采集中",
  paused: "已暂停",
  cancelled: "已取消",
  failed: "本次未完成",
  available: "结果已就绪",
  completed: "任务完成",
  succeeded: "结果已就绪",
  expired: "已错过窗口",
  purged: "已到期清理",
  skipped: "本次跳过",
  not_trading_day: "非交易日",
  waiting: "等待采集窗口",
  waiting_retry: "窗口内等待重试",
};

export default function AuctionAutomation({
  value,
  loading,
  error,
  reload,
  onSaved,
}: {
  value: Row | null;
  loading: boolean;
  error: string;
  reload: () => void;
  onSaved: (value: Row) => void;
}) {
  const settings = auctionAutomationView(value);
  const notify = useToast();
  const [editing, setEditing] = useState(false);
  const [baseline, setBaseline] = useState<Row | null>(null);
  const [mode, setMode] = useState<Mode>("off");
  const [codes, setCodes] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const inFlight = useRef(false);
  const parsed = auctionCodes(codes);
  const changed =
    settings && baseline && settings.revision !== baseline.revision;
  const enabled = settings?.enabled === true && settings.mode !== "off";
  const loadDraft = () => {
    if (!settings || inFlight.current) return;
    setBaseline(settings);
    setMode(settings.mode);
    setCodes(
      Array.isArray(settings.instrument_ids)
        ? settings.instrument_ids.join("\n")
        : "",
    );
    setSaveError("");
    setEditing(true);
  };
  async function save(next: Mode, direct = false) {
    const source = direct ? settings : baseline;
    if (
      !source ||
      inFlight.current ||
      (!direct && (changed || parsed.error)) ||
      (next !== "off" && settings?.can_enable !== true)
    )
      return;
    inFlight.current = true;
    setSaving(true);
    setSaveError("");
    try {
      const result = await post<Row>("/market/auction/automation", {
        mode: next,
        expected_revision: source.revision,
        ...(next === "once" ? { date: source.today } : {}),
        ...(next === "off" ? {} : { instrument_ids: parsed.ids }),
      });
      if (!auctionAutomationView(result))
        throw Error("服务没有返回可核验的自动采集设置，请重新读取状态。");
      onSaved(result);
      setEditing(false);
      setBaseline(null);
      notify(
        next === "off"
          ? "自动竞价已关闭，后续改为手动采集。"
          : next === "once"
            ? "已授权今天在窗口内自动采集一次。"
            : "已授权每个交易日自动采集竞价。",
      );
    } catch (problem: any) {
      setSaveError(
        problem.message || "设置保存未完成，请核对重新读取的服务状态。",
      );
      reload();
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }
  const last = settings?.last_run;
  return (
    <section
      className={`auction-automation ${enabled ? "is-enabled" : ""}`}
      aria-label="服务自动竞价设置"
    >
      <div className="auction-automation-head">
        <div>
          <span className="auction-automation-icon">
            {enabled ? <CheckCircle2 size={18} /> : <Power size={18} />}
          </span>
          <div>
            <h2>
              服务自动采集：
              {settings ? (enabled ? "已开启" : "已关闭") : "状态待确认"}
            </h2>
            <p>
              {settings
                ? enabled
                  ? settings.mode === "daily"
                    ? "每个交易日，由项目服务按已授权设置采集。"
                    : `仅 ${settings.date || settings.today} 自动采集一次。`
                  : "当前仅手动采集；需要自动运行时，先在这里保存授权。"
                : "正在读取服务设置，页面不会自行开启自动采集。"}
            </p>
          </div>
        </div>
        <div className="auction-head-actions">
          <button
            className="btn small"
            onClick={reload}
            disabled={loading || saving}
            aria-label="重新读取自动竞价设置"
          >
            <RefreshCw size={13} />
            状态
          </button>
          {!editing && (
            <button
              className="btn small"
              onClick={loadDraft}
              disabled={!settings || saving}
            >
              <Settings2 size={13} />
              {enabled ? "修改设置" : "设置自动采集"}
            </button>
          )}
          {enabled && !editing && (
            <button
              className="btn small"
              onClick={() => save("off", true)}
              disabled={saving}
            >
              {saving ? "正在关闭…" : "关闭，改为手动"}
            </button>
          )}
        </div>
      </div>
      <div className="auction-automation-facts">
        <span>
          <Clock3 size={13} />
          {settings?.start_time?.slice(0, 8) || "09:25:05"} 至 09:30前 ·
          北京时间
        </span>
        <span>服务运行时，浏览器可以关闭</span>
        <span>错过窗口不补跑</span>
        {settings && (
          <Badge tone={settings.worker?.alive === true ? "good" : "warn"}>
            {settings.worker?.alive === true
              ? "工作进程在线"
              : "工作进程未确认在线"}
          </Badge>
        )}
      </div>
      {settings && (
        <div className="auction-automation-status">
          <span>今天 {settings.today}</span>
          <span>
            下一次{" "}
            {settings.next_run_at
              ? noonClock(settings.next_run_at)
              : "暂无已确定时间"}
          </span>
          {last && (
            <span>
              最近 {last.date} ·{" "}
              {RUN_STATES[last.state] || last.state || "待核验"}
              {last.attempts != null ? ` · 尝试${last.attempts}次` : ""}
            </span>
          )}
        </div>
      )}
      {settings?.reason && (
        <p className="auction-automation-reason">{settings.reason}</p>
      )}
      {last?.reason && (
        <p className="auction-automation-reason">最近执行：{last.reason}</p>
      )}
      {error && <ErrorBox message={error} retry={reload} />}
      {saveError && <ErrorBox message={saveError} retry={reload} />}
      {editing && baseline && (
        <div className="auction-automation-editor">
          <fieldset disabled={saving}>
            <legend>设置项目服务的自动采集范围</legend>
            <div className="auction-automation-options">
              {(
                [
                  ["off", "仅手动"],
                  ["once", `今天自动一次 · ${baseline.today}`],
                  ["daily", "每个交易日自动"],
                ] as [Mode, string][]
              ).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="radio"
                    name="auction-automation-mode"
                    checked={mode === key}
                    onChange={() => setMode(key)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          {mode !== "off" && (
            <>
              <label
                className="auction-auto-codes-label"
                htmlFor="auction-auto-codes"
              >
                自动采集名单{" "}
                <span>
                  留空使用昨日原关注与主线、自选、前日已核验强势样本，最多100只。
                </span>
              </label>
              <textarea
                id="auction-auto-codes"
                value={codes}
                onChange={(event) => setCodes(event.target.value)}
                disabled={saving}
                rows={2}
                placeholder="选填完整代码，如600519.SH, 000001.SZ"
              />
              {parsed.error && (
                <p className="auction-automation-error">{parsed.error}</p>
              )}
            </>
          )}
          {changed && (
            <p className="auction-automation-error">
              服务设置已被更新，请载入最新设置后再保存。
              <button className="btn small" onClick={loadDraft}>
                载入最新设置
              </button>
            </p>
          )}
          <div className="auction-automation-editor-foot">
            <p>
              这是今天及后续服务的全局设置，不跟随上方历史观察日。关闭后不再自动触发，已提交的原任务可在采集进度中暂停或取消。
            </p>
            <div className="button-group">
              <button
                className="btn"
                onClick={() => setEditing(false)}
                disabled={saving}
              >
                取消修改
              </button>
              <button
                className="btn primary"
                onClick={() => save(mode)}
                disabled={
                  saving ||
                  !!changed ||
                  (mode !== "off" &&
                    (!!parsed.error || settings?.can_enable !== true))
                }
              >
                {saving
                  ? "正在保存…"
                  : mode === "off"
                    ? "关闭自动采集"
                    : "保存并开启"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
