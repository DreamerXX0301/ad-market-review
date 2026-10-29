import type { Row } from "../../lib/api";

/** Every read binds one immutable capture; no close-price membership endpoint. */
export function noonSectorEndpoint(
  snapshotId: string,
  topicCode: string,
  filters: Row = {},
): string | null {
  if (
    !/^noon_[0-9a-f]{32}$/.test(snapshotId) ||
    !/^\d{6}\.(TI|SH|SZ)$/.test(topicCode)
  )
    return null;
  const query = new URLSearchParams({ topic_code: topicCode });
  for (const key of ["board", "query", "sort", "direction", "page", "limit"]) {
    if (filters[key] != null) query.set(key, String(filters[key]));
  }
  return `/market/noon/${encodeURIComponent(snapshotId)}/sector-members?${query}`;
}
