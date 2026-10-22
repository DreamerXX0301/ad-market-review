import type { Row } from "../lib/api";
import { projectEventBaseline } from "./chartDisplay";

export type HistoricalChartEvent = {
  key: string;
  date: string;
  rawClose: number;
  rawPrice: number;
  priceBasis: "none";
  priceField: "open" | "high" | "low" | "close";
  label: string;
  match: string;
  observed: string;
};
export const eventPriceNames = {
  open: "开盘",
  high: "最高",
  low: "最低",
  close: "收盘",
};
function date(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
function name(where: Row | undefined): string {
  const leaves: Row[] = [];
  function walk(node: any) {
    if (!node || typeof node !== "object") return;
    if (node.field) leaves.push(node);
    else for (const child of node.all || node.any || []) walk(child);
  }
  walk(where);
  if (leaves.length !== 1) return "历史条件事件";
  const leaf = leaves[0];
  if (leaf.field === "volume_ratio")
    return [">", ">="].includes(leaf.op) && Number(leaf.value) > 1
      ? "倍量事件"
      : "量能事件";
  if (leaf.field === "limit_up" && leaf.op === "==" && leaf.value === true)
    return "涨停事件";
  if (leaf.field === "change_pct") return "涨幅事件";
  if (leaf.field === "macd_cross" && leaf.op === "==" && leaf.value === true)
    return "MACD金叉事件";
  if (leaf.field === "kd_cross" && leaf.op === "==" && leaf.value === true)
    return "KDJ金叉事件";
  return "历史条件事件";
}

/** Use the engine's frozen selected_event; never pick a different event in the chart. */
export function historicalChartEvents(value: unknown): HistoricalChartEvent[] {
  const result: HistoricalChartEvent[] = [];
  function walk(node: any, path: string) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach((child, index) => walk(child, `${path}.${index}`));
      return;
    }
    if (node.type === "history_event") {
      const selected = node.selected_event,
        evidence = node.evidence,
        spec = node.history_event;
      if (
        node.event_checks?.result !== true ||
        !date(selected?.date) ||
        !date(evidence?.as_of) ||
        selected.date >= evidence.as_of ||
        selected.price_basis !== "none" ||
        evidence.price_basis !== "none" ||
        !positive(selected.close)
      )
        return;
      const referenced = new Set<string>();
      for (const field of Array.isArray(node.reference_fields)
        ? node.reference_fields
        : [])
        if (Object.hasOwn(eventPriceNames, field)) referenced.add(field);
      function references(tree: any) {
        if (!tree || typeof tree !== "object") return;
        if (Object.hasOwn(eventPriceNames, tree.right_event_field))
          referenced.add(tree.right_event_field);
        for (const child of tree.all || tree.any || []) references(child);
      }
      references(spec?.then);
      const reclaimField = spec?.price_path?.reclaim?.event_field;
      if (Object.hasOwn(eventPriceNames, reclaimField))
        referenced.add(reclaimField);
      if (!referenced.size) referenced.add("close");
      for (const field of ["close", "high", "low", "open"] as const) {
        if (!referenced.has(field) || !positive(selected[field])) continue;
        result.push({
          key: `${path}:${field}`,
          date: selected.date,
          rawClose: selected.close,
          rawPrice: selected[field],
          priceBasis: "none",
          priceField: field,
          label: name(spec?.where),
          match: spec?.match === "earliest" ? "最早一次" : "最近一次",
          observed: evidence.as_of,
        });
      }
      return; // event_checks and then are evidence, not additional historical selections.
    }
    walk(node.checks || node.all || node.any || node.conditions, path);
  }
  walk(value, "history");
  return result;
}

function periodKey(day: string, period: string) {
  if (period === "M") return day.slice(0, 7);
  if (period !== "W") return day;
  const parsed = new Date(day + "T00:00:00Z");
  parsed.setUTCDate(parsed.getUTCDate() - ((parsed.getUTCDay() + 6) % 7));
  return parsed.toISOString().slice(0, 10);
}
export function projectHistoricalChartEvent(
  event: HistoricalChartEvent,
  chart: Row | null | undefined,
) {
  const view = projectEventBaseline(event, chart, event.rawPrice);
  const closeView = projectEventBaseline(event, chart);
  const slot = (chart?.bars || []).find(
    (bar: Row) =>
      date(bar.date) &&
      periodKey(bar.date, chart?.period) ===
        periodKey(event.date, chart?.period),
  );
  const dayBar =
    chart?.period === "D"
      ? (chart.bars || []).find((bar: Row) => bar.date === event.date)
      : null;
  const dayPrice =
    chart?.price_basis === "none"
      ? dayBar?.[event.priceField]
      : dayBar?.[`raw_${event.priceField}`];
  const reason =
    date(chart?.as_of) && event.observed > chart.as_of
      ? "本图观察日早于该规则的绑定日，未显示后来选择的历史事件"
      : view.price == null
        ? view.reason
        : chart?.period === "D" &&
            event.priceField !== "close" &&
            (!positive(dayPrice) ||
              Math.abs(dayPrice - event.rawPrice) > 0.00005)
          ? "冻结事件参照价与本图事件日原始报价不一致或缺失，未显示标记"
          : !slot
            ? "事件日在本图没有可核验K线，未显示标记"
            : "";
  return {
    event,
    price: reason ? null : view.price,
    closePrice: reason ? null : closeView.price,
    slot: reason ? null : slot.date,
    reason,
    converted: view.converted,
  };
}
