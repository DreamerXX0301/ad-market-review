import type { Row } from "../../lib/api";

export type SectorExplorerMode =
  "all" | "rising" | "falling" | "amount" | "limit" | "activity" | "broken";
export type SectorActivityMetric = "change_pct" | "amount";
export type SectorExplorerKind = "concept" | "industry";
export type SectorExplorerGroup = {
  key: string;
  label: string;
  source: string | null;
  level: string | null;
  quoteBasis: string;
  normalizationVersion: string | null;
  verified: boolean;
  count: number;
};

const text = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number =>
  finite(value) && Number.isInteger(value) && value >= 0;
const kindOf = (row: Row) =>
  row.kind === "industry" || row.category === "行业"
    ? "industry"
    : row.kind === "concept" || row.category === "概念"
      ? "concept"
      : "unknown";
const levelOf = (row: Row): string | null => {
  const value = row.classification_level ?? row.level;
  return (
    text(value) ??
    (finite(value) && Number.isInteger(value) && value >= 0
      ? String(value)
      : null)
  );
};

function context(row: Row) {
  const source = text(row.source);
  const quoteSource = text(row.quote_source);
  const kind = kindOf(row);
  const normalizationVersion = text(row.normalization_version);
  const quoteBasis = text(row.quote_basis) || "unknown";
  return {
    source: quoteSource || source,
    declaredSource: source,
    quoteSource,
    kind,
    level: kind === "industry" ? levelOf(row) : null,
    normalizationVersion,
    quoteBasis,
    conflict: !!source && !!quoteSource && source !== quoteSource,
  };
}

function codeOf(row: Row) {
  return (
    text(row.topic_code) ||
    text(row.thscode) ||
    text(row.source_code) ||
    text(row.topic_id)
  );
}

/** UI identity is source-scoped; an unidentified row is never a provider code. */
export function sectorExplorerId(row: Row): string {
  const c = context(row);
  return JSON.stringify([
    c.kind,
    c.declaredSource,
    c.quoteSource,
    c.normalizationVersion,
    c.quoteBasis,
    c.level,
    codeOf(row) ? "code" : "unidentified-name",
    codeOf(row) || text(row.name),
  ]);
}

function groupOf(row: Row): SectorExplorerGroup {
  const c = context(row);
  const verified =
    !!c.source &&
    !!c.normalizationVersion &&
    !c.conflict &&
    c.quoteBasis === "provider_index" &&
    c.kind !== "unknown";
  // Unknown source/normalization/basis rows cannot certify a shared universe.
  const uncertain = !verified ? sectorExplorerId(row) : null;
  const key = JSON.stringify([
    c.kind,
    c.declaredSource,
    c.quoteSource,
    c.normalizationVersion,
    c.quoteBasis,
    c.level,
    uncertain,
  ]);
  const basis =
    c.quoteBasis === "provider_index"
      ? "来源指数"
      : c.quoteBasis === "unknown"
        ? "报价口径未知"
        : "成员样本 / 独立口径";
  return {
    key,
    label: [
      c.conflict ? "来源字段冲突" : c.source || "来源未知",
      basis,
      c.kind === "industry"
        ? c.level === null
          ? "行业层级未知"
          : `行业层级 ${c.level}`
        : "概念",
      !c.normalizationVersion ? "规范版本未核验" : null,
      !verified ? codeOf(row) || text(row.name) || "身份待核验" : null,
    ]
      .filter(Boolean)
      .join(" · "),
    source: c.source,
    level: c.level,
    quoteBasis: c.quoteBasis,
    normalizationVersion: c.normalizationVersion,
    verified,
    count: 1,
  };
}

/** Keep source, quote basis, classification level and normalization separate. */
export function sectorExplorerGroups(
  rows: Row[],
  kind: SectorExplorerKind,
): SectorExplorerGroup[] {
  const groups = new Map<string, SectorExplorerGroup>();
  for (const row of rows) {
    if (kindOf(row) !== kind) continue;
    const group = groupOf(row);
    const known = groups.get(group.key);
    if (known) known.count += 1;
    else groups.set(group.key, group);
  }
  const collator = new Intl.Collator("zh-CN", { numeric: true });
  return [...groups.values()].sort(
    (a, b) =>
      Number(b.verified) - Number(a.verified) ||
      collator.compare(a.label, b.label) ||
      a.key.localeCompare(b.key),
  );
}

function activityView(row: Row, metric: SectorActivityMetric) {
  const group = groupOf(row);
  const raw = row.sector_activity_7d?.[metric];
  const unavailable = {
    count: null as number | null,
    complete: false,
    state: "unknown",
    label: "待核验",
  };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return unavailable;
  const rowDate = text(row.quote_date) || text(row.date);
  const scopeValid =
    group.verified &&
    raw.quote_basis === "provider_index" &&
    raw.source === group.source &&
    raw.group_kind === kindOf(row) &&
    (kindOf(row) !== "industry" ||
      (raw.group_level == null ? null : String(raw.group_level)) ===
        group.level) &&
    raw.metric === metric &&
    (!rowDate || raw.window_end === rowDate);
  if (
    !scopeValid ||
    raw.expected_days !== 7 ||
    !count(raw.observed_days) ||
    raw.observed_days > 7 ||
    !count(raw.count) ||
    raw.count > raw.observed_days
  )
    return unavailable;
  if (
    raw.state === "complete" &&
    raw.complete === true &&
    raw.observed_days === 7
  )
    return {
      count: raw.count,
      complete: true,
      state: "complete",
      label: `近7日${raw.count}次`,
    };
  if (
    raw.state === "observed_partial" &&
    raw.complete === false &&
    raw.count > 0
  )
    return {
      count: raw.count,
      complete: false,
      state: "observed_partial",
      label: `已观察${raw.count}次 · 样本不完整`,
    };
  return unavailable;
}

/** A dated backend result is authoritative; observations never become exact rates. */
export function sectorBrokenBoard(
  row: Row,
  observationDate?: string,
  snapshotId?: string,
) {
  const raw = row.broken_board;
  const date = observationDate || text(row.quote_date) || text(row.date);
  const unknown = (reason = "该日炸板结构尚未核验") => ({
    state: "unknown",
    date,
    limit_up_count: null as number | null,
    broken_count: null as number | null,
    touch_count: null as number | null,
    broken_rate_pct: null as number | null,
    observed_limit_up_count: null as number | null,
    observed_broken_count: null as number | null,
    membership_date: null as string | null,
    details: [] as Row[],
    reason,
  });
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return unknown();
  if (
    !date ||
    raw.date !== date ||
    (snapshotId && raw.snapshot_id !== snapshotId) ||
    (text(row.quote_date) && row.quote_date !== date)
  )
    return unknown("炸板结构的日期或行情代际与所选范围不一致");
  const state = raw.state;
  if (state !== "complete" && state !== "partial")
    return unknown(text(raw.reason) || undefined);
  const details = new Map<string, Row>();
  const conflicts = new Set<string>();
  for (const item of Array.isArray(raw.details) ? raw.details : []) {
    const id = text(item.instrument_id);
    if (
      !id ||
      item.date !== date ||
      item.snapshot_id !== raw.snapshot_id ||
      !["limit_up", "broken"].includes(item.outcome)
    )
      continue;
    if (details.has(id) && details.get(id)?.outcome !== item.outcome)
      conflicts.add(id);
    else if (!details.has(id)) details.set(id, item);
  }
  for (const id of conflicts) details.delete(id);
  const result = {
    ...unknown(),
    state,
    observed_limit_up_count: count(raw.observed_limit_up_count)
      ? raw.observed_limit_up_count
      : null,
    observed_broken_count: count(raw.observed_broken_count)
      ? raw.observed_broken_count
      : null,
    membership_date: text(raw.membership_date),
    details: [...details.values()],
    reason: text(raw.reason) || "按所选日留存成员关联；不代表涨停原因",
  };
  if (state === "partial") return result;
  const validCounts =
    count(raw.limit_up_count) &&
    count(raw.broken_count) &&
    count(raw.touch_count) &&
    raw.touch_count === raw.limit_up_count + raw.broken_count;
  const validRate =
    validCounts &&
    (raw.touch_count === 0
      ? raw.broken_rate_pct === null
      : finite(raw.broken_rate_pct) &&
        Math.abs(
          raw.broken_rate_pct - (raw.broken_count / raw.touch_count) * 100,
        ) < 0.001);
  if (!validCounts || !validRate)
    return unknown("炸板结构家数或分母未通过一致性核验");
  return {
    ...result,
    limit_up_count: raw.limit_up_count,
    broken_count: raw.broken_count,
    touch_count: raw.touch_count,
    broken_rate_pct: raw.broken_rate_pct,
  };
}

/** Unsorted, presentation-only rows; callers sort the full result before paging. */
export function sectorExplorerRows(
  rows: Row[],
  {
    kind,
    groupKey,
    query = "",
    mode = "all",
    activityMetric = "change_pct",
    observationDate,
    snapshotId,
  }: {
    kind: SectorExplorerKind;
    groupKey?: string | null;
    query?: string;
    mode?: SectorExplorerMode;
    activityMetric?: SectorActivityMetric;
    observationDate?: string;
    snapshotId?: string;
  },
): Row[] {
  const groups = sectorExplorerGroups(rows, kind);
  const selected = groups.find((group) => group.key === groupKey) || groups[0];
  if (!selected) return [];
  const search = query.trim().toLocaleLowerCase();
  return rows
    .filter((row) => {
      if (kindOf(row) !== kind || groupOf(row).key !== selected.key)
        return false;
      if (
        search &&
        ![
          row.name,
          row.topic_code,
          row.thscode,
          row.source_code,
          row.topic_id,
        ].some((value) => text(value)?.toLocaleLowerCase().includes(search))
      )
        return false;
      return mode === "rising"
        ? finite(row.change_pct) && row.change_pct > 0
        : mode === "falling"
          ? finite(row.change_pct) && row.change_pct < 0
          : true;
    })
    .map((row) => {
      const activity = activityView(row, activityMetric);
      const broken = sectorBrokenBoard(row, observationDate, snapshotId);
      const ratio =
        count(row.up_count) &&
        count(row.observed_count) &&
        row.observed_count > 0 &&
        row.up_count <= row.observed_count
          ? (row.up_count / row.observed_count) * 100
          : null;
      return {
        ...row,
        _sector_id: sectorExplorerId(row),
        _sector_group: selected.key,
        _sector_activity_count: activity.count,
        _sector_activity_complete: activity.complete,
        _sector_activity_state: activity.state,
        _sector_activity_label: activity.label,
        _sector_up_ratio: ratio,
        _sector_limit_sort: count(row.limit_up_count)
          ? row.limit_up_count
          : null,
        _sector_broken: broken,
        _sector_broken_sort: broken.broken_count,
        _sector_broken_rate_sort: broken.broken_rate_pct,
        _sector_touch_sort: broken.touch_count,
        _sector_broken_up_sort: broken.limit_up_count,
      };
    });
}

export function sectorExplorerDefaultSort(mode: SectorExplorerMode): {
  key: string;
  order: "asc" | "desc";
} {
  return {
    key:
      mode === "broken"
        ? "_sector_broken_sort"
        : mode === "activity"
          ? "_sector_activity_count"
          : mode === "limit"
            ? "_sector_limit_sort"
            : mode === "amount"
              ? "amount"
              : "change_pct",
    order: mode === "falling" ? "asc" : "desc",
  };
}
