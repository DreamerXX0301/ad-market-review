import type { Row } from "../lib/api";
import type { HotUniverseValue } from "../features/strategies/HotUniverse";

export type HotBrowseState = "matched" | "unmatched" | "unknown";
export type HotBrowseMode = "observed" | "verified";

const validDate = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const date = new Date(`${value}T00:00:00Z`);
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
};

export function hotBrowseScopeMatches(
  left: Row | undefined,
  right: Row | undefined,
) {
  return (
    !!left &&
    !!right &&
    validDate(left.business_date) &&
    typeof left.snapshot_id === "string" &&
    left.snapshot_id.length > 0 &&
    typeof left.topic_revision_signature === "string" &&
    /^[a-f0-9]{64}$/.test(left.topic_revision_signature) &&
    left.business_date === right.business_date &&
    left.snapshot_id === right.snapshot_id &&
    left.topic_revision_signature === right.topic_revision_signature
  );
}

export function hotBrowseConfigMatches(
  left: Row | undefined,
  right: HotUniverseValue,
) {
  return (
    left?.window_sessions === right.window_sessions &&
    left?.top_n === right.top_n &&
    left?.kind === right.kind
  );
}

export function hotBrowseEndpoint(
  scope: Row | undefined,
  config: HotUniverseValue,
  enabled: boolean,
) {
  if (!enabled || !hotBrowseScopeMatches(scope, scope)) return null;
  return (
    "/hot-universe?" +
    new URLSearchParams({
      as_of: scope!.business_date,
      snapshot_id: scope!.snapshot_id,
      topic_revision_signature: scope!.topic_revision_signature,
      window_sessions: String(config.window_sessions),
      top_n: String(config.top_n),
      kind: config.kind,
    })
  );
}

/** Apply before table sorting/paging. Current proxy receipt never changes frozen row evidence. */
export function filterHotBrowseRows(
  rows: Row[],
  evidence: Row | null | undefined,
  scope: Row | undefined,
  config: HotUniverseValue,
  enabled: boolean,
  includeUnknown: boolean,
  mode: HotBrowseMode = "observed",
) {
  if (!enabled)
    return {
      rows,
      matched: 0,
      unmatched: 0,
      unknown: 0,
      observed: 0,
      observedUnknown: 0,
      ready: false,
    };
  const valid =
    hotBrowseScopeMatches(scope, evidence?.browse_scope) &&
    evidence?.business_date === scope?.business_date &&
    evidence?.snapshot_id === scope?.snapshot_id &&
    evidence?.version === "current-hot-universe-v1" &&
    hotBrowseConfigMatches(evidence?.config, config) &&
    Array.isArray(evidence?.member_ids) &&
    evidence.member_ids.every(
      (id: unknown) => typeof id === "string" && /^\d{6}\.(SH|SZ|BJ)$/.test(id),
    );
  const members = new Set<string>(valid ? evidence!.member_ids : []);
  const complete =
    valid &&
    evidence!.complete === true &&
    evidence!.window?.complete === true &&
    evidence!.coverage?.complete === true;
  let matched = 0,
    unmatched = 0,
    unknown = 0,
    observed = 0,
    observedUnknown = 0;
  const checked = rows.map((row) => {
    const id = row.instrument_id;
    const validSymbol =
      typeof id === "string" && /^\d{6}\.(SH|SZ|BJ)$/.test(id);
    const present = validSymbol && members.has(id);
    const state: HotBrowseState =
      complete && validSymbol ? (present ? "matched" : "unmatched") : "unknown";
    if (state === "matched") matched++;
    else if (state === "unmatched") unmatched++;
    else unknown++;
    if (present) observed++;
    if (present && state === "unknown") observedUnknown++;
    return { ...row, _hot_filter_state: state, _hot_observed_match: present };
  });
  return {
    rows: checked.filter(
      (row) =>
        row._hot_filter_state === "matched" ||
        (mode === "observed" &&
          (row._hot_observed_match ||
            (includeUnknown && row._hot_filter_state === "unknown"))),
    ),
    matched,
    unmatched,
    unknown,
    observed,
    observedUnknown,
    ready: valid,
  };
}
