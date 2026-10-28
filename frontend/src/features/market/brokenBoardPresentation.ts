import type { Row } from "../../lib/api";

export const BROKEN_BOARD_VERSION = "broken-board-analysis-v1";

const record = (value: unknown): Row | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : null;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): number | null =>
  finite(value) && Number.isInteger(value) && value >= 0 ? value : null;
const text = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

function dated(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const day = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === value
  );
}

export type BrokenBoardMembershipKind = "concept" | "industry";

export const BROKEN_BOARD_BOARD_FILTERS = [
  { value: "all", label: "全部" },
  { value: "main", label: "主板" },
  { value: "chinext", label: "创业板" },
  { value: "star", label: "科创板" },
  { value: "beijing", label: "北交所" },
  { value: "unknown", label: "未知" },
] as const;
export type BrokenBoardBoardFilter =
  (typeof BROKEN_BOARD_BOARD_FILTERS)[number]["value"];

/** A missing or still-loading cohort is not a confirmed zero-stock result. */
export function brokenBoardDetailCount(
  state: unknown,
  itemCount: number,
  loading = false,
) {
  if (loading) return "读取中";
  const verified = count(itemCount);
  if (verified === null) return "待核验";
  if (state === "complete") return `${verified}只`;
  if (state === "partial" || verified > 0) return `已核验${verified}只`;
  return "待核验";
}

/** Use the API's code/exchange classification; missing identity stays unknown. */
export function brokenBoardBoard(
  input: unknown,
): Exclude<BrokenBoardBoardFilter, "all"> {
  const board = record(input)?.board;
  return ["main", "chinext", "star", "beijing"].includes(board)
    ? board
    : "unknown";
}

/** Keep a ranked prefix of complete chips; never expose a clipped last chip. */
export function brokenBoardVisibleTopicCount(
  availableWidth: number,
  rightEdges: number[],
) {
  if (!finite(availableWidth) || availableWidth <= 0) return 0;
  let visible = 0;
  for (const edge of rightEdges) {
    if (!finite(edge) || edge <= 0 || edge > availableWidth) break;
    visible += 1;
  }
  return visible;
}

/** Source, identity and industry level stay separate even when labels match. */
export function brokenBoardTopicKey(input: unknown): string | null {
  const row = record(input);
  if (!row || !text(row.source) || !["concept", "industry"].includes(row.kind))
    return null;
  const identity = text(row.topic_code) || text(row.topic_id);
  if (!identity) return null;
  const level = row.classification_level ?? row.level;
  return JSON.stringify([
    row.source.trim(),
    row.kind,
    identity,
    row.kind === "industry" && (text(level) || finite(level))
      ? String(level)
      : null,
  ]);
}

/** Membership is frozen to its stated cohort day, never replaced with today's. */
export function brokenBoardMembership(
  input: unknown,
  membershipDate: string,
  kind: BrokenBoardMembershipKind,
) {
  const row = record(input) || {};
  if (
    !dated(membershipDate) ||
    row.topics_date !== membershipDate ||
    row.topics_state === "unknown"
  )
    return [] as Row[];
  const tags = new Map<string, Row>();
  const inputTags = kind === "industry" ? row.industries : row.topics;
  for (const candidate of Array.isArray(inputTags) ? inputTags : []) {
    const tag = record(candidate);
    if (!tag || tag.kind !== kind || !text(tag.name)) continue;
    const key = brokenBoardTopicKey(tag);
    const observed = tag.observed_date ?? tag.source_observed_date;
    if (!key || !dated(observed) || observed > membershipDate) continue;
    if (!tags.has(key)) tags.set(key, { ...tag, observed_date: observed, key });
  }
  return [...tags.values()];
}

export function brokenBoardDistribution(
  input: unknown,
  membershipDate: string,
  kind: BrokenBoardMembershipKind,
) {
  if (!Array.isArray(input) || !dated(membershipDate)) return [] as Row[];
  const found = new Map<string, Row>();
  const conflicts = new Set<string>();
  for (const item of input) {
    const row = record(item);
    if (
      !row ||
      row.kind !== kind ||
      !text(row.name) ||
      count(row.observed_count) === null ||
      row.observed_count === 0 ||
      row.topics_date !== membershipDate ||
      (row.membership_date != null &&
        (!dated(row.membership_date) || row.membership_date > membershipDate))
    )
      continue;
    const key = brokenBoardTopicKey(row);
    if (!key) continue;
    if (found.has(key) && found.get(key)?.observed_count !== row.observed_count)
      conflicts.add(key);
    else if (!found.has(key)) {
      const cohort = count(row.cohort_count);
      const share =
        cohort !== null &&
        cohort > 0 &&
        row.observed_count <= cohort &&
        finite(row.sample_share_pct) &&
        row.sample_share_pct >= 0 &&
        row.sample_share_pct <= 100 &&
        Math.abs(row.sample_share_pct - (row.observed_count / cohort) * 100) <=
          0.001
          ? row.sample_share_pct
          : null;
      found.set(key, { ...row, key, sample_share_pct: share });
    }
  }
  for (const key of conflicts) found.delete(key);
  return [...found.values()]
    .sort(
      (a, b) =>
        b.observed_count - a.observed_count || a.key.localeCompare(b.key),
    )
    .slice(0, 10);
}

/** Equal-weight returns belong to this fixed cohort, not the full classification. */
export function brokenBoardDistributionSample(
  input: unknown,
  sampleDate: string,
) {
  const item = record(input) || {};
  const unverified = (reason: string) => ({
    change: null,
    tone: "muted",
    valid: null,
    total: null,
    partial: false,
    reason,
  });
  if (!dated(sampleDate) || item.sample_date !== sampleDate)
    return unverified("该观察日样本涨跌未留存或日期不一致，未用其他日期替代");
  if (!brokenBoardTopicKey(item) || item.sample_basis !== "cohort_equal_weight")
    return unverified("样本身份或等权平均口径待核验");
  const total = count(item.observed_count);
  const valid = count(item.sample_valid_count);
  const missing = count(item.sample_unknown_count);
  if (
    total === null ||
    total === 0 ||
    valid === null ||
    missing === null ||
    valid + missing !== total
  )
    return unverified("样本有效数、待核验数与关联总数不一致");
  const change = item.sample_avg_change_pct;
  if (valid === 0) {
    if (change !== null) return unverified("无可比样本，不应返回平均涨跌幅");
    return {
      change: null,
      tone: "muted",
      valid,
      total,
      partial: true,
      reason: `${sampleDate} · 本组关联股票均缺少可比涨跌；有效0/${total}`,
    };
  }
  if (!finite(change) || change < -100)
    return unverified("可比样本平均涨跌幅未留存或数值无效");
  return {
    change,
    tone: change > 0 ? "up" : change < 0 ? "down" : "neutral",
    valid,
    total,
    partial: missing > 0,
    reason: `${sampleDate} · 本组关联炸板股票等权平均涨跌；有效${valid}/${total}，仅统计可比股票，不随明细筛选改变`,
  };
}

export function brokenBoardMembershipSummary(input: unknown, date: string) {
  const data = record(input);
  if (!data || data.topics_date !== date || !dated(date)) return null;
  const keys = [
    "cohort_count",
    "industry_count",
    "concept_count",
    "stocks_with_industry",
    "stocks_with_concept",
    "unclassified_count",
  ];
  if (keys.some((key) => count(data[key]) === null)) return null;
  if (
    ["stocks_with_industry", "stocks_with_concept", "unclassified_count"].some(
      (key) => data[key] > data.cohort_count,
    )
  )
    return null;
  return data;
}

export function brokenBoardFilterItems(
  items: Row[],
  membershipDate: string,
  selected: { kind: BrokenBoardMembershipKind; key: string } | null,
  board: BrokenBoardBoardFilter = "all",
) {
  if (!selected && board === "all") return items;
  return items.filter(
    (row) =>
      (board === "all" || brokenBoardBoard(row) === board) &&
      (!selected ||
        brokenBoardMembership(row, membershipDate, selected.kind).some(
          (tag) => tag.key === selected.key,
        )),
  );
}

/** An out-of-date history response must never become a new detail day's facts. */
export function brokenBoardDetailContext(
  original: unknown,
  selectedDate: string,
  input: unknown,
) {
  const parent = record(original);
  if (
    !parent ||
    !dated(selectedDate) ||
    !dated(parent.date) ||
    selectedDate > parent.date
  )
    return null;
  return selectedDate === parent.date
    ? brokenBoardContext(parent, selectedDate, parent.snapshot_id)
    : brokenBoardContext(
        record(input)?.broken_board_analysis,
        selectedDate,
        parent.snapshot_id,
      );
}

/** This report belongs to one date and immutable publication, never just a route. */
export function brokenBoardContext(
  input: unknown,
  date: string,
  snapshotId?: unknown,
): Row | null {
  const data = record(input);
  if (
    !data ||
    !dated(date) ||
    data.version !== BROKEN_BOARD_VERSION ||
    data.date !== date ||
    (snapshotId !== undefined &&
      (typeof snapshotId !== "string" || data.snapshot_id !== snapshotId))
  )
    return null;
  const current = record(data.current);
  if (
    current &&
    ((current.date !== undefined && current.date !== date) ||
      (current.snapshot_id !== undefined &&
        current.snapshot_id !== data.snapshot_id))
  )
    return null;
  return data;
}

export function brokenBoardPeriod(input: unknown) {
  const data = record(input) || {};
  const up = count(data.limit_up_count);
  const broken = count(data.broken_count);
  const touch = count(data.touch_count);
  const complete =
    data.state === "complete" &&
    up !== null &&
    broken !== null &&
    touch !== null &&
    touch === up + broken;
  const ratio =
    complete &&
    touch > 0 &&
    finite(data.broken_rate_pct) &&
    data.broken_rate_pct >= 0 &&
    data.broken_rate_pct <= 100 &&
    Math.abs(data.broken_rate_pct - (broken / touch) * 100) <= 0.001
      ? data.broken_rate_pct
      : null;
  const conflictingRatio =
    complete && touch > 0 && data.broken_rate_pct != null && ratio === null;
  const state =
    complete && !conflictingRatio
      ? "complete"
      : data.state === "partial" || data.state === "complete"
        ? "partial"
        : "unknown";
  return {
    date: dated(data.date) ? data.date : null,
    state,
    label:
      state === "complete"
        ? "完整同口径"
        : state === "partial"
          ? "部分覆盖"
          : "待核验",
    up: state === "complete" ? up : null,
    broken: state === "complete" ? broken : null,
    touch: state === "complete" ? touch : null,
    rate: state === "complete" ? ratio : null,
    observedUp: count(data.observed_limit_up_count),
    observedBroken: count(data.observed_broken_count),
    source: text(data.source),
    scopeKey: text(data.scope_key),
    reason:
      text(data.reason) ||
      (conflictingRatio
        ? "家数与比例不一致，完整比例待核验。"
        : state !== "complete"
          ? "同日同源两池、价格或覆盖证据未齐，不能计算完整炸板比例。"
          : touch === 0
            ? "完整两池没有触板样本，分母为0，不计算比例。"
            : null),
  };
}

/** Keep missing periods visible; a table does not connect across missing days. */
export function brokenBoardHistory(
  input: unknown,
  date: string,
  snapshotId?: unknown,
) {
  if (!Array.isArray(input) || !dated(date)) return [];
  const days = new Map<string, Row[]>();
  for (const item of input) {
    const row = record(item);
    if (!row || !dated(row.date) || row.date > date) continue;
    days.set(row.date, [...(days.get(row.date) || []), row]);
  }
  return [...days]
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-7)
    .map(([day, rows]) => ({
      ...brokenBoardPeriod(
        rows.length === 1 &&
          (snapshotId === undefined || rows[0].snapshot_id === snapshotId)
          ? rows[0]
          : {
              state: "unknown",
              reason:
                rows.length !== 1
                  ? "该日返回重复统计记录，未选择其中一条替代。"
                  : "该日统计的行情代际不符，未混入当前结果。",
            },
      ),
      date: day,
    }));
}

const stateLabels: Record<string, string> = {
  limit_up: "涨停反包",
  limit_break: "今日炸板池",
  broken: "今日炸板池",
  limit_down: "今日收盘跌停",
  not_listed: "未进入今日三池",
  not_limit_up: "今日确认未涨停",
  conflict: "来源冲突",
  unknown: "待核验",
};

export function brokenBoardPriceFeedback(input: unknown) {
  const row = record(input) || {};
  const feedback = record(row.price_feedback);
  const change = finite(row.change_pct) ? row.change_pct : null;
  const unknown = {
    code: "unknown",
    label: "价格待核验",
    tone: "muted",
    reason: text(feedback?.reason),
  };
  if (!feedback || change === null || feedback.change_pct !== change)
    return unknown;
  const expected =
    change <= -5
      ? "large_drop"
      : change < 0
        ? "negative"
        : change > 0
          ? "positive"
          : "flat";
  if (feedback.code !== expected || feedback.threshold_pct !== -5)
    return unknown;
  return {
    code: expected,
    label: (
      {
        positive: "正反馈",
        negative: "负反馈",
        large_drop: "大面",
        flat: "平盘",
      } as Record<string, string>
    )[expected],
    tone: change > 0 ? "up" : change < 0 ? "down" : "muted",
    reason: text(feedback.reason),
  };
}

export function brokenBoardCurrentItems(
  input: unknown,
  date: string,
  snapshotId?: unknown,
) {
  const current = record(input) || {};
  const source: Row[] = Array.isArray(current.items)
    ? current.items.filter((row: unknown) => record(row))
    : [];
  const counts = new Map<string, number>();
  for (const row of source) {
    if (typeof row.instrument_id === "string")
      counts.set(row.instrument_id, (counts.get(row.instrument_id) || 0) + 1);
  }
  return source
    .filter(
      (row) =>
        row.outcome === "broken" &&
        typeof row.instrument_id === "string" &&
        /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id) &&
        counts.get(row.instrument_id) === 1 &&
        (snapshotId === undefined || row.snapshot_id === snapshotId) &&
        (row.date == null || row.date === date) &&
        (row.quote_date == null || row.quote_date === date),
    )
    .map((row) => ({
      ...row,
      code: row.instrument_id.slice(0, 6),
      change_pct: finite(row.change_pct) ? row.change_pct : null,
      drawdown_from_high_pct:
        finite(row.drawdown_from_high_pct) &&
        row.drawdown_from_high_pct <= 0 &&
        row.drawdown_from_high_pct >= -100
          ? row.drawdown_from_high_pct
          : null,
      current_state_label: "收盘未封住",
    }));
}

export function brokenBoardDrawdown(input: unknown) {
  const current = record(input) || {};
  const valid = count(current.drawdown_valid_count);
  const large = count(current.large_drawdown_count);
  return {
    valid,
    large: valid !== null && large !== null && large <= valid ? large : null,
    average:
      valid !== null &&
      valid > 0 &&
      finite(current.avg_drawdown_from_high_pct) &&
      current.avg_drawdown_from_high_pct <= 0 &&
      current.avg_drawdown_from_high_pct >= -100
        ? current.avg_drawdown_from_high_pct
        : null,
  };
}

export function brokenBoardFeedback(
  input: unknown,
  date: string,
  snapshotId?: unknown,
) {
  const data = record(input) || {};
  const valid =
    dated(date) &&
    data.date === date &&
    (snapshotId === undefined || data.snapshot_id === snapshotId) &&
    dated(data.previous_date) &&
    data.previous_date < date;
  const sourceItems: Row[] =
    valid && Array.isArray(data.items)
      ? data.items.filter((row: unknown) => record(row))
      : [];
  const identityCount = new Map<string, number>();
  for (const row of sourceItems) {
    const id = text(row.instrument_id);
    if (id) identityCount.set(id, (identityCount.get(id) || 0) + 1);
  }
  const items = sourceItems
    .filter(
      (row) =>
        typeof row.instrument_id === "string" &&
        /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id) &&
        identityCount.get(row.instrument_id) === 1 &&
        (snapshotId === undefined || row.snapshot_id === snapshotId) &&
        (row.date == null || row.date === date) &&
        (row.quote_date == null || row.quote_date === date),
    )
    .map((row) => ({
      ...row,
      code: row.instrument_id.slice(0, 6),
      change_pct: finite(row.change_pct) ? row.change_pct : null,
      current_state: stateLabels[row.current_state]
        ? row.current_state
        : "unknown",
      current_state_label: stateLabels[row.current_state] || "待核验",
    }));
  const readCount = (key: string) => (valid ? count(data[key]) : null);
  const source = readCount("source_count");
  const verified = readCount("verified_count");
  const validCount = readCount("valid_count");
  const limitUp = readCount("limit_up_count");
  const limitDown = readCount("limit_down_count");
  // Price comparability and closing-event proof are independent. A price gap
  // can make the report partial while the complete cohort's event rate is known.
  const completeEvents =
    valid &&
    source !== null &&
    source === verified &&
    limitUp !== null &&
    limitUp <= source &&
    (limitDown === null || limitUp + limitDown <= source);
  const rate =
    completeEvents &&
    verified !== null &&
    verified > 0 &&
    finite(data.return_limit_rate_pct) &&
    data.return_limit_rate_pct >= 0 &&
    data.return_limit_rate_pct <= 100 &&
    Math.abs(data.return_limit_rate_pct - (limitUp / verified) * 100) <= 0.001
      ? data.return_limit_rate_pct
      : null;
  return {
    valid,
    previousDate: valid ? data.previous_date : null,
    state: valid ? data.state : "unknown",
    source,
    observedSource: readCount("observed_source_count"),
    verified,
    validCount,
    unknown: readCount("unknown_count"),
    upUnknown: readCount("event_up_unknown_count"),
    downUnknown: readCount("event_down_unknown_count"),
    observedLimitUp: readCount("observed_limit_up_count"),
    observedLimitDown: readCount("observed_limit_down_count"),
    up: readCount("up_count"),
    down: readCount("down_count"),
    flat: readCount("flat_count"),
    average:
      valid &&
      validCount !== null &&
      validCount > 0 &&
      finite(data.avg_change_pct)
        ? data.avg_change_pct
        : null,
    median:
      valid &&
      validCount !== null &&
      validCount > 0 &&
      finite(data.median_change_pct)
        ? data.median_change_pct
        : null,
    largeDrop: readCount("large_drop_count"),
    limitUp,
    limitDown,
    returnLimitRate: rate,
    items,
    hiddenItems: sourceItems.length - items.length,
    reason:
      text(data.reason) ||
      (!valid
        ? "严格前一交易日炸板样本尚未核验，不能用其他日期代替。"
        : source === 0
          ? "昨日完整来源没有炸板样本，不计算价格反馈或涨停反包比例。"
          : null),
  };
}

export function brokenBoardStrings(input: unknown): string[] {
  return Array.isArray(input)
    ? input.map(text).filter((value): value is string => value !== null)
    : [];
}
