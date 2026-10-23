type Detail = Record<string, any>;
function day(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const date = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

/** Fixed daily detail quote: neither hovered candles nor W/M aggregates enter. */
export function stockHeaderQuote(
  detail: Detail | null,
  instrumentId: string,
  asOf?: string,
) {
  const identity =
    detail?.instrument?.instrument_id ||
    detail?.instrument_id ||
    detail?.thscode;
  const observed = detail?.data_date || detail?.as_of;
  const quoteDate = detail?.quote_date || detail?.date;
  const valid =
    typeof identity === "string" &&
    identity.toUpperCase() === instrumentId.toUpperCase() &&
    day(observed) &&
    (!asOf || asOf === observed) &&
    day(quoteDate) &&
    quoteDate <= observed &&
    detail?.price_basis === "none" &&
    positive(detail?.close);
  const change =
    valid &&
    typeof detail?.change_pct === "number" &&
    Number.isFinite(detail.change_pct) &&
    detail.price_continuity !== false &&
    detail.corporate_action !== true
      ? detail.change_pct
      : null;
  return {
    price: valid ? (detail!.close as number) : null,
    change,
    quoteDate: valid ? (quoteDate as string) : null,
    observed: day(observed) ? observed : day(asOf) ? asOf : null,
    stale: valid && quoteDate < observed,
    label: valid
      ? quoteDate < observed
        ? `截至 ${observed} 最近收盘 · 报价 ${quoteDate}`
        : `${quoteDate} 收盘`
      : "收盘报价待核验",
    reason: valid
      ? "已发布日行情的未复权收盘价，非实时；切换周期或悬停历史K线不改变此报价"
      : "缺少匹配证券、观察日与真实日行情报价，不使用旧股票、周/月聚合或未知值替代",
  };
}
