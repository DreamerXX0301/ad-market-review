type Item = Record<string, any>;

export type PurgeTarget = {
  kind: "strategy" | "backtest";
  id: string;
  name: string;
  purgeId?: string;
};

const scopeLabels: [string, string][] = [
  ["strategies", "策略"],
  ["strategy_versions", "策略版本"],
  ["backtests", "回测记录"],
  ["tracking_rounds", "跟踪轮次"],
  ["tracking_events", "跟踪事件"],
  ["selection_runs", "选股运行"],
  ["selection_candidates", "选股结果"],
  ["tracking_policies", "跟踪规则"],
  ["policy_applications", "规则应用"],
  ["tracking_ignores", "跟踪忽略记录"],
  ["execution_settings", "执行设置"],
];

/** Unknown totals cannot be presented as zero, especially in a destructive preview. */
export function purgeCount(value: unknown): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value.toLocaleString("zh-CN")
    : "—";
}

export function purgeBytes(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    return "—";
  if (value < 1024) return `${value.toLocaleString("zh-CN")} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

export function purgeScopeRows(preview: Item): [string, string][] {
  return scopeLabels
    .filter(
      ([key]) => preview.target_kind === "strategy" || key === "backtests",
    )
    .map(([key, label]) => [label, purgeCount(preview.counts?.[key])]);
}

export function hasWatchlistProtection(preview: Item): boolean {
  return (
    (typeof preview.protected_watchlist_count === "number" &&
      preview.protected_watchlist_count > 0) ||
    (Array.isArray(preview.protected_watchlist) &&
      preview.protected_watchlist.length > 0)
  );
}

/** A true capability, current scope and exact confirmation are all required. */
export function canConfirmPurge(
  preview: Item | null,
  target: PurgeTarget,
  typed: string,
  now: number,
): boolean {
  return !!(
    preview &&
    preview.target_kind === target.kind &&
    preview.target_id === target.id &&
    preview.can_purge === true &&
    !hasWatchlistProtection(preview) &&
    typeof preview.confirmation_token === "string" &&
    preview.confirmation_token.length > 0 &&
    typeof preview.confirmation_text === "string" &&
    preview.confirmation_text.length > 0 &&
    typed === preview.confirmation_text &&
    typeof preview.expires_at_ms === "number" &&
    preview.expires_at_ms > now
  );
}

export function purgePhase(status: unknown): string {
  return (
    {
      queued: "等待清理",
      cleaning: "正在清理",
      completed: "永久删除完成",
      failed: "清理未完成",
    }[String(status)] || "状态待核验"
  );
}

export function purgeIsWorking(status: unknown): boolean {
  return status === "queued" || status === "cleaning";
}

export function purgeBlocksRestore(purge: Item | null | undefined): boolean {
  return (
    !!purge && (purgeIsWorking(purge.status) || purge.business_deleted === true)
  );
}

/** Only the matching server job may update this dialog after polling. */
export function matchingPurgeStatus(
  value: Item | null,
  purgeId: string,
): Item | null {
  return value?.purge_id === purgeId ? value : null;
}

export function recyclePageOffset(
  total: unknown,
  offset: number,
  size = 25,
): number {
  if (typeof total !== "number" || !Number.isSafeInteger(total) || total < 0)
    return offset;
  return offset > 0 && total <= offset
    ? Math.max(0, Math.floor(Math.max(0, total - 1) / size) * size)
    : offset;
}
