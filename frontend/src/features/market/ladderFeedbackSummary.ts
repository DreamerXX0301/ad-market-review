type FeedbackRow = {
  change_pct?: unknown;
  previous_board_count?: unknown;
};

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function summarize(rows: FeedbackRow[]) {
  const changes = rows
    .map((row) => row.change_pct)
    .filter(finite)
    .sort((a, b) => a - b);
  const known = changes.length;
  const middle = Math.floor(known / 2);
  return {
    total: rows.length,
    known,
    unknown: rows.length - known,
    up: changes.filter((change) => change > 0).length,
    down: changes.filter((change) => change < 0).length,
    flat: changes.filter((change) => change === 0).length,
    sharpDecline: changes.filter((change) => change <= -5).length,
    average: known
      ? changes.reduce((sum, change) => sum + change, 0) / known
      : null,
    median: known
      ? known % 2
        ? changes[middle]
        : (changes[middle - 1] + changes[middle]) / 2
      : null,
  };
}

/** Summarize the complete returned sample before table sorting or pagination. */
export function summarizeLadderFeedback(rows: FeedbackRow[]) {
  const groups = new Map<number | null, FeedbackRow[]>();
  for (const row of rows) {
    const raw = row.previous_board_count;
    const height =
      finite(raw) && Number.isInteger(raw) && raw >= 2 ? raw : null;
    const group = groups.get(height) || [];
    group.push(row);
    groups.set(height, group);
  }
  const heights = [...groups]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : b - a))
    .map(([height, group]) => ({ height, ...summarize(group) }));
  return { ...summarize(rows), heights };
}
