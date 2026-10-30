import { CalendarDays } from "lucide-react";
import "./MarketDateNotice.css";

export default function MarketDateNotice({
  date,
  today,
  latest,
  intraday,
  fixed,
  published,
  onReset,
}: {
  date: string;
  today: string;
  latest: string;
  intraday: boolean;
  fixed: boolean;
  published: boolean;
  onReset: () => void;
}) {
  if (!date) return null;
  const historical = intraday
    ? !!today && date < today
    : (!!latest && date < latest) || (fixed && !!today && date < today);
  const unavailable = !intraday && !published;
  const latestFromPast =
    !intraday && published && date === latest && !!today && date < today;
  const title = unavailable
    ? "所选日期尚无已发布盘后行情"
    : historical
      ? "正在查看历史数据"
      : intraday
        ? date === today
          ? "正在查看今天"
          : "正在查看所选日期"
        : fixed
          ? "已固定观察日期"
          : latestFromPast
            ? "最新已发布行情（非今日）"
            : "最新已发布行情";
  const description = fixed
    ? `${latestFromPast ? `今天是 ${today}，当前仍为 ${date} 的历史收盘行情。` : ""}已保留当前观察日期；点击右侧按钮恢复默认浏览。`
    : intraday
      ? "当天未采集时显示缺失；切回基础行情查看最新已发布日。"
      : latestFromPast
        ? `今天是 ${today}，当前显示 ${date} 的历史收盘行情。盘中或休市时可查看最近发布日；盘后请到数据中心核对更新状态。`
        : "以已发布收盘行情为准；进入竞价、午评自动查看当天。";
  return (
    <div
      className={`market-date-notice${historical || unavailable || latestFromPast ? " is-history" : ""}`}
      role="status"
      aria-label="当前盘面日期说明"
    >
      <CalendarDays size={18} aria-hidden="true" />
      <div className="market-date-notice-copy">
        <strong>
          {title}：<time dateTime={date}>{date}</time>
        </strong>
        <span>{description}</span>
      </div>
      {(fixed || historical || unavailable) && (
        <button className="btn" onClick={onReset} disabled={intraday && !today}>
          {intraday ? "回到今天" : "回到最新"}
        </button>
      )}
    </div>
  );
}
