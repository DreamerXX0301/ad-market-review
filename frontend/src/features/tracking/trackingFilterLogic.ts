import type { Row } from "../../lib/api";

export type TopicMatch = "matched" | "unmatched" | "unknown";

export function trackingProgress(row: Row): {
  age: number | null;
  duration: number;
  percentage: number | null;
} {
  const validSessions = (value: unknown): value is number =>
    typeof value === "number" && Number.isInteger(value) && value >= 0;
  const age = validSessions(row.trading_age) ? row.trading_age : null;
  const remaining = validSessions(row.remaining_sessions)
    ? row.remaining_sessions
    : null;
  const configuredDuration = row.policy?.duration_sessions;
  const duration =
    validSessions(configuredDuration) && configuredDuration > 0
      ? configuredDuration
      : 30;
  return {
    age,
    duration,
    percentage:
      age === null || remaining === null
        ? null
        : Math.min(100, (age / (age + remaining || duration)) * 100),
  };
}

export function trackingTopicMatch(
  row: Row | undefined,
  selected: string[],
  match: "any" | "all",
  complete: Set<string>,
): TopicMatch {
  if (!selected.length) return "matched";
  if (!Array.isArray(row?.current_topics)) return "unknown";
  const members = new Set(
    row.current_topics.map((topic: Row) => topic.topic_id),
  );
  const present = selected.filter((id) => members.has(id));
  if (match === "any") {
    if (present.length) return "matched";
    return selected.every((id) => complete.has(id)) ? "unmatched" : "unknown";
  }
  if (present.length === selected.length) return "matched";
  return selected.some((id) => !members.has(id) && complete.has(id))
    ? "unmatched"
    : "unknown";
}

export function topicSortValue(row: Row): string | null {
  if (!Array.isArray(row.current_topics) || !row.current_topics.length)
    return null;
  return row.current_topics
    .map((topic: Row) => String(topic.name || topic.topic_id))
    .sort()
    .join("、");
}

export function matchesTrackingText(row: Row, query: string): boolean {
  return [row.name, row.code, row.instrument_id, row.strategy_name]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase()
    .includes(query.trim().toLocaleLowerCase());
}
