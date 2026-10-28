import type { Row } from "../../lib/api";

export type NoonBoard = "main" | "chinext" | "star" | "beijing" | "unknown";
export type NoonBoardFilterValue = "all" | Exclude<NoonBoard, "unknown">;

export const NOON_BOARD_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "main", label: "主板" },
  { value: "chinext", label: "创业板" },
  { value: "star", label: "科创板" },
  { value: "beijing", label: "北交所" },
] as const satisfies readonly { value: NoonBoardFilterValue; label: string }[];

const boardLabels: Record<NoonBoard, string> = {
  main: "主板",
  chinext: "创业板",
  star: "科创板",
  beijing: "北交所",
  unknown: "板别待核验",
};
const text = (value: unknown) =>
  typeof value === "string" ? value.trim().toUpperCase() : "";

export function boardLabel(board: NoonBoard): string {
  return boardLabels[board];
}

/** The code/exchange prefixes mirror backend/app/domain/universe.py.
 * This is a local display classification, not historical identity evidence.
 */
export function boardOf(input: Row | string | null | undefined): NoonBoard {
  const row = typeof input === "string" ? { instrument_id: input } : input;
  if (!row || typeof row !== "object" || Array.isArray(row)) return "unknown";
  const kind = row.kind ?? row.asset_type;
  if (kind != null && kind !== "stock") return "unknown";
  if (Object.prototype.hasOwnProperty.call(row, "board") && row.board != null)
    return ["main", "chinext", "star", "beijing"].includes(row.board)
      ? row.board
      : "unknown";
  const symbol = text(row.instrument_id) || text(row.thscode);
  if (symbol && !/^\d{6}(?:\.(?:SH|SZ|BJ))?$/.test(symbol)) return "unknown";
  const rawCode =
    row.code != null && row.code !== "" ? row.code : symbol.split(".")[0];
  if (rawCode == null || rawCode === "") return "unknown";
  const code =
    typeof rawCode === "string" || typeof rawCode === "number"
      ? String(rawCode).trim().padStart(6, "0")
      : "";
  if (!/^\d{6}$/.test(code)) return "unknown";
  const exchange = text(row.exchange) || symbol.split(".")[1] || "";
  if (
    ["BJ", "BSE", "北京", "北交所"].includes(exchange) ||
    /^(4|8|92)/.test(code)
  )
    return "beijing";
  if (/^(688|689)/.test(code)) return "star";
  if (/^(300|301)/.test(code)) return "chinext";
  if (/^(600|601|603|605|000|001|002|003)/.test(code)) return "main";
  return "unknown";
}

/** Filter only a view's rows; never recalculate or mutate frozen aggregates. */
export function filterRows<T extends Row>(
  rows: T[],
  value: NoonBoardFilterValue,
): T[] {
  return value === "all"
    ? [...rows]
    : rows.filter((row) => boardOf(row) === value);
}

export const noonBoardOf = boardOf;
export const noonFilterBoards = filterRows;
