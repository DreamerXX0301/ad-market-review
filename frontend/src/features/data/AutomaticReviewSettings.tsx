import { useEffect, useRef, useState } from "react";
import { Clock3, RefreshCw, Save } from "lucide-react";
import { api, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { Badge, ErrorBox, Panel, Spinner, Status } from "../../components/UI";
import {
  automaticReviewSettings,
  automaticSettingsChanged,
  automaticTimeError,
} from "./automaticReviewPresentation";
import type { AutomaticReviewSettingsValue } from "./automaticReviewPresentation";
import "./AutomaticReviewSettings.css";

const PATH = "/settings/automatic-tasks";
const PLANS = [
  {
    kind: "noon",
    title: "自动午评",
    subtitle: "默认12:05 · 当天13:00前有效",
    min: "11:31",
    max: "12:59",
  },
  {
    kind: "after_close",
    title: "自动盘后复盘",
    subtitle: "默认18:30 · 当天18:00后有效",
    min: "18:00",
    max: "23:59",
  },
] as const;

export default function AutomaticReviewSettings({
  onSelectTask,
}: {
  onSelectTask?: (id: string) => void;
}) {
  const active = usePageActive();
  const result = useApi<Row>(PATH);
  const saved = automaticReviewSettings(result.data);
  const [draft, setDraft] = useState<AutomaticReviewSettingsValue | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const value = draft || saved;
  const dirty = !!draft && !!saved && automaticSettingsChanged(draft, saved);
  const validation = value
    ? automaticTimeError("noon", value.noon.time) ||
      automaticTimeError("after_close", value.after_close.time)
    : "";
  const change = (
    kind: "noon" | "after_close",
    patch: { enabled?: boolean; time?: string },
  ) => {
    if (!value || pending) return;
    setDraft({ ...value, [kind]: { ...value[kind], ...patch } });
    setError("");
    setNotice("");
  };
  const reload = () => {
    if (inFlight.current) return;
    setDraft(null);
    setError("");
    setNotice("");
    result.setData(null);
    result.reload();
  };
  const save = async () => {
    if (!active || inFlight.current || !value || !dirty || validation) return;
    inFlight.current = true;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const response = await api<Row>(PATH, {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: value.revision,
          noon: value.noon,
          after_close: value.after_close,
        }),
      });
      if (!mounted.current) return;
      if (!automaticReviewSettings(response))
        throw new Error(
          "保存响应不完整，请重新读取确认设置，暂不声称保存成功。",
        );
      result.setData(response);
      setDraft(null);
      setNotice(
        "设置已保存。服务端worker按北京时间检查；保存计划不代表任务已经创建或执行。",
      );
    } catch (problem: any) {
      if (mounted.current)
        setError(
          (problem?.message || "自动复盘设置保存失败。") +
            (problem?.status === 409 && !problem?.incrementLocked
              ? " 请重新读取最新设置后再保存。"
              : ""),
        );
    } finally {
      inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  };
  const checked = (kind: "noon" | "after_close") => {
    const item = saved?.last_checks?.[kind];
    if (!item || typeof item !== "object")
      return (
        <p className="automatic-review-check muted">
          暂无调度检查记录，不代表当天已经执行。
        </p>
      );
    return (
      <div className="automatic-review-check">
        <p>
          {typeof item.reason === "string"
            ? item.reason
            : "最近检查未提供说明。"}
        </p>
        <p className="muted">
          {item.date ? <span>任务日 {item.date} · </span> : null}
          检查于 {item.checked_at ? dateTime(item.checked_at) : "—"}{" "}
          {item.task_status && <Status value={item.task_status} />}
        </p>
        {typeof item.task_id === "string" && item.task_id && onSelectTask && (
          <button
            type="button"
            className="btn small"
            onClick={() => onSelectTask(item.task_id)}
          >
            查看已有任务
          </button>
        )}
      </div>
    );
  };
  return (
    <Panel
      title="自动午评与盘后复盘"
      subtitle="新安装默认关闭；保存开启后，由本实例唯一worker按北京时间检查。"
      actions={
        <Badge
          tone={
            saved
              ? saved.noon.enabled || saved.after_close.enabled
                ? "good"
                : "neutral"
              : "warn"
          }
        >
          {saved
            ? saved.noon.enabled || saved.after_close.enabled
              ? "自动已开启"
              : "自动未开启"
            : "设置待读取"}
        </Badge>
      }
    >
      <div className="automatic-review-settings">
        {!saved &&
          (result.loading ? (
            <p className="automatic-review-loading">
              <Spinner />
              正在读取自动复盘设置…
            </p>
          ) : (
            <ErrorBox
              message={result.error || "自动复盘设置尚未完整读取，暂不可修改。"}
              retry={reload}
            />
          ))}
        {saved && result.error && (
          <ErrorBox message={result.error} retry={reload} />
        )}
        <div className="automatic-review-plans">
          {PLANS.map((plan) => (
            <section key={plan.kind}>
              <label className="automatic-review-toggle">
                <input
                  type="checkbox"
                  checked={value?.[plan.kind].enabled ?? false}
                  disabled={!value || pending || !active}
                  onChange={(event) =>
                    change(plan.kind, { enabled: event.target.checked })
                  }
                />
                <span>
                  <strong>{plan.title}</strong>
                  <small>{plan.subtitle}</small>
                </span>
              </label>
              <label className="automatic-review-time">
                北京时间
                <input
                  type="time"
                  step="60"
                  min={plan.min}
                  max={plan.max}
                  value={value?.[plan.kind].time || ""}
                  disabled={!value || pending || !active}
                  onChange={(event) =>
                    change(plan.kind, { time: event.target.value })
                  }
                  aria-label={plan.title + "执行时间（北京时间）"}
                />
              </label>
              {checked(plan.kind)}
            </section>
          ))}
        </div>
        <p className="automatic-review-policy">
          <Clock3 size={14} aria-hidden="true" />
          仅当天有效窗口补跑；午评13:00后不补，盘后跨零点不追历史。电脑和API/worker需保持运行，页面打开不负责计时。
        </p>
        <p className="automatic-review-policy">
          仅市场交易日执行；日历未核验、未初始化或来源未就绪时保留原因。同日已有运行、已完成、失败、暂停或取消任务不会重复创建；失败或显式暂停/取消请到原任务手动处理。首次初始化仍由你手动发起。
        </p>
        <p className="automatic-review-policy">
          自动盘后复盘也会显示全局进度并锁定业务操作；完成后需要你确认结果才解锁。上次结果尚未确认时，后续自动盘后复盘等待确认，不会绕过或自动关闭进度框。
        </p>
        {validation && (
          <p className="automatic-review-error" role="alert">
            {validation}
          </p>
        )}
        {error && <ErrorBox message={error} />}
        {notice && (
          <p className="automatic-review-success" role="status">
            {notice}
          </p>
        )}
        <footer className="automatic-review-actions">
          <span className="muted">
            {dirty
              ? "有未保存的修改，仅保存后的开关和时间生效。"
              : saved
                ? "当前显示服务端已保存设置。"
                : "尚未读取服务端设置。"}
            {saved?.now ? " 服务器时间 " + dateTime(saved.now) : ""}
          </span>
          <div>
            <button
              type="button"
              className="btn small"
              disabled={pending || !active}
              onClick={reload}
            >
              <RefreshCw size={13} />
              {dirty ? "放弃修改并重新读取" : "重新读取"}
            </button>
            <button
              type="button"
              className="btn primary small"
              disabled={
                !saved ||
                !dirty ||
                pending ||
                !!validation ||
                result.loading ||
                !active
              }
              onClick={save}
            >
              {pending ? <Spinner /> : <Save size={13} />}
              {pending ? "保存中…" : "保存自动设置"}
            </button>
          </div>
        </footer>
      </div>
    </Panel>
  );
}
