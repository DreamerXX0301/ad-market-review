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

export function filterLadderRows(
  rows: Row[],
  query: string,
  topic: LadderTopic | null,
) {
  const search = query.trim().toLocaleLowerCase();
  return rows.filter((row) => {
    const topics = ladderTopics(row);
    const matchesTopic =
      !topic ||
      topics.some((value) =>
        topic.topic_id
          ? value.topic_id === topic.topic_id
          : value.name === topic.name,
      );
    return (
      matchesTopic &&
      (!search ||
        [
          row.name,
          row.code,
          row.instrument_id,
          row.reason,
          ...topics.map((value) => value.name),
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase()
          .includes(search))
    );
  });
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
