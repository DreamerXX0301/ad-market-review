import type { Row } from "../../lib/api";
import { ladderTopics, themeCountText } from "./ladderPresentation";
import type { LadderTopic } from "./ladderPresentation";

export type ThemeKind = "all" | "concept" | "industry";

const STRUCTURAL_TOPIC_NAMES = new Set([
  "融资融券",
  "转融券标的",
  "沪股通",
  "深股通",
  "沪深股通",
  "港股通",
  "沪深300",
  "上证50",
  "中证500",
  "中证1000",
  "科创50",
  "创业板指",
  "北证50",
  "ST",
  "*ST",
  "ST板块",
  "ST概念",
  "注册制次新股",
  "次新股",
  "科创板",
  "创业板",
  "北交所",
  "主板",
]);
export function isStructuralLadderTopic(topic: { name?: unknown }): boolean {
  const name =
    typeof topic.name === "string"
      ? topic.name.replace(/\s/g, "").toUpperCase()
      : "";
  return (
    STRUCTURAL_TOPIC_NAMES.has(name) ||
    name.includes("注册制") ||
    /^(MSCI|富时罗素|标普道琼斯)/.test(name) ||
    /(?:成分股|成份股|指数成分|指数成份)$/.test(name)
  );
}

export const ladderStockKey = (row: Row) =>
  String(row.instrument_id || row.code || "")
    .trim()
    .toUpperCase();

export type LadderDisplayGroup = {
  key: string;
  topic: LadderTopic | null;
  theme: Row | null;
  items: Row[];
  countLabel: string | null;
};

/** A small browsing partition, never a claim about a stock's limit-up cause. */
export function buildLadderDisplayGroups(rows: Row[], themes: Row[]) {
  const unique = new Map<string, Row>();
  rows.forEach((row, index) => {
    const key = ladderStockKey(row) || `unknown:${index}`;
    if (!unique.has(key)) unique.set(key, row);
  });
  const stable = (left: string, right: string) =>
    left < right ? -1 : left > right ? 1 : 0;
  const remaining = new Set([...unique.keys()].sort(stable));
  const candidates = [...ladderThemeIndex(themes)].flatMap(([key, theme]) => {
    const topic = ladderTopics({ topics: [theme] })[0];
    if (!topic || theme.kind !== "concept" || isStructuralLadderTopic(topic))
      return [];
    const members = [...unique]
      .filter(([, row]) => ladderTopics(row).some((value) => value.key === key))
      .map(([id]) => id);
    return [{ key, theme, topic, members }];
  });
  const groups: LadderDisplayGroup[] = [];
  const topicsByInstrument = new Map<string, LadderTopic>();
  for (let step = 0; step < 6; step += 1) {
    const ranked = candidates
      .map((candidate) => ({
        ...candidate,
        available: candidate.members.filter((id) => remaining.has(id)),
      }))
      .filter((candidate) => candidate.available.length >= 2)
      .sort(
        (left, right) =>
          right.available.length - left.available.length ||
          stable(left.key, right.key),
      );
    const next = ranked[0];
    if (!next) break;
    const ids = next.available.sort(stable);
    for (const id of ids) {
      remaining.delete(id);
      topicsByInstrument.set(id, next.topic);
    }
    groups.push({
      key: next.key,
      theme: next.theme,
      topic: next.topic,
      items: ids.map((id) => unique.get(id)!),
      countLabel: themeCountText(next.theme),
    });
  }
  if (remaining.size)
    groups.push({
      key: "other",
      theme: null,
      topic: null,
      items: [...remaining].sort(stable).map((id) => unique.get(id)!),
      countLabel: null,
    });
  return { groups, topicsByInstrument };
}

/** IDs remain authoritative: same-name provider classifications cannot merge. */
export function ladderThemeIndex(themes: Row[]) {
  const index = new Map<string, Row>();
  for (const theme of themes) {
    const topic = ladderTopics({ topics: [theme] })[0];
    if (topic && !index.has(topic.key)) index.set(topic.key, theme);
  }
  return index;
}

export function ladderThemeGroups(
  rows: Row[],
  themes: Row[],
  kind: ThemeKind = "all",
) {
  const index = ladderThemeIndex(themes);
  const grouped = new Map<string, Row[]>();
  for (const row of rows) {
    for (const topic of ladderTopics(row)) {
      if (index.has(topic.key))
        grouped.set(topic.key, [...(grouped.get(topic.key) || []), row]);
    }
  }
  const count = (theme: Row) =>
    typeof theme.limit_up_observed_count === "number" &&
    Number.isFinite(theme.limit_up_observed_count)
      ? theme.limit_up_observed_count
      : typeof theme.limit_up_count === "number" &&
          Number.isFinite(theme.limit_up_count)
        ? theme.limit_up_count
        : -1;
  return [...index]
    .flatMap(([key, theme]) => {
      const topic = ladderTopics({ topics: [theme] })[0] as LadderTopic;
      const items = grouped.get(key) || [];
      return items.length && (kind === "all" || theme.kind === kind)
        ? [{ topic, theme, items, countLabel: themeCountText(theme) }]
        : [];
    })
    .sort(
      (left, right) =>
        count(right.theme) - count(left.theme) ||
        left.topic.name.localeCompare(right.topic.name, "zh-CN") ||
        left.topic.key.localeCompare(right.topic.key),
    );
}
