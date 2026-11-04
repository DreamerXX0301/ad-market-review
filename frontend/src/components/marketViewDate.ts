import { isCalendarDate } from "./marketDate";

export type MarketDateChoice = {
  date: string;
  mode: "manual" | "result";
} | null;

export const isIntradayReview = (route: string) =>
  route === "auction" || route === "noon" || route === "intraday";

/** An explicit selection is shared; automatic defaults follow each page's data scope. */
export function marketViewDate(
  route: string,
  choice: MarketDateChoice,
  latest: string,
  today: string,
): string {
  if (choice && (choice.mode === "manual" || isIntradayReview(route)))
    return choice.date;
  const value = isIntradayReview(route) ? today : latest;
  return isCalendarDate(value) ? value : "";
}

/** Completing a task must not overwrite a date the user deliberately selected. */
export function completedReviewDate(
  choice: MarketDateChoice,
  target: unknown,
  today = "",
): MarketDateChoice {
  if (choice?.mode === "manual") return choice;
  if (target === today) return null;
  return typeof target === "string" && isCalendarDate(target)
    ? { date: target, mode: "result" }
    : choice;
}

/** Keep an existing explicit review link consistent with a date change/reset. */
export function updateReviewDateLink(hash: string, date?: string): string {
  const [route, query = ""] = hash.replace(/^#/, "").split("?", 2);
  const search = new URLSearchParams(query);
  if (!isIntradayReview(route) || !search.has("date")) return hash;
  if (date) search.set("date", date);
  else search.delete("date");
  return `#${route}${search.size ? `?${search}` : ""}`;
}
