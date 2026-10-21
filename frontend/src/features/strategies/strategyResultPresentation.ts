type Row = Record<string, any>;
const prefix = "workbench-strategy-selection:";
export function readStrategySelection(scope: string) {
  try {
    return localStorage.getItem(prefix + scope) || "";
  } catch {
    return "";
  }
}
export function rememberStrategySelection(scope: string, id: string) {
  try {
    localStorage.setItem(prefix + scope, id);
  } catch {
    /* Selection is optional; results remain on the server. */
  }
}
export function resolvedStrategyId(items: Row[], preferred: string) {
  return (
    items.find((item) => item.strategy_id === preferred)?.strategy_id ||
    items[0]?.strategy_id ||
    ""
  );
}
export const strategyDecisionKeys = [
  "matched",
  "unknown",
  "unmatched",
] as const;
export function strategyDecisionCounts(run: Row) {
  const rows = Array.isArray(run.items)
    ? run.items
    : Array.isArray(run.candidates)
      ? run.candidates
      : null;
  const count = (value: unknown): value is number =>
    typeof value === "number" && Number.isInteger(value) && value >= 0;
  return Object.fromEntries(
    strategyDecisionKeys.map((key) => {
      const value = run[`${key}_count`] ?? run.counts?.[key];
      return [
        key,
        count(value)
          ? value
          : rows
            ? rows.filter((row: Row) => row.decision === key).length
            : null,
      ];
    }),
  ) as Record<(typeof strategyDecisionKeys)[number], number | null>;
}
export function nextDecisionTab(index: number, key: string) {
  return key === "ArrowRight"
    ? (index + 1) % 3
    : key === "ArrowLeft"
      ? (index + 2) % 3
      : key === "Home"
        ? 0
        : key === "End"
          ? 2
          : null;
}
export function savedRunNotice(
  run: Row | null,
  strategy: Row | undefined,
  dataDate?: string,
  running = false,
) {
  if (!run) return null;
  const dateChanged = !!dataDate && run.business_date !== dataDate;
  const versionChanged =
    !!strategy && run.strategy_version !== strategy.version;
  const context = `已保存结果 · ${run.business_date} · 规则 V${run.strategy_version}`;
  const differences = [
    dateChanged ? `当前行情日 ${dataDate}` : "",
    versionChanged ? `当前规则 V${strategy?.version}` : "",
  ].filter(Boolean);
  return {
    stale: dateChanged || versionChanged,
    attention: running || dateChanged || versionChanged,
    context,
    message: running
      ? "正在更新结果，当前继续显示上次已保存结果。"
      : differences.length
        ? `${differences.join("；")}，当前保留原结果。`
        : "结果已保存，可随时返回查看。",
  };
}
