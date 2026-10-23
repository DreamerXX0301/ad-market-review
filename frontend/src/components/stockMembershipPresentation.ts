type RecordValue = Record<string, any>;

/** Current membership is separate from the chart date and historical reasons. */
export function stockCurrentMemberships(
  data: RecordValue | null,
  instrumentId: string,
) {
  const actual = data?.instrument?.instrument_id || data?.instrument_id;
  const matches =
    typeof actual === "string" &&
    actual.toUpperCase() === instrumentId.toUpperCase();
  const known = matches && Array.isArray(data?.current_topics);
  const seen = new Set<string>();
  const tags: RecordValue[] = known
    ? data!.current_topics.filter((tag: unknown): tag is RecordValue => {
        if (!tag || typeof tag !== "object" || Array.isArray(tag)) return false;
        const row = tag as RecordValue;
        if (typeof row.name !== "string" || !row.name.trim()) return false;
        const key =
          typeof row.topic_id === "string"
            ? row.topic_id
            : `${row.kind}:${row.name}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
    : [];
  const industries = tags.filter((tag) => tag.kind === "industry");
  const others = tags.filter((tag) => tag.kind !== "industry");
  return {
    known,
    visible: [...industries.slice(0, 1), ...others.slice(0, 3)],
    additional: [...industries.slice(1), ...others.slice(3)],
    observed:
      known && typeof data!.current_topics_observed_date === "string"
        ? data!.current_topics_observed_date
        : null,
    scope:
      known && typeof data!.current_topics_scope === "string"
        ? data!.current_topics_scope
        : "当前本地归属，不代表历史成员或当日上涨原因；缺少归属不能推断不属于。",
  };
}
