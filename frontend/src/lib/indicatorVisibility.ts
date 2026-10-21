/** Hide optional indicator details in views without changing stored source data. */
export const HIDDEN_INDICATOR_CONDITION_TEXT = "已隐藏的指标条件（保留原配置）";

export function isHiddenIndicatorField(value: unknown): boolean {
  return (
    typeof value === "string" &&
    /^(?:display_)?kdj[\s_.-]*j(?:$|[_\s.-])/i.test(value.trim())
  );
}

/** A display copy only: never use this output to save strategies or evidence. */
export function visibleIndicatorData(value: unknown): any {
  if (typeof value === "string")
    return value
      .split("\n")
      .map((line) =>
        /\b(?:display_)?kdj[\s_.-]*j(?:\b|_)|\bJ\s*(?:值|线|[:：=])|(?:^|\s)J\s+[+-]?\d/i.test(
          line,
        )
          ? "部分指标内容已隐藏"
          : line,
      )
      .join("\n");
  if (Array.isArray(value))
    return value
      .filter((item) => !isHiddenIndicatorField(item))
      .map(visibleIndicatorData);
  if (!value || typeof value !== "object") return value;
  const row = value as Record<string, unknown>;
  if (
    [row.field, row.right_field, row.right_event_field].some(
      isHiddenIndicatorField,
    )
  )
    return { description: HIDDEN_INDICATOR_CONDITION_TEXT };
  return Object.fromEntries(
    Object.entries(row)
      .filter(([key]) => !isHiddenIndicatorField(key))
      .map(([key, item]) => [key, visibleIndicatorData(item)]),
  );
}
