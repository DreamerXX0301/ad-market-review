/** Presentation only: cross-session aggregates are computed from complete frozen samples on the server. */
type WorkbenchRow = Record<string, any>;
export type NoonFeedbackFilter = "all" | "first" | "multi" | "unknown";

export function noonConfirmedHeight(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

export function noonFeedbackRows(
  rows: WorkbenchRow[],
  filter: NoonFeedbackFilter = "all",
): WorkbenchRow[] {
  return [...rows]
    .filter((row) => {
      const height = noonConfirmedHeight(row.previous_board_count);
      return filter === "first"
        ? height === 1
        : filter === "multi"
          ? height != null && height >= 2
          : filter === "unknown"
            ? height == null
            : true;
    })
    .sort((a, b) => {
      const ah = noonConfirmedHeight(a.previous_board_count);
      const bh = noonConfirmedHeight(b.previous_board_count);
      return (
        (ah == null ? 1 : 0) - (bh == null ? 1 : 0) ||
        (bh || 0) - (ah || 0) ||
        String(a.instrument_id || "").localeCompare(
          String(b.instrument_id || ""),
        )
      );
    });
}

export function noonLadderRows(rows: WorkbenchRow[]): WorkbenchRow[] {
  return [...rows].sort((a, b) => {
    const ah = noonConfirmedHeight(a.continue_day_cnt ?? a.board_count);
    const bh = noonConfirmedHeight(b.continue_day_cnt ?? b.board_count);
    return (
      (ah == null ? 1 : 0) - (bh == null ? 1 : 0) ||
      (bh || 0) - (ah || 0) ||
      String(a.instrument_id || "").localeCompare(String(b.instrument_id || ""))
    );
  });
}

export function noonFeedbackPage(
  rows: WorkbenchRow[],
  page: number,
  size = 12,
) {
  const pageSize = Number.isInteger(size) && size > 0 ? size : 12;
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.max(
    0,
    Math.min(pages - 1, Number.isInteger(page) ? page : 0),
  );
  return {
    rows: rows.slice(current * pageSize, (current + 1) * pageSize),
    page: current,
    pages,
    total: rows.length,
    start: rows.length ? current * pageSize + 1 : 0,
    end: Math.min(rows.length, (current + 1) * pageSize),
  };
}

export function noonFeedbackState(value: unknown): string {
  const labels: Record<string, string> = {
    limit_up: "本次涨停池",
    limit_break: "本次炸板池",
    limit_down: "本次跌停池",
    not_listed: "本次三池未列入",
    conflict: "池状态冲突",
    unknown: "池状态未知",
  };
  return typeof value === "string"
    ? labels[value] || labels.unknown
    : labels.unknown;
}

export function noonDirectionState(value: unknown): string {
  const labels: Record<string, string> = {
    both_up: "两日均上涨",
    turned_weak: "前日涨 · 午间跌",
    turned_strong: "前日跌 · 午间涨",
    both_down: "两日均下跌",
    flat: "含平盘",
    unknown: "跨日表现未知",
  };
  return typeof value === "string"
    ? labels[value] || "跨日状态待核验"
    : labels.unknown;
}
