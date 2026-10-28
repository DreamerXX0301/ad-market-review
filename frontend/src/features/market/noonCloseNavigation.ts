type Row = Record<string, any>;

function day(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}

/** The retrospective can show its exact completed day, never the next session. */
export function noonCloseChartDate(
  published: string[],
  viewDate: string,
  requestedDate: string,
): string | null {
  return day(viewDate) &&
    requestedDate === viewDate &&
    published.includes(viewDate)
    ? viewDate
    : null;
}

export function noonCloseSectorSelection(
  row: Row,
  viewDate: string,
  published: string[],
  requestedDate: string,
  view: "chart" | "members" = "chart",
) {
  const date = noonCloseChartDate(published, viewDate, requestedDate);
  const code = row.topic_code || row.instrument_id;
  if (
    !date ||
    typeof code !== "string" ||
    !/^\d{6}\.(TI|SH|SZ)$/.test(code) ||
    !["industry", "concept"].includes(row.kind) ||
    (row.topic_code &&
      row.instrument_id &&
      row.topic_code !== row.instrument_id)
  )
    return null;
  return {
    topic: {
      ...row,
      name: typeof row.name === "string" ? row.name : code,
      topic_code: code,
      topic_id: `fuyao-${code}`,
      kind: row.kind,
      noon_snapshot_date: date,
      noon_chart_as_of: date,
      noon_scope_notice: `来自${date}午评收盘回看；图表最晚截至当日已发布收盘，不包含后续交易日行情；午间归属与收盘成员名单分别按实际日期核验`,
    },
    date,
    view,
  };
}
