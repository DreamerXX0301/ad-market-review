import type { Row } from "../../lib/api";

export type LadderTopic = {
  key: string;
  name: string;
  topic_id?: string;
  kind?: string;
  member_lookup?: Row;
  [key: string]: unknown;
};
const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;

const patternLabels: Record<string, string> = {
  earth_to_sky: "地天板",
  one_price_limit_up: "一字涨停",
  one_price_limit_down: "一字跌停",
};
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const isoDay = (value: string) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
};

/** A card only describes backend-confirmed events for its selected generation/day. */
export function ladderPatternTags(
  row: Row,
  observationDate: string,
  snapshotId: unknown,
) {
  if (
    !isoDay(observationDate) ||
    row.date !== observationDate ||
    row.pattern_date !== observationDate ||
    row.pattern_as_of !== observationDate ||
    typeof snapshotId !== "string" ||
    !snapshotId ||
    row.snapshot_id !== snapshotId
  )
    return [];
  const found = new Map<
    string,
    { kind: string; label: string; detail: string }
  >();
  for (const event of Array.isArray(row.pattern_events)
    ? row.pattern_events
    : []) {
    const label =
      event && Object.prototype.hasOwnProperty.call(patternLabels, event.kind)
        ? patternLabels[event.kind]
        : null;
    if (
      !label ||
      event.basis !== "verified-source-close-pool-and-raw-ohlc-v1" ||
      event.date !== observationDate ||
      event.bar_date !== observationDate ||
      event.snapshot_id !== snapshotId ||
      event.raw_price_basis !== "none" ||
      !positive(event.raw_volume) ||
      !["open", "high", "low", "close"].every((field) =>
        positive(event.raw_ohlc?.[field]),
      ) ||
      row.pattern_status?.[event.kind]?.state !== true ||
      row.pattern_status?.[event.kind]?.status !== "confirmed"
    )
      continue;
    const basis =
      event.kind === "earth_to_sky"
        ? "当日最低成交触及派生跌停参考价，收盘经来源池核验为涨停；不还原首次触板或开板回封路径"
        : "原始日K开高低收同价且收盘入已核验来源池；不代表盘口持续封死";
    found.set(event.kind, {
      kind: event.kind,
      label,
      detail: `${observationDate} · ${basis} · 来源：${event.source || "已核验收盘池与原始日K"}`,
    });
  }
  return [...found.values()];
}

/** Do not promote a missing streak to a first board, or collapse distinct IDs. */
export function ladderTopics(row: Row): LadderTopic[] {
  const topics = Array.isArray(row.topics) ? row.topics : [];
  const found = new Map<string, LadderTopic>();
  for (const topic of topics) {
    const value = typeof topic === "string" ? { name: topic } : topic;
    if (!value || typeof value !== "object") continue;
    const name =
      typeof value.name === "string"
        ? value.name.trim()
        : typeof value.topic_name === "string"
          ? value.topic_name.trim()
          : "";
    if (!name) continue;
    const id =
      typeof value.topic_id === "string" && value.topic_id
        ? value.topic_id
        : undefined;
    const key = id ? "id:" + id : "name:" + name;
    if (!found.has(key)) found.set(key, { ...value, key, name, topic_id: id });
  }
  return [...found.values()];
}

export function groupLadderRows(rows: Row[]) {
  const groups = new Map<number | null, Row[]>();
  for (const row of rows) {
    const height =
      count(row.board_count) && row.board_count > 0 ? row.board_count : null;
    groups.set(height, [...(groups.get(height) || []), row]);
  }
  return [...groups.entries()]
    .sort(([left], [right]) =>
      left == null
        ? right == null
          ? 0
          : 1
        : right == null
          ? -1
          : right - left,
    )
    .map(([height, items]) => ({ height, items }));
}

export type LadderSearchNames = Record<string, readonly [string, string]>;

function normalizeSearch(value: unknown) {
  return typeof value === "string"
    ? value
        .normalize("NFKC")
        .toLowerCase()
        .normalize("NFD")
        .replace(/u\u0308|u:/g, "v")
        .replace(/[^\p{L}\p{N}]/gu, "")
    : "";
}

function oneEdit(left: string, right: string) {
  if (Math.abs(left.length - right.length) > 1) return false;
  if (left.length > right.length) [left, right] = [right, left];
  let index = 0;
  while (index < left.length && left[index] === right[index]) index++;
  if (left.length !== right.length)
    return left.slice(index) === right.slice(index + 1);
  return (
    left.slice(index + 1) === right.slice(index + 1) ||
    (left[index] === right[index + 1] &&
      left[index + 1] === right[index] &&
      left.slice(index + 2) === right.slice(index + 2))
  );
}

/** Match only labels supplied by the dated view. Codes and reasons use literal inclusion. */
export function ladderSearch(query: string, names?: LadderSearchNames) {
  const term = normalizeSearch(query);
  const han = /^\p{Script=Han}{3,40}$/u.test(term);
  const latin = /^[a-z][a-z0-9]+$/.test(term) && term.length <= 80;
  const contains = (value: unknown) => normalizeSearch(value).includes(term);
  return {
    term,
    contains,
    results<T>(items: T[], match: (item: T, typo: boolean) => boolean) {
      if (!term) return items;
      const direct = items.filter((item) => match(item, false));
      return direct.length ? direct : items.filter((item) => match(item, true));
    },
    label(value: unknown, typo = false) {
      if (typeof value !== "string") return false;
      const label = normalizeSearch(value);
      if (label.includes(term)) return true;
      const key = value.trim();
      if (latin && names && Object.hasOwn(names, key)) {
        const [pinyin, initials] = names[key].map(normalizeSearch);
        if (
          pinyin.startsWith(term) ||
          initials.startsWith(term) ||
          (term.length >= 4 && pinyin.includes(term)) ||
          (typo && /^[a-z]{5,}$/.test(term) && oneEdit(term, pinyin))
        )
          return true;
      }
      return (
        typo &&
        han &&
        /^\p{Script=Han}{2,41}$/u.test(label) &&
        oneEdit(term, label)
      );
    },
  };
}

export function filterLadderRows(
  rows: Row[],
  query: string,
  topic: LadderTopic | null,
  names?: LadderSearchNames,
) {
  const search = ladderSearch(query, names);
  const candidates = rows.flatMap((row) => {
    const topics = ladderTopics(row);
    return !topic ||
      topics.some((value) =>
        topic.topic_id
          ? value.topic_id === topic.topic_id
          : value.name === topic.name,
      )
      ? [{ row, topics }]
      : [];
  });
  return search
    .results(
      candidates,
      ({ row, topics }, typo) =>
        search.label(row.name, typo) ||
        search.label(row.source_industry, typo) ||
        topics.some((value) => search.label(value.name, typo)) ||
        [row.code, row.instrument_id, row.reason].some(search.contains),
    )
    .map(({ row }) => row);
}

export function ladderClassificationCount(
  rows: Row[],
  coverage: Row | null | undefined,
): number | null {
  if (rows.length) return rows.length;
  return coverage &&
    count(coverage.examined_count) &&
    coverage.examined_count > 0 &&
    coverage.complete === true &&
    coverage.confirmed_count === 0 &&
    coverage.unknown_count === 0
    ? 0
    : null;
}

export function themeCountText(theme: Row): string {
  if (count(theme.limit_up_count)) return `${theme.limit_up_count}只`;
  return count(theme.limit_up_observed_count) &&
    theme.limit_up_observed_count > 0
    ? `已确认≥${theme.limit_up_observed_count}只`
    : "数量待核验";
}
