/** Local presentation only: dated source rows stay separate by disclosure range. */
export type DragonTigerBoard = "all" | "org" | "hot_money";
export type DragonTigerPeriod = 1 | 3;
export type DragonTigerSortKey =
  "name" | "change" | "net" | "buy" | "sell" | "org";
export type DragonTigerSort = {
  key: DragonTigerSortKey;
  order: "asc" | "desc";
};
export type DragonTigerSearchMatch = {
  instrument_id: string;
  range_days: DragonTigerPeriod;
  label_name: string | null;
  stock_match: { match_label: string; is_suggestion: boolean } | null;
  seat_matches: { side: "buy" | "sell"; name: string; record_id: string }[];
};
type SourceRow = Record<string, any>;
export type DragonTigerRow = SourceRow & {
  key: string;
  instrument_id: string | null;
  code: string | null;
  name: string | null;
  range_days: DragonTigerPeriod | null;
  label_name: string | null;
  source_concepts: { name: string }[];
  change_pct: number | null;
  net_value: number | null;
  buy_value: number | null;
  sell_value: number | null;
  org_net_value: number | null;
  label_net_value: number | null;
  search_match?: DragonTigerSearchMatch;
};
const records = (value: unknown): SourceRow[] =>
  Array.isArray(value)
    ? value.filter((row) => row && typeof row === "object")
    : [];
const text = (value: unknown) =>
  typeof value === "string" && value.trim() ? value.trim() : null;
export const dragonTigerNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** A retained response can be usable while its upstream statistics stay unverified. */
export function dragonTigerBoardAvailable(
  payload: unknown,
  board: DragonTigerBoard,
) {
  const data =
    payload && typeof payload === "object" ? (payload as SourceRow) : {};
  const source = data.boards?.[board];
  if (data.availability?.available === false || !Array.isArray(source?.items))
    return false;
  return (
    source.state === "ready" ||
    (source.state === "partial" && data.availability?.available === true)
  );
}

/** Counts here cover the returned source labels, including unmatched source codes. */
export function dragonTigerHotStatistics(payload: unknown) {
  const data =
    payload && typeof payload === "object" ? (payload as SourceRow) : {};
  const source = data.boards?.hot_money ?? {};
  const count = (value: unknown) => {
    const number = dragonTigerNumber(value);
    return number != null && Number.isInteger(number) && number >= 0
      ? number
      : null;
  };
  const available = dragonTigerBoardAvailable(data, "hot_money");
  const groups = records(source.items);
  const rows = groups.flatMap((group) => records(group.rows));
  const codes = new Set(
    rows.flatMap((row) => (text(row.code) ? [text(row.code)!] : [])),
  );
  const ranges = new Set(
    rows.flatMap((row) => {
      const range = count(row.source_range_days ?? row.range_days);
      return text(row.code) && range != null && range > 0
        ? [JSON.stringify([text(row.code), range])]
        : [];
    }),
  );
  return {
    sourceCount: count(source.source_count),
    sourceStockCount: count(source.source_stock_count),
    labelCount: available
      ? (count(source.observed_label_count) ?? groups.length)
      : null,
    rowCount: available
      ? (count(source.observed_label_row_count) ?? rows.length)
      : null,
    stockCount: available
      ? (count(source.observed_stock_count) ?? codes.size)
      : null,
    stockRangeCount: available
      ? (count(source.observed_stock_range_count) ?? ranges.size)
      : null,
  };
}

export function dragonTigerRows(
  payload: unknown,
  board: DragonTigerBoard,
): DragonTigerRow[] {
  const data =
    payload && typeof payload === "object" ? (payload as SourceRow) : {};
  const source = records(data.boards?.[board]?.items);
  const items: SourceRow[] =
    board === "hot_money"
      ? source.flatMap((group) =>
          records(group.rows).map((row) => ({
            ...row,
            label_name: text(group.name),
          })),
        )
      : source.map((row) => ({ ...row, label_name: null }));
  const occurrences = new Map<string, number>();
  return items.map((row) => {
    const id = text(row.instrument_id)?.toUpperCase() ?? null;
    const instrumentId = id && /^\d{6}\.(SH|SZ|BJ)$/.test(id) ? id : null;
    const range =
      row.range_days === 1 || row.range_days === 3 ? row.range_days : null;
    const baseKey = JSON.stringify([
      instrumentId,
      range,
      row.label_name,
      text(row.code),
      text(row.name),
    ]);
    const occurrence = occurrences.get(baseKey) ?? 0;
    occurrences.set(baseKey, occurrence + 1);
    return {
      ...row,
      key: `${baseKey}|${occurrence}`,
      instrument_id: instrumentId,
      code: text(row.code),
      name: text(row.name),
      range_days: range,
      label_name: text(row.label_name),
      source_concepts: records(row.source_concepts).flatMap((item) =>
        text(item.name) ? [{ name: text(item.name)! }] : [],
      ),
      change_pct: dragonTigerNumber(row.change_pct),
      net_value: dragonTigerNumber(row.net_value),
      buy_value: dragonTigerNumber(row.buy_value),
      sell_value: dragonTigerNumber(row.sell_value),
      org_net_value: dragonTigerNumber(row.org_net_value),
      label_net_value: dragonTigerNumber(row.label_net_value),
    };
  });
}

export function filterDragonTigerRows(
  rows: DragonTigerRow[],
  period: DragonTigerPeriod,
  label: string | null,
  matches?: DragonTigerSearchMatch[],
) {
  const scoped = rows.filter(
    (row) =>
      row.instrument_id &&
      row.range_days === period &&
      (!label || row.label_name === label),
  );
  if (!matches) return scoped;
  const key = (row: DragonTigerRow | DragonTigerSearchMatch) =>
    JSON.stringify([row.instrument_id, row.range_days, row.label_name]);
  const byRow = new Map(matches.map((match) => [key(match), match]));
  return scoped.flatMap((row) => {
    const search_match = byRow.get(key(row));
    return search_match ? [{ ...row, search_match }] : [];
  });
}

export function dragonTigerNetField(board: DragonTigerBoard) {
  return board === "hot_money"
    ? "label_net_value"
    : board === "org"
      ? "org_net_value"
      : "net_value";
}

export function sortDragonTigerRows(
  rows: DragonTigerRow[],
  sort: DragonTigerSort,
  board: DragonTigerBoard,
): DragonTigerRow[] {
  const field =
    sort.key === "net"
      ? dragonTigerNetField(board)
      : {
          name: "name",
          change: "change_pct",
          buy: "buy_value",
          sell: "sell_value",
          org: "org_net_value",
        }[sort.key];
  const collator = new Intl.Collator("zh-CN", {
    numeric: true,
    sensitivity: "base",
  });
  return rows
    .map((row, index) => ({ row, index, value: row[field] }))
    .sort((a, b) => {
      const av =
        sort.key === "name" ? text(a.value) : dragonTigerNumber(a.value);
      const bv =
        sort.key === "name" ? text(b.value) : dragonTigerNumber(b.value);
      if (av == null) return bv == null ? a.index - b.index : 1;
      if (bv == null) return -1;
      const compared =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : collator.compare(String(av), String(bv));
      return compared * (sort.order === "asc" ? 1 : -1) || a.index - b.index;
    })
    .map((item) => item.row);
}

export function uniqueDragonTigerStocks(rows: DragonTigerRow[]) {
  const unique = new Map<string, DragonTigerRow>();
  for (const row of rows) {
    if (!row.instrument_id) continue;
    const key = `${row.instrument_id}|${row.range_days}`;
    const previous = unique.get(key);
    if (!previous) unique.set(key, { ...row });
    else
      for (const field of [
        "buy_value",
        "sell_value",
        "net_value",
        "org_net_value",
      ] as const) {
        if (previous[field] !== row[field]) previous[field] = null;
      }
  }
  return [...unique.values()];
}

function total(rows: DragonTigerRow[], field: string, known: boolean) {
  if (!known || rows.some((row) => dragonTigerNumber(row[field]) == null))
    return null;
  return rows.reduce((sum, row) => sum + row[field], 0);
}

export function dragonTigerSummary(
  rows: DragonTigerRow[],
  board: DragonTigerBoard,
  label: string | null,
  sourceKnown = true,
) {
  const stocks = uniqueDragonTigerStocks(rows);
  const stockNet = board === "hot_money" && !label;
  return {
    count: sourceKnown || rows.length ? stocks.length : null,
    net: total(
      stockNet ? stocks : rows,
      stockNet ? "net_value" : dragonTigerNetField(board),
      sourceKnown || rows.length > 0,
    ),
    netLabel: stockNet
      ? "股票龙虎榜净额（去重）"
      : board === "hot_money"
        ? "该标签净买额"
        : board === "org"
          ? "机构净买额"
          : "龙虎榜净买额",
    buy: total(stocks, "buy_value", sourceKnown || rows.length > 0),
    sell: total(stocks, "sell_value", sourceKnown || rows.length > 0),
  };
}

export function dragonTigerLabels(
  rows: DragonTigerRow[],
  period: DragonTigerPeriod,
) {
  const groups = new Map<string, DragonTigerRow[]>();
  for (const row of rows) {
    if (!row.instrument_id || row.range_days !== period || !row.label_name)
      continue;
    const group = groups.get(row.label_name) ?? [];
    group.push(row);
    groups.set(row.label_name, group);
  }
  return [...groups]
    .map(([name, items]) => ({
      name,
      count: uniqueDragonTigerStocks(items).length,
    }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-CN"));
}

export function dragonTigerDisclosure(
  recordsValue: unknown,
  recordId: string | null,
) {
  const items = records(recordsValue);
  return (
    items.find((row) => String(row.record_id) === recordId) ?? items[0] ?? null
  );
}

export function dragonTigerJobId(value: unknown): string | null {
  const data = value && typeof value === "object" ? (value as SourceRow) : {};
  return (
    text(data.task_id) ||
    text(data.job_id) ||
    text(data.id) ||
    text(data.task?.task_id) ||
    text(data.task?.job_id)
  );
}
