import type { Row } from "../../lib/api";
import { auctionPreviousHeight } from "./auctionHeightPresentation";

const BOARD_NAMES: Record<string, string> = {
  main: "主板",
  chinext: "创业板",
  star: "科创板",
  beijing: "北交所",
};
const clean = (value: unknown) =>
  typeof value === "string" ? value.trim() : "";

/** Only the frozen previous-day evidence can create an event badge. */
export function auctionStockEvent(row: Row) {
  const reasons = Array.isArray(row.reasons)
    ? row.reasons
        .filter((value: unknown) => typeof value === "string")
        .map(clean)
    : [];
  const up = reasons.includes("严格前日已核验涨停");
  const broken = reasons.includes("严格前日已核验炸板");
  const state = row.previous_state;
  if (
    (state != null && state !== "limit_up" && state !== "limit_break") ||
    (up && broken) ||
    (state === "limit_up" && broken) ||
    (state === "limit_break" && up)
  )
    return null;
  const height = auctionPreviousHeight(row);
  if (broken && height != null) return null;
  if (state === "limit_break" || broken)
    return { kind: "limit_break", label: "昨日炸板" } as const;
  if (height != null) return { kind: "height", label: null } as const;
  if (state === "limit_up" || up)
    return { kind: "limit_up", label: "昨日涨停" } as const;
  return null;
}

/** Never infer a listing board from a code or an arbitrary provider label. */
export function auctionStockBoard(row: Row) {
  const known =
    typeof row.board === "string" && Object.hasOwn(BOARD_NAMES, row.board);
  return {
    key: known ? row.board : "unknown",
    label: known ? BOARD_NAMES[row.board] : "板块待核验",
    title: [
      clean(row.board_scope) ||
        "当前证券代码与交易所分类，不证明历史上市板块身份",
      clean(row.board_reason),
    ]
      .filter(Boolean)
      .join("；"),
  };
}
