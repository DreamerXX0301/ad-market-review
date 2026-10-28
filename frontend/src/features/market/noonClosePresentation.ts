import type { Row } from "../../lib/api";

export const CLOSE_SECTIONS = [
  {
    key: "overview",
    title: "整体变化",
    question: "午后整体发生了什么变化？",
    section: "events",
  },
  {
    key: "events",
    title: "封板与修复",
    question: "哪些回封，哪些收盘未封？",
    section: "events",
  },
  {
    key: "relay",
    title: "接力结果",
    question: "昨日各高度最后是否晋级？",
    section: "relay",
  },
  {
    key: "themes",
    title: "行业概念",
    question: "变化集中在哪些方向？",
    section: "events",
  },
  {
    key: "watch",
    title: "原关注核验",
    question: "午间实际关注的股票最后怎样？",
    section: "watch",
  },
  {
    key: "tomorrow",
    title: "明日衔接",
    question: "下一交易日带着哪些事实继续看？",
    section: "tomorrow",
  },
] as const;
export type CloseSection = (typeof CLOSE_SECTIONS)[number]["key"];
export type CloseSort =
  | "final_board_count"
  | "afternoon_change_pct"
  | "close_change_pct"
  | "noon_change_pct"
  | "amount"
  | "name";
export const CLOSE_TRANSITIONS = [
  ["all", "全部"],
  ["kept", "封板保持"],
  ["resealed", "炸板回封"],
  ["opened", "收盘未封"],
  ["new_limit_up", "未封→涨停"],
  ["down_opened", "跌停打开"],
  ["new_limit_down", "新增跌停"],
  ["unknown", "待核验"],
] as const;
const labels: Record<string, string> = {
  kept: "午间封板 → 收盘仍封",
  resealed: "午间炸板 → 收盘回封",
  opened: "午间封板 → 收盘未封",
  new_limit_up: "午间未封 → 收盘涨停",
  down_opened: "午间跌停 → 收盘打开",
  new_limit_down: "收盘新增跌停",
  not_resealed: "午间炸板 → 收盘未封",
  unchanged: "未见已确认池变化",
  unknown: "结果待核验",
  conflict: "来源冲突待核验",
};
export function closeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
export function closeCountTone(
  value: unknown,
  positive: "up" | "down",
): string {
  const n = closeNumber(value);
  return n == null || n === 0 ? "muted" : positive;
}
export function closeRows(value: unknown): Row[] {
  return Array.isArray(value)
    ? value.filter((x) => x && typeof x === "object" && !Array.isArray(x))
    : [];
}
export function closeTransitionLabel(row: Row): string {
  return row.close_state === "conflict"
    ? labels.conflict
    : labels[row.transition] || labels.unknown;
}
/** State color describes the event; signed price values retain their own tone. */
export function closeTransitionTone(row: Row): string {
  if (
    ["unknown", "conflict"].includes(row.close_state) ||
    row.transition === "unknown"
  )
    return "close-warn";
  if (
    ["kept", "resealed", "new_limit_up", "down_opened"].includes(row.transition)
  )
    return "up";
  if (
    ["opened", "new_limit_down"].includes(row.transition) ||
    row.close_state === "limit_down"
  )
    return "down";
  return "muted";
}
export function closeStateLabel(row: Row): string {
  if (row.close_state === "limit_up") {
    const n = closeNumber(row.final_board_count);
    if (row.relay_state === "height_conflict")
      return "收盘涨停 · 板数关系待核验";
    return n != null && n >= 1
      ? n === 1
        ? "收盘首板"
        : "收盘" + n + "连板"
      : "收盘涨停 · 板数待核验";
  }
  return (
    (
      {
        limit_down: "收盘跌停",
        limit_break: "收盘炸板未封",
        not_listed: "收盘未入已核验三池",
        conflict: "来源冲突",
        unknown: "待核验",
      } as Record<string, string>
    )[row.close_state] || "待核验"
  );
}
export function closeDayEventLabel(event: unknown): string {
  const key =
    typeof event === "string"
      ? event
      : event && typeof event === "object"
        ? (event as Row).kind
        : "";
  return (
    (
      {
        ever_broken: "日内曾炸板",
        closing_break: "收盘炸板未封",
        break_event_semantics_unknown: "炸板记录口径待核验",
      } as Record<string, string>
    )[key] || "来源日内事件"
  );
}
export function closeCopyCodes(values: unknown): string {
  if (!Array.isArray(values)) return "";
  const codes = new Set<string>();
  for (const value of values) {
    if (typeof value !== "string") continue;
    if (/^\d{6}$/.test(value)) codes.add(value);
    else if (/^\d{6}\.(SH|SZ|BJ)$/.test(value)) codes.add(value.slice(0, 6));
  }
  return [...codes].join("\n");
}
export function closeItemsPath(options: {
  date: string;
  reviewId: string;
  section: string;
  transition?: string;
  board?: string;
  sort?: CloseSort;
  direction?: string;
  cursor?: string;
  classification?: string;
  codesOnly?: boolean;
}) {
  const q = new URLSearchParams({
    date: options.date,
    review_id: options.reviewId,
    section: options.section,
    transition: options.transition || "all",
    board: options.board || "all",
    sort: options.sort || "final_board_count",
    direction: options.direction || "desc",
    limit: "50",
  });
  if (options.cursor && !options.codesOnly) q.set("cursor", options.cursor);
  if (options.classification) q.set("classification", options.classification);
  if (options.codesOnly) q.set("codes_only", "true");
  return "/market/noon/close-review/items?" + q;
}
export function closeMetric(value: unknown) {
  const row =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Row)
      : {};
  return {
    count: closeNumber(row.count),
    source: closeNumber(row.source_count),
    denominator: closeNumber(row.denominator),
    unknown: closeNumber(row.unknown_count),
    rate: closeNumber(row.rate_pct),
  };
}
export const closeStateMessages: Record<string, [string, string]> = {
  waiting_close: [
    "等待当日盘后更新",
    "盘后增量成功并取得同日收盘事实后自动核验；不会重新提交午评采集。",
  ],
  not_collected: [
    "当日未采集午评",
    "仍可正常查看盘后复盘与下一交易日反馈；收盘数据不会反造午间记录。",
  ],
  no_snapshot: [
    "没有有效午评快照",
    "已有任务未形成有效正文，不能补造午间对照；当天提交名额保持原状态。",
  ],
  purged: [
    "该日午评已到期清理",
    "专属收盘回看随午评清理，小台账与清理回执保留。",
  ],
};
