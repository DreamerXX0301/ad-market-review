type ClipboardWriter = { writeText: (value: string) => Promise<void> };
const completeCode = /^\d{6}\.(SH|SZ|BJ)$/;
const plainCode = /^\d{6}$/;

export function stockCodeCopyValue(
  code?: unknown,
  instrumentId?: unknown,
  variant: "short" | "full" = "short",
) {
  const visible = typeof code === "string" ? code.trim().toUpperCase() : "";
  const identifier =
    typeof instrumentId === "string" ? instrumentId.trim().toUpperCase() : "";
  const full = completeCode.test(identifier)
    ? identifier
    : completeCode.test(visible)
      ? visible
      : null;
  const short = plainCode.test(visible)
    ? visible
    : completeCode.test(visible)
      ? visible.slice(0, 6)
      : full?.slice(0, 6) || null;
  const value = variant === "full" ? full : short;
  return {
    display: value || visible || identifier || "—",
    value,
    full,
    variant,
  };
}

/** Only a resolved clipboard write counts as success; never pretend a fallback copied. */
export async function copyStockCode(
  value: string | null,
  writer?: ClipboardWriter | null,
) {
  if (!value || !(plainCode.test(value) || completeCode.test(value)))
    return "invalid";
  try {
    const clipboard =
      writer === undefined
        ? typeof navigator === "undefined"
          ? null
          : navigator.clipboard
        : writer;
    if (!clipboard || typeof clipboard.writeText !== "function")
      return "unavailable";
    await clipboard.writeText(value);
    return "copied";
  } catch {
    return "failed";
  }
}
