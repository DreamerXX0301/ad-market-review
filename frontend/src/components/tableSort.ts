/** Sort raw values across the supplied result set, before any UI pagination. */
export type SortValue = string | number | boolean | null | undefined;
export type TableSort = { key: string; order: "asc" | "desc" };

export function isSortValue(value: unknown): value is SortValue {
  return (
    value == null || ["string", "number", "boolean"].includes(typeof value)
  );
}

function comparable(value: unknown): string | number | boolean | null {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  return typeof value === "string" || typeof value === "boolean" ? value : null;
}

export function sortTableRows<T>(
  rows: T[],
  sort: TableSort | null,
  valueFor: (row: T) => unknown,
): T[] {
  if (!sort) return rows;
  const collator = new Intl.Collator("zh-CN", {
    numeric: true,
    sensitivity: "base",
  });
  return rows
    .map((row, index) => ({ row, index, value: comparable(valueFor(row)) }))
    .sort((left, right) => {
      if (left.value == null)
        return right.value == null ? left.index - right.index : 1;
      if (right.value == null) return -1;
      const compared =
        typeof left.value === "number" && typeof right.value === "number"
          ? left.value - right.value
          : typeof left.value === "boolean" && typeof right.value === "boolean"
            ? Number(left.value) - Number(right.value)
            : collator.compare(String(left.value), String(right.value));
      return (
        compared * (sort.order === "asc" ? 1 : -1) || left.index - right.index
      );
    })
    .map(({ row }) => row);
}
