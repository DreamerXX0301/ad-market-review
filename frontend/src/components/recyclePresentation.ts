type Item = Record<string, any>;

/** Server tombstones remain authoritative after navigation and later cascade actions. */
export function visibleRecycleRows(
  rows: Item[],
  deleted: boolean,
  key: "strategy_id" | "backtest_id",
): Item[] {
  return rows.filter((row) => (row.deleted === true) === deleted);
}
export function selectionAfterRecycle(
  current: string,
  removed: string,
  rows: Item[],
  key: "strategy_id" | "backtest_id",
): string {
  if (current !== removed) return current;
  return rows.find((row) => row[key] !== removed && !row.deleted)?.[key] || "";
}
export function recycleListWithout(
  value: Item | null,
  removed: string,
  key: "strategy_id" | "backtest_id",
): Item | null {
  if (!value || !Array.isArray(value.items)) return value;
  const items = value.items.filter((row: Item) => row[key] !== removed);
  const count = value.items.length - items.length;
  return {
    ...value,
    items,
    total:
      Number.isInteger(value.total) && value.total >= count
        ? value.total - count
        : value.total,
  };
}
export function deletionReason(item: Item): string | null {
  return item.can_delete === true
    ? null
    : item.delete_blocked_reason ||
        "当前不能删除，请先结束或取消相关任务后刷新";
}
export function restorationReason(item: Item): string | null {
  return item.can_restore === true
    ? null
    : item.restore_blocked_reason || "当前不能恢复，请先恢复所属策略后刷新";
}
export function selectedBacktestReport(
  report: Item | null,
  selectedId: string,
  deletedView: boolean,
  reportError = "",
): Item | null {
  return !reportError &&
    !deletedView &&
    !!selectedId &&
    report?.backtest_id === selectedId &&
    report.deleted !== true
    ? report
    : null;
}
