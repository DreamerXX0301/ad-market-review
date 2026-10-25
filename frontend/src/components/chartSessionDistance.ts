import type { Row } from "../lib/api";

const validDate = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/** Display only the distance proved by this response's own market calendar. */
export function chartSessionDistance(bar: Row | undefined, data: Row | null) {
  const evidence = data?.trading_day_distance;
  const anchor = validDate(data?.as_of) ? data.as_of : null;
  const periodEnd = data?.period === "W" || data?.period === "M";
  const prefix = periodEnd ? "末根报价距行情截止日" : "距行情截止日";
  const unknown = (reason: string) => ({
    days: null as number | null,
    anchor,
    label: `${prefix} ${anchor || "待核验"} · 交易日距离待核验`,
    reason,
  });
  if (
    !anchor ||
    evidence?.anchor_date !== anchor ||
    evidence?.basis !== "market_calendar" ||
    evidence?.state !== "available"
  )
    return unknown(
      typeof evidence?.reason === "string" && evidence.reason
        ? evidence.reason
        : "本图未留存可核验的市场交易日距离，不按自然日或K线根数推算。",
    );
  const day = bar?.date;
  const days = bar?.trading_days_to_as_of;
  if (
    !validDate(day) ||
    day > anchor ||
    typeof days !== "number" ||
    !Number.isSafeInteger(days) ||
    days < 0 ||
    (day === anchor ? days !== 0 : days === 0)
  )
    return unknown(
      "所选报价日或本图市场交易日距离字段待核验，不用其它日期代替。",
    );
  return {
    days,
    anchor,
    label: `${prefix} ${anchor} · ${days}个交易日`,
    reason: `${evidence.reason || "按本图已留存市场交易日日历计算。"}${periodEnd ? `以本柱末根实际报价日 ${day} 为起点，不将周/月柱数折算为交易日；原形成中及缺报价状态保持。` : ""}观察日为本图数据截止日期，不是电脑当天。`,
  };
}
