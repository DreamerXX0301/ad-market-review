/** Incomplete rankings are observed sample counts; they are not lower bounds. */
type Row = Record<string, any>;
const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const day = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const date = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
};
export function amountActivityView(value: Row | null | undefined) {
  const observed =
    count(value?.observed_days) && value.observed_days <= 7
      ? value.observed_days
      : null;
  const complete =
    value?.complete === true &&
    value?.state === "complete" &&
    value?.expected_days === 7 &&
    observed === 7;
  const appearances =
    count(value?.count) && value.count <= 7 && value?.state !== "unknown"
      ? value.count
      : null;
  const ranks: Row[] = Array.isArray(value?.ranks) ? value.ranks : [];
  const inWindow = (date: unknown) =>
    day(date) &&
    (!day(value?.window_start) || date >= value.window_start) &&
    (!day(value?.window_end) || date <= value.window_end);
  const details = ranks
    .filter(
      (row) =>
        row &&
        inWindow(row.date) &&
        count(row.rank) &&
        row.rank >= 1 &&
        row.rank <= 10,
    )
    .sort((a, b) => b.date.localeCompare(a.date));
  const dates = Array.isArray(value?.dates) ? value.dates.filter(inWindow) : [];
  return {
    count: appearances,
    complete,
    repeated: appearances !== null && appearances >= 2,
    label:
      appearances === null
        ? "上榜次数待核验"
        : appearances === 0
          ? complete
            ? "近7日未上榜"
            : "已观察样本未见"
          : complete
            ? `近7日上榜${appearances}次`
            : `近7日已观察${appearances}次`,
    coverage:
      observed === null
        ? "观察范围待核验"
        : complete
          ? "核验范围7/7日"
          : `已观察${observed}/7日 · 样本不完整`,
    reason:
      typeof value?.reason === "string"
        ? value.reason
        : complete
          ? "近7个市场交易日（含观察日）的已留存成交额TOP10榜单"
          : "部分日期或榜单样本不完整，次数仅说明本地已观察排名",
    details,
    dates: [...new Set(dates)].sort().reverse(),
  };
}
export function amountActivityDirections(
  summary: Row | null | undefined,
  asOf?: string,
) {
  const end = day(summary?.window_end)
    ? summary.window_end
    : day(asOf)
      ? asOf
      : null;
  if (!["available", "partial"].includes(summary?.directions?.state)) return [];
  const rows: Row[] = Array.isArray(summary?.directions?.items)
    ? summary.directions.items
    : [];
  return rows.filter(
    (row) =>
      row &&
      end &&
      day(row.observed_date) &&
      row.observed_date <= end &&
      typeof row.name === "string" &&
      row.name.trim() &&
      count(row.stock_count) &&
      row.stock_count > 0,
  );
}
