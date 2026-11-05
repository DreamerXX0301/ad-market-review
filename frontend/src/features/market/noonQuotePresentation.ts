import type { Row } from "../../lib/api";

export type NoonSortDirection = "asc" | "desc";
export type NoonSortValue = string | number | null | undefined;

/** Stable local ordering; absent values stay last in either direction. */
export function noonSortRows(
  rows: Row[],
  key: string,
  direction: NoonSortDirection,
  valueOf: (row: Row, key: string) => NoonSortValue = (row, field) =>
    row[field],
): Row[] {
  const value = (row: Row): string | number | null => {
    const candidate = valueOf(row, key);
    if (typeof candidate === "number")
      return Number.isFinite(candidate) ? candidate : null;
    if (typeof candidate !== "string" || !candidate.trim()) return null;
    const text = candidate.trim();
    if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) {
      const numeric = Number(text);
      if (Number.isFinite(numeric)) return numeric;
    }
    return text;
  };
  return rows
    .map((row, index) => ({ row, index, value: value(row) }))
    .sort((left, right) => {
      if (left.value == null || right.value == null)
        return left.value == null && right.value == null
          ? left.index - right.index
          : left.value == null
            ? 1
            : -1;
      const compare =
        typeof left.value === "number" && typeof right.value === "number"
          ? left.value - right.value
          : String(left.value).localeCompare(String(right.value), "zh-CN", {
              numeric: true,
            });
      return (
        (direction === "desc" ? -compare : compare) || left.index - right.index
      );
    })
    .map(({ row }) => row);
}

export function noonKnownHeight(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

const rows = (value: unknown): Row[] =>
  Array.isArray(value)
    ? value.filter(
        (item): item is Row =>
          !!item && typeof item === "object" && !Array.isArray(item),
      )
    : [];

/** Join only facts already returned for this immutable noon snapshot. */
export function noonQuoteContext(snapshot: Row): Record<string, Row> {
  const workbench = snapshot.workbench || {};
  const output: Record<string, Row> = {};
  for (const [symbol, evidence] of Object.entries(
    snapshot.stock_suspensions || {},
  )) {
    output[symbol] = { suspension: evidence };
  }
  const classifications = {
    ...(snapshot.membership_context?.stock_classifications || {}),
    ...(snapshot.payload?.stock_classifications || {}),
  };
  for (const [symbol, value] of Object.entries(classifications)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const classification = value as Row;
      output[symbol] = {
        ...output[symbol],
        industries: rows(classification.industries),
        topics: rows(classification.topics),
        membership_state: classification.state,
        membership_reason: classification.reason,
      };
    }
  }
  const previous = rows(workbench.previous_feedback?.items);
  const negatives = rows(workbench.negative_feedback?.cohorts).flatMap((item) =>
    rows(item.items),
  );
  const watched = rows(workbench.directions?.watched).flatMap((item) =>
    rows(item.items),
  );
  const current = rows(workbench.today_ladder?.distribution).flatMap((item) =>
    rows(item.items),
  );
  for (const row of [...current, ...negatives, ...watched, ...previous]) {
    const symbol = row.instrument_id;
    if (typeof symbol !== "string") continue;
    const existing = output[symbol] || {};
    output[symbol] = {
      ...existing,
      ...(Array.isArray(row.industries) ? { industries: row.industries } : {}),
      ...(Array.isArray(row.topics) ? { topics: row.topics } : {}),
      ...(noonKnownHeight(row.previous_board_count) != null
        ? { previous_board_count: row.previous_board_count }
        : {}),
      ...(typeof row.theme_text === "string" && row.theme_text.trim()
        ? {
            theme_text: row.theme_text,
            theme_date: row.theme_date,
            theme_source: row.theme_source,
          }
        : {}),
    };
  }
  const currentEvents: Record<string, Set<string>> = {};
  const payload = snapshot.payload || {};
  for (const key of ["limit_up", "limit_down", "limit_break"]) {
    const pool = payload.events?.[key];
    if (!["observed", "partial"].includes(pool?.state)) continue;
    for (const row of rows(pool.items)) {
      const symbol = row.instrument_id;
      if (typeof symbol !== "string" || !/^\d{6}\.(SH|SZ|BJ)$/.test(symbol))
        continue;
      (currentEvents[symbol] ||= new Set()).add(key);
      const value = (output[symbol] ||= {});
      if (key === "limit_up")
        value.current_board_count = noonKnownHeight(row.continue_day_cnt);
    }
  }
  for (const [symbol, states] of Object.entries(currentEvents)) {
    output[symbol].current_event_state =
      states.size === 1 ? [...states][0] : "conflict";
  }
  const previousDay = payload.previous_review?.date;
  for (const cohort of rows(workbench.negative_feedback?.cohorts)) {
    if (
      !previousDay ||
      cohort.date !== previousDay ||
      !["observed", "observed_sample", "partial"].includes(cohort.state)
    )
      continue;
    for (const row of rows(cohort.items)) {
      const value = (output[row.instrument_id] ||= {});
      value.previous_event_states = [
        ...new Set([...(value.previous_event_states || []), cohort.key]),
      ];
    }
  }
  return output;
}

export function noonEventLabels(row: Row): { label: string; tone: string }[] {
  const result: { label: string; tone: string }[] = [];
  const height = noonKnownHeight(row.current_board_count);
  if (row.current_event_state === "limit_up")
    result.push({
      label:
        height === 1 ? "本次首板" : height ? `本次${height}连板` : "本次涨停",
      tone: "up",
    });
  else if (row.current_event_state === "limit_down")
    result.push({ label: "本次跌停", tone: "down" });
  else if (row.current_event_state === "limit_break")
    result.push({ label: "本次炸板", tone: "neutral" });
  else if (row.current_event_state === "conflict")
    result.push({ label: "本次池状态冲突", tone: "neutral" });
  const previousHeight = noonKnownHeight(row.previous_board_count);
  if (previousHeight)
    result.push({
      label: previousHeight === 1 ? "昨日首板" : `昨日${previousHeight}连板`,
      tone: "up",
    });
  if (row.previous_event_states?.includes("limit_down"))
    result.push({ label: "昨日跌停", tone: "down" });
  if (row.previous_event_states?.includes("limit_break"))
    result.push({ label: "昨日炸板", tone: "neutral" });
  return result;
}

export function noonEnrichQuotes(
  items: Row[],
  context: Record<string, Row>,
): Row[] {
  return items.map((row) => {
    const known = context[row.instrument_id] || {};
    return {
      ...known,
      ...row,
      // Current provider rows cannot introduce an unverified yesterday height.
      previous_board_count: noonKnownHeight(known.previous_board_count),
    };
  });
}
