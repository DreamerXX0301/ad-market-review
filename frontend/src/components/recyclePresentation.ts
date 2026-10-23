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

/** Only a known parent missing from the complete strategy list proves a cascade deletion. */
export function backtestSelectionForStrategies(
  current: string,
  report: Item | null,
  history: Item[],
  strategies: Item[],
  strategiesReady: boolean,
): string {
  if (!strategiesReady) return current;
  // Archived strategies remain valid parents for immutable historical reports.
  const parents = new Set(
    strategies.filter((item) => !item.deleted).map((item) => item.strategy_id),
  );
  if (current) {
    const parent =
      (report?.backtest_id === current ? report.strategy_id : undefined) ||
      history.find((item) => item.backtest_id === current)?.strategy_id;
    // An off-page report or an unavailable detail response is not evidence of deletion.
    if (!parent || parents.has(parent)) return current;
  }
  return (
    history.find(
      (item) =>
        !item.deleted && item.backtest_id && parents.has(item.strategy_id),
    )?.backtest_id || ""
  );
}

/** A transaction dialog belongs to the report that opened it, even across retained pages. */
export function selectedBacktestTrade(
  selection: { backtestId: string; trade: Item } | null,
  selectedId: string,
  report: Item | null,
): Item | null {
  return selection?.backtestId === selectedId &&
    report?.backtest_id === selectedId
    ? selection.trade
    : null;
}
