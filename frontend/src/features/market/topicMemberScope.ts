import type { Row } from "../../lib/api";
import { topicMembersRequest } from "./marketPresentation";

export type TopicMemberScope = "historical" | "latest";

/** Current constituents require a retained provider identity, never a name guess. */
export function latestMemberTopicCode(topic: Row): string | null {
  for (const value of [
    topic.topic_code,
    topic.source_code,
    topic.topic_id,
    topic.membership_topic_id,
    topic.member_lookup?.source_code,
    topic.member_lookup?.topic_id,
  ]) {
    if (typeof value !== "string") continue;
    const code = value.replace(/^fuyao-/i, "").toUpperCase();
    if (/^\d{6}\.TI$/.test(code)) return code;
  }
  return null;
}

export function topicMemberScopeRequest(
  topic: Row,
  date: string,
  scope: TopicMemberScope,
): string | null {
  if (scope === "historical") return topicMembersRequest(topic, date);
  const code = latestMemberTopicCode(topic);
  // Omit the old view date: latest must use the server's current business day.
  return code
    ? `/sectors/${encodeURIComponent(code)}/members?membership_scope=latest`
    : null;
}

export function memberQuoteDate(
  data: Row | null,
  date: string,
  scope: TopicMemberScope,
): string | null {
  if (scope === "historical") return date;
  const value = data?.quote_as_of;
  if (data?.membership_scope !== "latest" || typeof value !== "string")
    return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = new Date(value + "T00:00:00Z");
  return Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
    ? value
    : null;
}
