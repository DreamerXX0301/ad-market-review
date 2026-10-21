export interface DataNote {
  key: string;
  message: string;
  blocking: boolean;
}

function flatten(items: unknown): unknown[] {
  return Array.isArray(items) ? items.flatMap(flatten) : [items];
}

function messageOf(item: unknown): string | null {
  if (typeof item === "string") return item.trim() || null;
  if (!item || typeof item !== "object") return null;
  const value = item as Record<string, unknown>;
  for (const field of ["message", "reason", "text", "detail"]) {
    if (typeof value[field] === "string" && value[field].trim())
      return value[field].trim();
  }
  return JSON.stringify(value);
}

/** Source notes may be folded; explicit errors/blockers remain outside it. */
export function dataNotesModel(items: unknown): {
  notes: DataNote[];
  issues: DataNote[];
} {
  const unique = new Map<string, DataNote>();
  for (const item of flatten(items)) {
    const message = messageOf(item);
    if (!message) continue;
    const key = message
      .normalize("NFKC")
      .replace(/\s+/gu, " ")
      .replace(/[。；;.!！\s]+$/gu, "");
    const value = item && typeof item === "object" ? item : {};
    const severity = String(
      (value as Record<string, unknown>).severity ||
        (value as Record<string, unknown>).level ||
        "",
    ).toLowerCase();
    const blocking =
      (value as Record<string, unknown>).blocking === true ||
      ["error", "critical", "fatal", "blocking"].includes(severity);
    const prior = unique.get(key);
    unique.set(key, {
      key,
      message: prior?.message || message,
      blocking: blocking || prior?.blocking === true,
    });
  }
  const all = [...unique.values()];
  return {
    notes: all.filter((item) => !item.blocking),
    issues: all.filter((item) => item.blocking),
  };
}

/** Base storage and the selected chart basis are separate, for either source. */
export function chartDataNotes(...groups: unknown[]): unknown[] {
  return groups.flatMap(flatten).map((item) => {
    if (typeof item === "string" && item.trim().startsWith("未复权原始价格"))
      return "基础行情保存未复权原始价格；本地前复权仅切换当前图表，策略、跟踪和回测口径独立。";
    return item;
  });
}
