import type { Row } from "../../lib/api";

export type SavedRunTarget = "stock" | "sector";
const MAX_ENTRIES = 24;
const MAX_BYTES = 8 * 1024 * 1024;
const MAX_CANDIDATES = 30000;
const MAX_AGE_MS = 60000;
type Entry = {
  value: Row;
  scope: string;
  bytes: number;
  rows: number;
  at: number;
};
const cache = new Map<string, Entry>();
let bytes = 0,
  candidates = 0;
const keyFor = (target: SavedRunTarget, id: string, decision: string) =>
  `${target}:${id}:${decision}`;

export function savedRunPath(
  target: SavedRunTarget,
  id: string,
  decision: string,
) {
  return `${target === "sector" ? "/sector-strategy-runs/" : "/strategy-runs/"}${encodeURIComponent(id)}?decision=${encodeURIComponent(decision)}`;
}

export function savedStrategyScope(
  target: SavedRunTarget,
  strategy?: Row,
  inputKey = "",
) {
  return JSON.stringify([
    target,
    strategy?.strategy_id,
    strategy?.version,
    strategy?.latest_run?.run_id,
    strategy?.latest_run?.status,
    strategy?.updated_at_ms,
    strategy?.deleted_at_ms,
    strategy?.archived,
    inputKey,
  ]);
}

/** Saved candidate objects are immutable UI data. Never retain other decisions. */
export function savedRunPart(value: Row, decision: string): Row {
  const { items, candidates: oldCandidates, ...metadata } = value;
  const rows = Array.isArray(items)
    ? items
    : Array.isArray(oldCandidates)
      ? oldCandidates
      : [];
  return {
    ...metadata,
    items: rows.filter((row: Row) => row.decision === decision),
    loaded_decision: decision,
  };
}

function remove(key: string) {
  const previous = cache.get(key);
  if (previous) {
    bytes -= previous.bytes;
    candidates -= previous.rows;
    cache.delete(key);
  }
}

export function readSavedRun(
  target: SavedRunTarget,
  id: string,
  decision: string,
  scope: string,
  now = Date.now(),
) {
  const key = keyFor(target, id, decision),
    entry = cache.get(key);
  if (!entry) return null;
  if (entry.scope !== scope || now - entry.at > MAX_AGE_MS) {
    remove(key);
    return null;
  }
  cache.delete(key);
  cache.set(key, entry);
  return entry.value;
}

export function rememberSavedRun(
  target: SavedRunTarget,
  value: Row,
  decision: string,
  scope: string,
  now = Date.now(),
) {
  const part = savedRunPart(value, decision),
    key = keyFor(target, part.run_id, decision);
  remove(key);
  // Conservative UTF-16 payload budget, independent of the number of rows.
  const size = JSON.stringify(part).length * 2,
    rows = part.items.length;
  if (size > MAX_BYTES || rows > MAX_CANDIDATES) return part;
  cache.set(key, { value: part, scope, bytes: size, rows, at: now });
  bytes += size;
  candidates += rows;
  while (
    cache.size > MAX_ENTRIES ||
    bytes > MAX_BYTES ||
    candidates > MAX_CANDIDATES
  )
    remove(cache.keys().next().value!);
  return part;
}

export function invalidateSavedStrategy(
  target: SavedRunTarget,
  strategyId: string,
) {
  for (const [key, entry] of cache)
    if (key.startsWith(target + ":") && entry.value.strategy_id === strategyId)
      remove(key);
}

export function clearSavedRunCache() {
  cache.clear();
  bytes = 0;
  candidates = 0;
}
export function savedRunCacheStats() {
  return {
    entries: cache.size,
    bytes,
    candidates,
    maxEntries: MAX_ENTRIES,
    maxBytes: MAX_BYTES,
    maxCandidates: MAX_CANDIDATES,
  };
}
