import {
  CheckCircle2,
  Clock3,
  Power,
  RefreshCw,
  Settings2,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { Badge, ErrorBox } from "../../components/UI";
import { noonClock } from "./noonPresentation";
import { auctionAutomationView } from "./auctionPresentation";
import { automaticStatusText } from "../data/automaticReviewPresentation";

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

/** Read-only status here; every authorization lives in Data Center settings. */
export default function AuctionAutomation({
  value,
  loading,
  error,
  reload,
}: {
  value: Row | null;
  loading: boolean;
  error: string;
  reload: () => void;
  onSaved?: (value: Row) => void;
}) {
  const parsed = auctionAutomationView(value);
  const settings =
    parsed && typeof parsed.enabled === "boolean" ? parsed : null;
  const enabled =
    settings?.enabled === true &&
    settings.mode === "daily" &&
    (!Array.isArray(settings.instrument_ids) ||
      settings.instrument_ids.length === 0);
  const last = settings?.last_run;
  return (
    <section
      className={`auction-automation ${enabled ? "is-enabled" : ""}`}
      aria-label="服务自动竞价状态"
    >
      <div className="auction-automation-head">
        <div>
          <span className="auction-automation-icon">
            {enabled ? <CheckCircle2 size={18} /> : <Power size={18} />}
          </span>
          <div>
            <h2>
              服务自动竞价：
              {settings
                ? enabled
                  ? "已开启"
                  : "未开启每日采集"
                : "状态待确认"}
            </h2>
            <p>
              {settings
                ? enabled
                  ? "每个交易日按系统关注范围自动采集。"
                  : "需要开启或关闭每日竞价时，请到数据中心的自动采集计划设置。"
                : "正在读取服务状态，页面不会自行开启采集。"}
            </p>
          </div>
        </div>
        <div className="auction-head-actions">
          <button
            type="button"
            className="btn small"
            onClick={reload}
            disabled={loading}
            aria-label="重新读取自动竞价状态"
          >
            <RefreshCw size={13} /> 状态
          </button>
          <a className="btn small" href="#data?section=automatic">
            <Settings2 size={13} /> 去数据中心设置
          </a>
        </div>
      </div>
      <div className="auction-automation-facts">
        <span>
          <Clock3 size={13} /> 北京时间09:25:05 · 当天09:30前有效
        </span>
        <span>服务运行时，浏览器可以关闭</span>
        <span>错过窗口不补跑</span>
        {settings && (
          <Badge tone={settings.worker?.alive === true ? "good" : "warn"}>
            {settings.worker?.alive === true
              ? "服务运行中"
              : settings.worker?.alive === false
                ? "服务未运行"
                : "服务状态待确认"}
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
              : "暂无已核验时间"}
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
        <p className="auction-automation-reason">
          {automaticStatusText(settings.reason)}
        </p>
      )}
      {last?.reason && (
        <p className="auction-automation-reason">
          最近执行：{automaticStatusText(last.reason)}
        </p>
      )}
      {error && <ErrorBox message={error} retry={reload} />}
    </section>
  );
}
