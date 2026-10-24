import type { Row } from "../../lib/api";

export type SystemMainlineMember = {
  instrument_id: string;
  source_reason: string | null;
  matched_tag: string | null;
  matched_tags: string[];
  source: string | null;
  source_ref: {
    snapshot_id: string;
    date: string;
    endpoint: string;
    field_path: string;
  };
  closing_pool_refs: {
    source?: string | null;
    path?: string | null;
    raw_sha256?: string;
    purpose: "closing_event_only";
  }[];
};
export type SystemMainlineGroup = {
  key: string;
  name: string;
  kind: "direction" | "catalyst";
  grouping_rule: string;
  labels: string[];
  count: number;
  state?: "candidate";
  max_known_height: number | null;
  high_stock_ids: string[];
  members: SystemMainlineMember[];
};
export type SystemMainlines = {
  algorithm_version: string;
  source_kind: "local_source_synthesis";
  state: "candidate" | "unavailable";
  date: string;
  snapshot_id: string;
  groups: SystemMainlineGroup[];
  other_groups: SystemMainlineGroup[];
  individual_cues: SystemMainlineMember[];
  missing_reason: SystemMainlineMember[];
  coverage: {
    scope: string;
    confirmed_sample_count: number;
    focus_count: number;
    other_count: number;
    individual_count: number;
    missing_reason_count: number;
    rejected_row_count: number;
    [key: string]: unknown;
  };
  reason: string | null;
  notice: string;
};
export type MainlineSourceItem = {
  stock: Row;
  sourceReason: string;
  height: number | null;
  evidence: SystemMainlineMember;
};
export type MainlineDirection = {
  key: string;
  name: string;
  kind: "direction" | "catalyst";
  items: MainlineSourceItem[];
  count: number;
  maxKnownHeight: number | null;
};
export type DailyMainlineDisplay = {
  available: boolean;
  total: number;
  classifiedCount: number;
  groups: MainlineDirection[];
  otherGroups: MainlineDirection[];
  individualCues: MainlineSourceItem[];
  missingReasonCount: number;
  sourceGapCount: number;
  globalHigh: { height: number | null; stocks: Row[]; unknownCount: number };
};
const object = (value: unknown): value is Row =>
  !!value && typeof value === "object" && !Array.isArray(value);
const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const stockId = (value: unknown): value is string =>
  typeof value === "string" && /^\d{6}\.(SH|SZ|BJ)$/.test(value);
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const differs = (row: Row, key: string, expected: unknown) =>
  Object.hasOwn(row, key) && row[key] !== expected;
const hasRawQuote = (stock: Row) =>
  positive(stock.close) &&
  positive(stock.volume) &&
  !differs(stock, "price_basis", "none");
const emptyReasons = new Set([
  "未知",
  "暂无",
  "暂无数据",
  "未提供",
  "未归因",
  "待核验",
  "无",
  "其他",
  "n/a",
  "na",
  "none",
  "null",
  "unknown",
  "--",
  "-",
  "—",
  "–",
]);
// This is the backend's missing-text predicate, not a direction classifier.
const hasSourceReason = (text: string) =>
  text.split(/[+＋]/).some((part) => {
    const tag = part.trim();
    return !emptyReasons.has(tag.toLowerCase()) && /[\p{L}\p{N}_]/u.test(tag);
  });
const day = (value: string) =>
  /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  !value.startsWith("0000-") &&
  Number.isFinite(Date.parse(`${value}T00:00:00Z`)) &&
  new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

/** Only dated display facts can nominate a high observation; raw counts do not. */
export function confirmedMainlineHeight(
  stock: Row,
  date: string,
  snapshotId: unknown,
): number | null {
  const facts = stock.board_presentation;
  const height = object(facts) && object(facts.height) ? facts.height : null;
  return typeof snapshotId === "string" &&
    !!snapshotId &&
    stock.date === date &&
    stock.snapshot_id === snapshotId &&
    hasRawQuote(stock) &&
    !differs(stock, "trade_date", date) &&
    !differs(stock, "evidence_date", date) &&
    !differs(stock, "kind", "stock") &&
    object(facts) &&
    facts.version === "dated-stock-board-presentation-v1" &&
    facts.date === date &&
    facts.snapshot_id === snapshotId &&
    facts.instrument_id === stock.instrument_id &&
    height?.state === "confirmed" &&
    height.source_date === date &&
    typeof height.label === "string" &&
    !!height.label.trim() &&
    count(height.value) &&
    height.value > 0
    ? height.value
    : null;
}

/** Validate the backend partition rather than recreating a Chinese classifier. */
export function buildDailyMainlineDisplay(
  date: string,
  rows: Row[],
  system: unknown,
  snapshotId: unknown,
): DailyMainlineDisplay {
  const identities = new Map<string, Row>();
  const conflicting = new Set<string>();
  const bound =
    day(date) && typeof snapshotId === "string" && !!snapshotId.trim();
  for (const stock of rows) {
    if (
      !bound ||
      !object(stock) ||
      !stockId(stock.instrument_id) ||
      stock.date !== date ||
      stock.snapshot_id !== snapshotId ||
      stock.limit_up_state !== true ||
      !hasRawQuote(stock) ||
      differs(stock, "trade_date", date) ||
      differs(stock, "evidence_date", date) ||
      differs(stock, "kind", "stock")
    )
      continue;
    const previous = identities.get(stock.instrument_id);
    if (
      previous &&
      ((previous.reason ?? null) !== (stock.reason ?? null) ||
        confirmedMainlineHeight(previous, date, snapshotId) !==
          confirmedMainlineHeight(stock, date, snapshotId))
    )
      conflicting.add(stock.instrument_id);
    else if (!previous) identities.set(stock.instrument_id, stock);
  }
  for (const id of conflicting) identities.delete(id);
  const known = [...identities.values()].map((stock) => ({
    stock,
    height: confirmedMainlineHeight(stock, date, snapshotId),
  }));
  const maximum =
    known.reduce((value, item) => Math.max(value, item.height || 0), 0) || null;
  const empty: DailyMainlineDisplay = {
    available: false,
    total: identities.size,
    classifiedCount: 0,
    groups: [],
    otherGroups: [],
    individualCues: [],
    missingReasonCount: 0,
    sourceGapCount: 0,
    globalHigh: {
      height: maximum,
      stocks:
        maximum == null
          ? []
          : known
              .filter((item) => item.height === maximum)
              .map((item) => item.stock),
      unknownCount: known.filter((item) => item.height == null).length,
    },
  };
  if (
    !bound ||
    conflicting.size ||
    !object(system) ||
    system.algorithm_version !== "source-reason-directions-v2" ||
    system.source_kind !== "local_source_synthesis" ||
    system.state !== "candidate" ||
    system.date !== date ||
    system.snapshot_id !== snapshotId ||
    !Array.isArray(system.groups) ||
    system.groups.length > 3 ||
    !Array.isArray(system.other_groups) ||
    !Array.isArray(system.individual_cues) ||
    !Array.isArray(system.missing_reason) ||
    !object(system.coverage) ||
    system.coverage.scope !== "local_confirmed_sample_only"
  )
    return empty;
  const seen = new Set<string>();
  const groupKeys = new Set<string>();
  const member = (
    value: unknown,
    category: "group" | "individual" | "missing",
  ): MainlineSourceItem | null => {
    if (
      !object(value) ||
      !stockId(value.instrument_id) ||
      seen.has(value.instrument_id)
    )
      return null;
    const stock = identities.get(value.instrument_id);
    const reference = value.source_ref;
    if (
      !stock ||
      !object(reference) ||
      reference.date !== date ||
      reference.snapshot_id !== snapshotId ||
      reference.endpoint !== "/api/v1/market/ladder" ||
      reference.field_path !==
        `items[instrument_id="${value.instrument_id}"].reason`
    )
      return null;
    const rawReason = value.source_reason;
    if (rawReason != null && typeof rawReason !== "string") return null;
    const text = rawReason ?? "";
    if (
      text !== (stock.reason ?? "") ||
      (category === "missing"
        ? hasSourceReason(text)
        : !hasSourceReason(text)) ||
      !Array.isArray(value.matched_tags) ||
      !value.matched_tags.every(
        (tag: unknown) => typeof tag === "string" && !!tag.trim(),
      ) ||
      new Set(value.matched_tags).size !== value.matched_tags.length ||
      (value.matched_tag !== null && typeof value.matched_tag !== "string") ||
      (value.matched_tag ?? "") !== (value.matched_tags[0] || "") ||
      (category === "group" && !value.matched_tags.length) ||
      (category === "missing" && value.matched_tags.length)
    )
      return null;
    const tags = text.split(/[+＋]/).map((tag: string) => tag.trim());
    if (!value.matched_tags.every((tag: string) => tags.includes(tag)))
      return null;
    const evidence: SystemMainlineMember = {
      instrument_id: value.instrument_id,
      source_reason: rawReason ?? null,
      matched_tag: value.matched_tag,
      matched_tags: [...value.matched_tags],
      source: typeof value.source === "string" ? value.source : null,
      source_ref: {
        snapshot_id: reference.snapshot_id,
        date: reference.date,
        endpoint: reference.endpoint,
        field_path: reference.field_path,
      },
      closing_pool_refs: Array.isArray(value.closing_pool_refs)
        ? value.closing_pool_refs
            .filter(
              (ref: unknown) =>
                object(ref) && ref.purpose === "closing_event_only",
            )
            .map((ref: Row) => ({
              purpose: "closing_event_only" as const,
              ...(typeof ref.source === "string" ? { source: ref.source } : {}),
              ...(typeof ref.path === "string" ? { path: ref.path } : {}),
              ...(typeof ref.raw_sha256 === "string" &&
              /^[a-fA-F0-9]{64}$/.test(ref.raw_sha256)
                ? { raw_sha256: ref.raw_sha256 }
                : {}),
            }))
        : [],
    };
    seen.add(value.instrument_id);
    return {
      stock,
      sourceReason: text,
      height: confirmedMainlineHeight(stock, date, snapshotId),
      evidence,
    };
  };
  const direction = (
    value: unknown,
    focus: boolean,
  ): MainlineDirection | null => {
    if (
      !object(value) ||
      typeof value.key !== "string" ||
      !value.key ||
      groupKeys.has(value.key) ||
      typeof value.name !== "string" ||
      !value.name.trim() ||
      !["direction", "catalyst"].includes(value.kind) ||
      (focus && value.kind !== "direction") ||
      typeof value.grouping_rule !== "string" ||
      !value.grouping_rule ||
      !Array.isArray(value.members) ||
      !count(value.count) ||
      value.count !== value.members.length ||
      value.count < (focus ? 3 : 2) ||
      !Array.isArray(value.labels) ||
      !value.labels.every(
        (label: unknown) => typeof label === "string" && !!label.trim(),
      ) ||
      new Set(value.labels).size !== value.labels.length
    )
      return null;
    const items: MainlineSourceItem[] = [];
    for (const entry of value.members) {
      const item = member(entry, "group");
      if (!item) return null;
      items.push(item);
    }
    const labels = new Set(items.flatMap((item) => item.evidence.matched_tags));
    if (
      labels.size !== value.labels.length ||
      !value.labels.every((label: string) => labels.has(label))
    )
      return null;
    groupKeys.add(value.key);
    return {
      key: value.key,
      name: value.name,
      kind: value.kind,
      items,
      count: items.length,
      maxKnownHeight: items.reduce<number | null>(
        (maximum, item) =>
          item.height == null ? maximum : Math.max(maximum || 0, item.height),
        null,
      ),
    };
  };
  const groups: MainlineDirection[] = [],
    otherGroups: MainlineDirection[] = [],
    individuals: MainlineSourceItem[] = [];
  for (const value of system.groups) {
    const item = direction(value, true);
    if (!item) return empty;
    groups.push(item);
  }
  for (const value of system.other_groups) {
    const item = direction(value, false);
    if (!item) return empty;
    otherGroups.push(item);
  }
  for (const value of system.individual_cues) {
    const item = member(value, "individual");
    if (!item) return empty;
    individuals.push(item);
  }
  for (const value of system.missing_reason)
    if (!member(value, "missing")) return empty;
  const expected = {
    focus_count: groups.reduce((n, group) => n + group.count, 0),
    other_count: otherGroups.reduce((n, group) => n + group.count, 0),
    individual_count: individuals.length,
    missing_reason_count: system.missing_reason.length,
  };
  const coverage = system.coverage;
  if (
    !Object.entries(expected).every(
      ([key, value]) => count(coverage[key]) && coverage[key] === value,
    ) ||
    !count(coverage.confirmed_sample_count) ||
    coverage.confirmed_sample_count !== seen.size ||
    !count(coverage.rejected_row_count) ||
    seen.size > identities.size
  )
    return empty;
  const eligibleKnown = known.filter((item) =>
    seen.has(item.stock.instrument_id),
  );
  const eligibleMaximum =
    eligibleKnown.reduce(
      (value, item) => Math.max(value, item.height || 0),
      0,
    ) || null;
  return {
    ...empty,
    available: true,
    total: seen.size,
    classifiedCount: seen.size,
    groups,
    otherGroups,
    individualCues: individuals,
    missingReasonCount: system.missing_reason.length,
    sourceGapCount: Math.max(
      identities.size - seen.size,
      coverage.rejected_row_count,
    ),
    globalHigh: {
      height: eligibleMaximum,
      stocks:
        eligibleMaximum == null
          ? []
          : eligibleKnown
              .filter((item) => item.height === eligibleMaximum)
              .map((item) => item.stock),
      unknownCount: eligibleKnown.filter((item) => item.height == null).length,
    },
  };
}
