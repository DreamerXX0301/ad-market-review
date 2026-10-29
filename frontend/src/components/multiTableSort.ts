import type { SortValue, TableSort } from "./tableSort";

const collator = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base",
});

function comparable(value: unknown): SortValue {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  return typeof value === "string" || typeof value === "boolean" ? value : null;
}

/** Keep only distinct, currently available columns. Never mutate saved defaults. */
export function availableSortLevels(
  levels: TableSort[],
  keys: string[],
): TableSort[] {
  const allowed = new Set(keys),
    seen = new Set<string>();
  return levels.filter((level) => {
    if (!allowed.has(level.key) || seen.has(level.key)) return false;
    seen.add(level.key);
    return true;
  });
}

export function moveSortLevel(
  levels: TableSort[],
  index: number,
  offset: -1 | 1,
): TableSort[] {
  const next = index + offset;
  if (index < 0 || index >= levels.length || next < 0 || next >= levels.length)
    return levels;
  const moved = [...levels];
  [moved[index], moved[next]] = [moved[next], moved[index]];
  return moved;
}

/** Compare unrounded values once per level, then a stable identity, before paging. */
export function sortTableRowsByLevels<T>(
  rows: T[],
  levels: TableSort[],
  valueFor: (row: T, key: string) => unknown,
  identityFor: (row: T) => string,
): T[] {
  if (!levels.length) return rows;
  return rows
    .map((row, index) => ({
      row,
      index,
      values: levels.map((level) => comparable(valueFor(row, level.key))),
      identity: identityFor(row),
    }))
    .sort((left, right) => {
      for (let index = 0; index < levels.length; index++) {
        const a = left.values[index],
          b = right.values[index];
        // Unknown is last within this priority in both directions.
        if (a == null && b == null) continue;
        if (a == null) return 1;
        if (b == null) return -1;
        const difference =
          typeof a === "number" && typeof b === "number"
            ? a - b
            : typeof a === "boolean" && typeof b === "boolean"
              ? Number(a) - Number(b)
              : collator.compare(String(a), String(b));
        if (difference)
          return difference * (levels[index].order === "asc" ? 1 : -1);
      }
      return (
        collator.compare(left.identity, right.identity) ||
        left.index - right.index
      );
    })
    .map(({ row }) => row);
}
