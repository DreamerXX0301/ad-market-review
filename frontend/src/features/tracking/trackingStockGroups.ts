import type { Row } from "../../lib/api";

const text = (value: unknown): string =>
  typeof value === "string" ? value.trim() : "";
const day = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.valueOf()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

function commonQuote(rounds: Row[], businessDate?: string) {
  if (
    !day(businessDate) ||
    rounds.some((row) => row.valuation_date !== businessDate)
  )
    return {
      valuation_date: null,
      latest_price: null,
      today_change_pct: null,
      quote_reason:
        "当前业务日或轮次行情日期未能一致核验，不合并不同日期的报价",
    };

  const reasons: string[] = [];
  const common = (field: string, positive: boolean, label: string) => {
    const values = rounds.map((row) => row[field]);
    if (values.some((value) => !finite(value) || (positive && value <= 0))) {
      reasons.push(`部分轮次缺少有效${label}`);
      return null;
    }
    if (values.some((value) => value !== values[0])) {
      reasons.push(`各轮次${label}不一致，保持待核验`);
      return null;
    }
    return values[0] as number;
  };
  const latest = common("latest_price", true, "收盘价");
  const change = common("today_change_pct", false, "当日涨幅");
  return {
    valuation_date: businessDate,
    latest_price: latest,
    today_change_pct: change,
    quote_reason: reasons.join("；"),
  };
}

/** Group only the already-matching rounds; each child keeps its own evidence. */
export function groupTrackingStocks(rows: Row[], businessDate?: string): Row[] {
  const grouped = new Map<string, Row[]>();
  for (const row of rows) {
    const instrumentId = text(row?.instrument_id);
    // An absent identity cannot be merged with another unknown or valid stock.
    if (!instrumentId) continue;
    const group = grouped.get(instrumentId);
    if (group) group.push(row);
    else grouped.set(instrumentId, [row]);
  }
  return [...grouped].map(([instrumentId, rounds]) => {
    const strategies = new Map<string, string>();
    const triggeredRounds = new Set<string | Row>();
    for (const row of rounds) {
      const strategyId = text(row.strategy_id);
      const strategyName = text(row.strategy_name);
      if (
        strategyId &&
        (!strategies.has(strategyId) ||
          (strategies.get(strategyId) === strategyId && strategyName))
      )
        strategies.set(strategyId, strategyName || strategyId);
      if (
        day(businessDate) &&
        Array.isArray(row.alerts) &&
        row.alerts.some(
          (alert: Row) =>
            alert &&
            alert.business_date === businessDate &&
            (alert.event_type == null || alert.event_type === "alert") &&
            (alert.round_id == null || alert.round_id === row.round_id),
        )
      )
        triggeredRounds.add(text(row.round_id) || row);
    }
    const dates = rounds.map((row) => row.entry_business_date);
    const completeDates = dates.every(day);
    const sortedDates = completeDates ? [...dates].sort() : [];
    return {
      instrument_id: instrumentId,
      name: rounds.map((row) => text(row.name)).find(Boolean) || instrumentId,
      code:
        rounds.map((row) => text(row.code)).find(Boolean) ||
        instrumentId.split(".")[0],
      rounds,
      strategy_count: strategies.size,
      round_count: rounds.length,
      strategy_names: [...strategies.values()],
      triggered_round_count: triggeredRounds.size,
      earliest_entry_date: sortedDates[0] ?? null,
      last_entry_date: sortedDates.at(-1) ?? null,
      ...commonQuote(rounds, businessDate),
    };
  });
}
