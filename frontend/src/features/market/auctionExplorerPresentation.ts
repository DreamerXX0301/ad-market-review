import type { Row } from "../../lib/api";
import {
  auctionPreviousHeight,
  auctionUniqueHeightRows,
} from "./auctionHeightPresentation";
import { auctionStockEvent } from "./auctionStockBadgePresentation";
import { auctionNumber } from "./auctionPresentation";

// Display-only, exact labels: broad access/index attributes are not themes.
// Real industries, company-chain themes and policy themes remain selectable.
const GENERAL_ATTRIBUTES = new Set([
  "融资融券",
  "转融券标的",
  "沪股通",
  "深股通",
  "沪深股通",
  "陆股通",
  "MSCI概念",
  "MSCI中国",
  "富时罗素概念",
  "富时罗素概念股",
  "标普道琼斯A股",
  "沪深300",
  "上证50",
  "上证180",
  "中证500",
  "中证1000",
]);

export const isAuctionGeneralAttribute = (row: Row) =>
  row.kind === "concept" && GENERAL_ATTRIBUTES.has(String(row.name).trim());

export function auctionFocusedDirections(groups: Row[]) {
  const attributes = groups.filter(isAuctionGeneralAttribute);
  const ids = new Set(attributes.map(auctionGroupIdentity));
  const meaningful = groups.filter(
    (row) => !ids.has(auctionGroupIdentity(row)),
  );
  return {
    groups: meaningful.filter(
      (row) => typeof row.sample_count === "number" && row.sample_count >= 3,
    ),
    attribute_count: attributes.length,
    small_count: meaningful.filter(
      (row) => !(typeof row.sample_count === "number" && row.sample_count >= 3),
    ).length,
  };
}

const idsOf = (group: Row): string[] => [
  ...new Set<string>(
    (Array.isArray(group.instrument_ids) ? group.instrument_ids : []).filter(
      (id: unknown) => typeof id === "string" && /^\d{6}\.(SH|SZ|BJ)$/.test(id),
    ),
  ),
];

/** Conflicting previous-day event evidence must not create a ladder level. */
export const auctionVerifiedPreviousHeight = (row: Row): number | null =>
  auctionStockEvent(row)?.kind === "height" ? auctionPreviousHeight(row) : null;

const observedChange = (row: Row): number | null =>
  row.state === "observed" ? auctionNumber(row.auction_pct) : null;

function quoteIndex(quotes: Row[]) {
  const events = new Map<string, string>();
  const conflicts = new Set<string>();
  for (const row of quotes) {
    const event = `${auctionStockEvent(row)?.kind || "unknown"}:${auctionVerifiedPreviousHeight(row)}`;
    if (
      events.has(row.instrument_id) &&
      events.get(row.instrument_id) !== event
    )
      conflicts.add(row.instrument_id);
    events.set(row.instrument_id, event);
  }
  return new Map(
    auctionUniqueHeightRows(quotes).map((row) => [
      row.instrument_id,
      conflicts.has(row.instrument_id)
        ? { ...row, previous_state: "conflict", previous_board_count: null }
        : row,
    ]),
  );
}

/** A selected reference is not evidence that it opened at limit or stayed one-price. */
export function auctionPeerObservation(
  group: Row,
  quotes: Row[],
  anchorId: string,
): Row | null {
  const ids = idsOf(group);
  const byId = quoteIndex(quotes);
  if (!anchorId || !ids.includes(anchorId) || !byId.has(anchorId)) return null;
  const anchor = byId.get(anchorId)!;
  const anchorHeight = auctionVerifiedPreviousHeight(anchor);
  const peers = ids
    .filter((id) => id !== anchorId)
    .map((id) => byId.get(id) || { instrument_id: id });
  const changes = peers
    .map(observedChange)
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);
  const middle = Math.floor(changes.length / 2);
  const lower =
    anchorHeight == null
      ? []
      : peers.filter((row) => {
          const height = auctionVerifiedPreviousHeight(row);
          return height != null && height < anchorHeight;
        });
  const highOpen = (row: Row) => (observedChange(row) ?? 0) > 0;
  return {
    anchor_id: anchorId,
    anchor_height: anchorHeight,
    instrument_ids: peers.map((row) => row.instrument_id),
    count: peers.length,
    valid_count: changes.length,
    missing_count: peers.length - changes.length,
    up_count: changes.filter((value) => value > 0).length,
    flat_count: changes.filter((value) => value === 0).length,
    down_count: changes.filter((value) => value < 0).length,
    up_ratio: changes.length
      ? changes.filter((value) => value > 0).length / changes.length
      : null,
    median_auction_pct: changes.length
      ? changes.length % 2
        ? changes[middle]
        : changes[middle - 1] / 2 + changes[middle] / 2
      : null,
    lower_count: anchorHeight == null ? null : lower.length,
    lower_valid_count:
      anchorHeight == null
        ? null
        : lower.filter((row) => observedChange(row) !== null).length,
    lower_up_ids:
      anchorHeight == null
        ? null
        : lower.filter(highOpen).map((row) => row.instrument_id),
    first_count: peers.filter((row) => auctionVerifiedPreviousHeight(row) === 1)
      .length,
    second_count: peers.filter(
      (row) => auctionVerifiedPreviousHeight(row) === 2,
    ).length,
    higher_count: peers.filter(
      (row) => (auctionVerifiedPreviousHeight(row) ?? 0) >= 3,
    ).length,
    broken_count: peers.filter(
      (row) => auctionStockEvent(row)?.kind === "limit_break",
    ).length,
    other_count: peers.filter(
      (row) =>
        auctionVerifiedPreviousHeight(row) === null &&
        auctionStockEvent(row)?.kind !== "limit_break",
    ).length,
  };
}

export const auctionGroupIdentity = (row: Row) =>
  JSON.stringify([
    row.source,
    row.kind,
    row.topic_code,
    row.members_observed_date,
    row.source_snapshot_id,
    row.source_sha256,
  ]);

/** Join only the same frozen auction and member evidence, never a display name. */
export function auctionExplorerGroups(
  topics: Row | null,
  resonance: Row | null,
  date: string,
  quotes: Row[] = [],
): Row[] {
  if (topics?.date !== date || topics.version !== "auction-topic-summary-v1")
    return [];
  const same =
    resonance?.date === date &&
    resonance.version === "auction-resonance-v1" &&
    topics.source_snapshot_id &&
    topics.source_sha256 &&
    resonance.source_snapshot_id === topics.source_snapshot_id &&
    resonance.source_sha256 === topics.source_sha256;
  const evidence = new Map<string, Row>(
    (same && Array.isArray(resonance.groups) ? resonance.groups : []).map(
      (row: Row) => [auctionGroupIdentity(row), row],
    ),
  );
  const byId = quoteIndex(quotes);
  return (Array.isArray(topics.groups) ? topics.groups : []).map(
    (group: Row) => {
      const related = evidence.get(auctionGroupIdentity(group));
      const rows = idsOf(group).map(
        (id) => byId.get(id) || { instrument_id: id },
      );
      const heights = rows
        .map(auctionVerifiedPreviousHeight)
        .filter((value): value is number => value !== null);
      return {
        ...group,
        resonance: related,
        top_count: related?.top_count ?? null,
        concentration_lift: related?.concentration_lift ?? null,
        median_auction_pct: related?.median_auction_pct ?? null,
        up_ratio: related?.up_ratio ?? null,
        previous_max_height: heights.length ? Math.max(...heights) : null,
        previous_height_known_count: heights.length,
        previous_height_unknown_count: rows.filter(
          (row) =>
            auctionVerifiedPreviousHeight(row) === null &&
            auctionStockEvent(row)?.kind !== "limit_break",
        ).length,
      };
    },
  );
}
