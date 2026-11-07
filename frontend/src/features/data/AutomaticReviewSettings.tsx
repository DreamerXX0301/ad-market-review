import { useEffect, useRef, useState } from "react";
import { Clock3, RefreshCw, Save } from "lucide-react";
import { api, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { Badge, ErrorBox, Panel, Spinner, Status } from "../../components/UI";
import IntradayAutomationSettings from "./IntradayAutomationSettings";
import {
  automaticReviewSettings,
  automaticSettingsChanged,
  automaticSettingsRequest,
  automaticSettingsStale,
  automaticSavedResponse,
  automaticTimeError,
  automaticStatusText,
  beijingPlanTime,
} from "./automaticReviewPresentation";
import type {
  AutomaticPlan,
  AutomaticReviewSettingsValue,
} from "./automaticReviewPresentation";
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
    subtitle: "默认17:00 · 当天有效",
    min: "17:00",
    max: "23:59",
  },
] as const;

export default function AutomaticReviewSettings({
  onSelectTask,
  onAfterCloseSaved,
}: {
  onSelectTask?: (id: string) => void;
  onAfterCloseSaved?: (plan: AutomaticPlan | null) => void;
}) {
  const active = usePageActive();
  const result = useApi<Row>(PATH);
  const saved = automaticReviewSettings(result.data);
  useEffect(() => {
    onAfterCloseSaved?.(saved?.after_close ?? null);
  }, [onAfterCloseSaved, saved]);
  const [draft, setDraft] = useState<AutomaticReviewSettingsValue | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [needsReload, setNeedsReload] = useState(false);
  const inFlight = useRef(false),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (
      active &&
      typeof window !== "undefined" &&
      window.location.hash === "#data?section=automatic"
    )
      document
        .getElementById("automatic-review-settings")
        ?.scrollIntoView({ block: "start" });
  }, [active]);
  const value = draft || saved;
  const stale = !!draft && !!saved && automaticSettingsStale(draft, saved);
  const unavailable = !saved || !!result.error || result.loading;
  const dirty = !!draft && !!saved && automaticSettingsChanged(draft, saved);
  const auctionToggleAvailable =
    !!value &&
    !unavailable &&
    (value.auction.enabled || saved?.auction.can_enable === true);
  const validation = value
    ? automaticTimeError("noon", value.noon.time) ||
      automaticTimeError("after_close", value.after_close.time)
    : "";
  const change = (
    kind: "noon" | "after_close" | "auction",
    patch: { enabled?: boolean; time?: string },
  ) => {
    if (!value || unavailable || pending || !active) return;
    setDraft({ ...value, [kind]: { ...value[kind], ...patch } });
    setError("");
    setNotice("");
  };
  const reload = () => {
    if (inFlight.current) return;
    setDraft(null);
    setError("");
    setNotice("");
    setNeedsReload(false);
    result.setData(null);
    result.reload();
  };
  const save = async () => {
    if (
      !active ||
      inFlight.current ||
      !value ||
      !saved ||
      unavailable ||
      !dirty ||
      validation ||
      stale ||
      needsReload ||
      (value.auction.enabled &&
        !saved.auction.enabled &&
        !saved.auction.can_enable)
    )
      return;
    inFlight.current = true;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const response = await api<Row>(PATH, {
        method: "PUT",
        body: JSON.stringify(automaticSettingsRequest(value)),
      });
      if (!mounted.current) return;
      const confirmed = automaticSavedResponse(saved, value, response);
      if (!confirmed)
        throw new Error(
          "保存响应不完整或与本次修改不一致，请重新读取确认设置，暂不声称保存成功。",
        );
      result.setData(confirmed);
      setDraft(null);
      setNotice(
        "设置已保存，生效后按北京时间执行；保存计划不代表任务已经创建或执行。",
      );
    } catch (problem: any) {
      if (mounted.current) {
        setNeedsReload(true);
        setError(
          (problem?.message || "自动复盘设置保存失败。") +
            (problem?.status === 409 && !problem?.incrementLocked
              ? " 请重新读取最新设置后再保存。"
              : ""),
        );
      }
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
        <p>{automaticStatusText(item.reason, "最近检查未提供说明。")}</p>
        <p className="muted">
          {item.date ? <span>任务日 {item.date} · </span> : null}
          检查于 {beijingPlanTime(item.checked_at)}{" "}
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
      title="自动采集计划"
      subtitle="新安装默认关闭，开启后按北京时间自动采集。"
      actions={<Badge>北京时间</Badge>}
    >
      <div className="automatic-review-settings" id="automatic-review-settings">
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
          <section className="automatic-review-auction">
            <label
              className={`automatic-review-toggle${auctionToggleAvailable ? "" : " is-unavailable"}`}
            >
              {auctionToggleAvailable && (
                <input
                  type="checkbox"
                  checked={value?.auction.enabled ?? false}
                  disabled={pending || !active}
                  onChange={(event) =>
                    change("auction", { enabled: event.target.checked })
                  }
                />
              )}
              <span>
                <strong>自动盘前竞价</strong>
                <small>生成关注名单 · 09:30 截止</small>
              </span>
              {!auctionToggleAvailable && (
                <Badge tone="warn">
                  {saved && !unavailable ? "暂不可开启" : "状态待读取"}
                </Badge>
              )}
            </label>
            <div className="automatic-review-time automatic-review-auction-time">
              <Clock3 size={14} aria-hidden="true" />
              <strong>09:25:30</strong>
            </div>
            {value?.auction_discovery && (
              <label className="automatic-review-toggle automatic-review-discovery">
                <input
                  type="checkbox"
                  checked={value.auction_discovery.enabled}
                  disabled={
                    pending ||
                    !active ||
                    unavailable ||
                    (!value.auction.enabled && !value.auction_discovery.enabled)
                  }
                  onChange={(event) => {
                    setDraft({
                      ...value,
                      auction_discovery: {
                        revision: value.auction_discovery!.revision,
                        enabled: event.target.checked,
                      },
                    });
                    setError("");
                    setNotice("");
                  }}
                />
                <span>
                  <strong>今日竞价发现</strong>
                  <small>关注名单保存后，查找异动并补查昨日重点股</small>
                </span>
              </label>
            )}
            <div className="automatic-review-check">
              <div className="automatic-review-next">
                <span>下次执行</span>
                <strong>
                  {beijingPlanTime(saved?.auction.next_run_at, "待核验")}
                </strong>
              </div>
              {saved?.auction.reason ? (
                <p>{automaticStatusText(saved.auction.reason)}</p>
              ) : saved && !unavailable && !auctionToggleAvailable ? (
                <p>开启条件尚未满足，请检查初始化和数据源状态。</p>
              ) : null}
              {saved?.auction.legacy_configuration && (
                <p className="automatic-review-error">
                  旧设置已停用，重新保存开关后使用每日计划。
                </p>
              )}
              {saved?.auction.last_run && (
                <p>
                  最近执行：
                  {automaticStatusText(
                    saved.auction.last_run.reason,
                    "尚无执行说明",
                  )}{" "}
                  {saved.auction.last_run.state && (
                    <Status value={saved.auction.last_run.state} />
                  )}
                </p>
              )}
              {typeof saved?.auction.worker?.alive === "boolean" && (
                <Badge tone={saved.auction.worker.alive ? "good" : "neutral"}>
                  {saved.auction.worker.alive ? "服务运行中" : "服务未运行"}
                </Badge>
              )}
            </div>
          </section>
          <IntradayAutomationSettings />
          {PLANS.map((plan) => (
            <section key={plan.kind}>
              <label className="automatic-review-toggle">
                <input
                  type="checkbox"
                  checked={value?.[plan.kind].enabled ?? false}
                  disabled={!value || unavailable || pending || !active}
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
                  disabled={!value || unavailable || pending || !active}
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
          仅交易日且服务运行时执行，浏览器可关闭。错过竞价、午评窗口或盘后跨零点不补跑。
        </p>
        <p className="automatic-review-policy">
          同日已有任务不重复创建，暂停或取消不自动继续；盘后完成需确认结果才解锁。首次初始化仍需手动操作。
        </p>
        {(stale || needsReload) && (
          <p className="automatic-review-error" role="alert">
            设置版本已变化或保存结果需要确认，请放弃修改并重新读取后再保存。
          </p>
        )}
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
            {saved?.now ? " 服务器时间 " + beijingPlanTime(saved.now) : ""}
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
                unavailable ||
                !saved ||
                !dirty ||
                stale ||
                needsReload ||
                (!!value?.auction.enabled &&
                  !saved?.auction.enabled &&
                  !saved?.auction.can_enable) ||
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
