import type { Row } from "../../lib/api";

const strategyFields = [
  "period",
  "price_basis",
  "definition",
  "hot_universe",
  "trade_rules",
];
const policyFields = [
  "duration_sessions",
  "allow_entry_day_alerts",
  "allow_entry_day_exits",
  "alert_logic",
  "exit_logic",
  "alerts",
  "exits",
];

/** Copy editable configuration only. Persistence and runs belong to the source. */
export function strategyCopyDraft(strategy: Row): Row {
  const suffix = " · 副本";
  const name =
    Array.from(String(strategy.name || "选股策略").trim())
      .slice(0, 100 - Array.from(suffix).length)
      .join("") + suffix;
  const draft: Row = { name };
  for (const field of strategyFields)
    if (Object.hasOwn(strategy, field)) draft[field] = strategy[field];
  if (strategy.policy)
    draft.policy = Object.fromEntries(
      policyFields
        .filter((field) => Object.hasOwn(strategy.policy, field))
        .map((field) => [field, strategy.policy[field]]),
    );
  return structuredClone(draft);
}
