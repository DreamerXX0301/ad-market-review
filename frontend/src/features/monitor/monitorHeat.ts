import type { Row } from "../../lib/api";

export type HeatPeriod = "intraday" | "30d" | "ytd";
export type HeatKind = "industry" | "concept";
export type HeatGroup = {
  topic_id: string;
  name: string;
  kind: HeatKind;
  source_kinds: string[];
  stock_count: number;
  valid_count: number;
  unknown_count: number;
  up_count: number | null;
  up_ratio_pct: number | null;
  strong_count: number | null;
  median_pct: number | null;
  mean_pct: number | null;
  alert_stock_count: number;
  leaders: Row[];
  stocks: Row[];
};
export type MonitorHeat = {
  groups: HeatGroup[];
  allGroups: HeatGroup[];
  stock_count: number;
  valid_stock_count: number;
  unclassified_count: number;
  alert_scope: "complete" | "loaded_events";
};
export type MonitorHeatOptions = {
  topicDate?: string;
  tradeDate?: string;
  period: HeatPeriod;
  kind: HeatKind;
  minSamples: 1 | 3 | 5;
};

const fields: Record<HeatPeriod, string> = {
  intraday: "change_pct",
  "30d": "change_30d_pct",
  ytd: "return_ytd_pct",
};
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const text = (value: unknown) =>
  typeof value === "string" ? value.trim() : "";
const compareText = (left: string, right: string) =>
  left < right ? -1 : left > right ? 1 : 0;

function day(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const stamp = Date.parse(`${value}T00:00:00Z`);
  return (
    Number.isFinite(stamp) &&
    new Date(stamp).toISOString().slice(0, 10) === value
  );
}

function valueAt(row: Row, options: MonitorHeatOptions): number | null {
  const value = row[fields[options.period]];
  if (!finite(value)) return null;
  if (options.period !== "intraday")
    return day(options.topicDate) && row.metrics_date === options.topicDate
      ? value
      : null;
  // The overview already scopes samples to its current trade date. An explicit
  // conflicting per-row day must not override that scope; levels/state do not
  // invalidate an otherwise known numeric quote.
  if (
    day(options.tradeDate) &&
    row.trade_date != null &&
    row.trade_date !== options.tradeDate
  )
    return null;
  return value;
}

function recognizedKind(value: unknown): HeatKind | null {
  if (value === "industry") return "industry";
  // Current membership contracts distinguish provider concepts and local
  // manually maintained research topics. Unknown kinds are not concepts.
  if (value === "concept" || value === "manual") return "concept";
  return null;
}

function canonicalText(rows: Row[], key: string, fallback: string): string {
  return (
    rows
      .map((row) => text(row[key]))
      .filter(Boolean)
      .sort(compareText)[0] || fallback
  );
}

function compareStocks(left: Row, right: Row): number {
  const a = left.heat_change_pct,
    b = right.heat_change_pct;
  if (!finite(a) || !finite(b)) {
    if (finite(a)) return -1;
    if (finite(b)) return 1;
  } else if (a !== b) return a > b ? -1 : 1;
  return compareText(left.instrument_id, right.instrument_id);
}

function compareGroups(left: HeatGroup, right: HeatGroup): number {
  for (const key of ["median_pct", "up_ratio_pct", "valid_count"] as const) {
    const a = left[key],
      b = right[key];
    if (a == null || b == null) {
      if (a != null) return -1;
      if (b != null) return 1;
    } else if (a !== b) return a > b ? -1 : 1;
  }
  return compareText(left.topic_id, right.topic_id);
}

/** Pool-relative breadth; percentages remain in the API's percent units. */
export function buildMonitorHeat(
  stocks: Row[],
  events: Row[],
  options: MonitorHeatOptions,
): MonitorHeat {
  if (!Object.hasOwn(fields, options.period))
    throw new RangeError("池内热点周期无效");
  if (!["industry", "concept"].includes(options.kind))
    throw new RangeError("池内热点分类无效");
  if (![1, 3, 5].includes(options.minSamples))
    throw new RangeError("池内热点最少有效样本只能为1、3或5");

  const byStock = new Map<string, Row[]>();
  for (const row of stocks) {
    const id = text(row?.instrument_id).toUpperCase();
    if (!id) continue;
    const duplicates = byStock.get(id);
    if (duplicates) duplicates.push(row);
    else byStock.set(id, [row]);
  }
  const alertIds = new Set(
    events
      .filter(
        (event) =>
          day(options.tradeDate) && event?.trade_date === options.tradeDate,
      )
      .map((event) => text(event.instrument_id).toUpperCase())
      .filter(Boolean),
  );
  const grouped = new Map<
    string,
    { names: Set<string>; kinds: Set<string>; rows: Row[]; alerts: Set<string> }
  >();
  let validStocks = 0,
    unclassified = 0,
    alertScope: MonitorHeat["alert_scope"] = "complete";
  for (const [id, rows] of byStock) {
    const values = rows.map((row) => valueAt(row, options));
    // Conflicting duplicate source rows stay unknown, regardless of order.
    const value = values.every((item) => item != null && item === values[0])
      ? values[0]
      : null;
    if (value != null) validStocks += 1;
    const copy = {
      ...rows[0],
      instrument_id: id,
      name: canonicalText(rows, "name", id),
      code: canonicalText(rows, "code", id.split(".")[0]),
      [fields[options.period]]: value,
      heat_change_pct: value,
    };
    const counts = rows.map((row) => row.today_alert_count);
    const exactCount = counts.every(
      (count) =>
        Number.isSafeInteger(count) && count >= 0 && count === counts[0],
    );
    if (!exactCount) alertScope = "loaded_events";
    const alerted = exactCount ? counts[0] > 0 : alertIds.has(id);
    const topics = new Map<
      string,
      { names: Set<string>; kinds: Set<string> }
    >();
    for (const row of rows) {
      if (
        !day(options.topicDate) ||
        row.topic_date !== options.topicDate ||
        !Array.isArray(row.current_topics)
      )
        continue;
      for (const topic of row.current_topics) {
        const topicId = text(topic?.topic_id),
          name = text(topic?.name);
        if (!topicId || !name || recognizedKind(topic.kind) !== options.kind)
          continue;
        const current = topics.get(topicId) || {
          names: new Set<string>(),
          kinds: new Set<string>(),
        };
        current.names.add(name);
        current.kinds.add(topic.kind);
        topics.set(topicId, current);
      }
    }
    if (!topics.size) unclassified += 1;
    for (const [topicId, topic] of topics) {
      const group = grouped.get(topicId) || {
        names: new Set<string>(),
        kinds: new Set<string>(),
        rows: [],
        alerts: new Set<string>(),
      };
      topic.names.forEach((name) => group.names.add(name));
      topic.kinds.forEach((kind) => group.kinds.add(kind));
      group.rows.push(copy);
      if (alerted) group.alerts.add(id);
      grouped.set(topicId, group);
    }
  }
  const allGroups: HeatGroup[] = [...grouped]
    .map(([topicId, input]) => {
      const ordered = [...input.rows].sort(compareStocks);
      const values = ordered
        .map((row) => row.heat_change_pct)
        .filter(finite)
        .sort((a, b) => a - b);
      const valid = values.length,
        middle = Math.floor(valid / 2);
      const up = valid ? values.filter((value) => value > 0).length : null;
      const mean = valid
        ? values.reduce((sum, value) => sum + value / valid, 0)
        : null;
      return {
        topic_id: topicId,
        name: [...input.names].sort(compareText)[0],
        kind: options.kind,
        source_kinds: [...input.kinds].sort(compareText),
        stock_count: ordered.length,
        valid_count: valid,
        unknown_count: ordered.length - valid,
        up_count: up,
        up_ratio_pct: valid ? (up! / valid) * 100 : null,
        strong_count: valid
          ? values.filter((value) => value >= 3).length
          : null,
        median_pct: valid
          ? valid % 2
            ? values[middle]
            : values[middle - 1] / 2 + values[middle] / 2
          : null,
        mean_pct: finite(mean) ? mean : null,
        alert_stock_count: input.alerts.size,
        leaders: ordered
          .filter((row) => finite(row.heat_change_pct))
          .slice(0, 3),
        stocks: ordered,
      };
    })
    .sort(compareGroups);
  return {
    groups: allGroups.filter(
      (group) => group.valid_count >= options.minSamples,
    ),
    allGroups,
    stock_count: byStock.size,
    valid_stock_count: validStocks,
    unclassified_count: unclassified,
    alert_scope: alertScope,
  };
}
