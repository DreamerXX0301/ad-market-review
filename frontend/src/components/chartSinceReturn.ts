import type { Row } from "../lib/api";

const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const date = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const time = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(time.getTime()) && time.toISOString().slice(0, 10) === value
  );
};

/** A same-basis endpoint comparison within this chart's frozen observation date. */
export function chartSinceReturn(bar: Row | undefined, data: Row | null) {
  const end = date(data?.as_of) ? data.as_of : null;
  const start = date(bar?.date) ? bar.date : null;
  const result = { value: null as number | null, start, end, reason: "" };
  if (!start || !end || start > end || !positive(bar?.close))
    return { ...result, reason: "所选K线收盘或观察日期待核验。" };
  const bars: Row[] = Array.isArray(data?.bars) ? data.bars : [];
  const endpoints = bars.filter((item) => item.date === end);
  if (endpoints.length !== 1 || !positive(endpoints[0].close))
    return {
      ...result,
      reason: "观察日没有唯一有效收盘价，不用较早报价代替。",
    };
  if (bars.filter((item) => item.date === start).length !== 1)
    return { ...result, reason: "所选K线日期不唯一，端点待核验。" };
  const forward = data?.price_basis === "forward_event_affine";
  const raw = data?.price_basis === "none";
  if (!raw && !forward)
    return { ...result, reason: "当前图表价格口径尚未核验。" };
  if (forward && data?.adjustment?.usable !== true)
    return { ...result, reason: "本地前复权依据未通过，保持未知。" };
  if (forward && data?.adjustment?.anchor !== end)
    return {
      ...result,
      reason: "前复权锚点与观察日不一致，不能混用其他时点价格。",
    };
  if (
    !["stock", "index", "industry", "concept", "sector"].includes(
      data?.instrument?.kind,
    ) &&
    data?.price_unit !== "点"
  )
    return {
      ...result,
      reason: "标的类型待核验，不能假定价格具有相同公司行动语义。",
    };
  if (raw && data?.instrument?.kind === "stock" && start < end) {
    const span = bars.filter((item) => item.date > start && item.date <= end);
    if (span.some((item) => item.corporate_action_coverage_known !== true))
      return {
        ...result,
        reason: "区间公司行动覆盖未核验，不能比较原始价格。",
      };
    const events = data?.adjustment?.events;
    const quarantined = data?.adjustment?.quarantined;
    if (
      Array.isArray(quarantined) &&
      quarantined.some(
        (event) =>
          !date(event?.date) || (event.date > start && event.date <= end),
      )
    )
      return {
        ...result,
        reason: "区间存在未核验公司行动或除权日期未知，原始价格保持不可比。",
      };
    if (
      span.some((item) => item.corporate_action === true) ||
      (Array.isArray(events) &&
        events.some((event) => event.date > start && event.date <= end))
    )
      return {
        ...result,
        reason:
          "区间包含公司行动，原始价格不可直接比较；可切换已核验的本地前复权。",
      };
  }
  const value = (endpoints[0].close / bar.close - 1) * 100;
  return {
    ...result,
    value: Number.isFinite(value) ? value : null,
    reason: `${start}收盘至${end}收盘，按本图${forward ? "本地前复权" : "原始"}价格计算；仅为端点价格变化，不含持仓分红或成交费用。${bar.week_closed === false || bar.month_closed === false ? "所选周期仍在形成中。" : ""}`,
  };
}
