import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import {
  firstGainersReadPath,
  historicalPrefetchBusy,
  prefetchMarketReads,
  subscribeReadChanges,
  usePageActive,
} from "../../lib/api";

type MarketPrefetch = {
  date: string;
  sourcePath: string;
  currentPaths: string[];
  marketSnapshotId: string;
};

export const MarketPrefetchContext = createContext<MarketPrefetch | null>(null);

export function marketPrimaryReadPath(route: string, date: string) {
  switch (route) {
    case "market":
      return `/market/overview?date=${date}`;
    case "gainers":
      return firstGainersReadPath(date);
    case "abnormal":
      return `/market/abnormal?monitor_scope=latest&limit=1000&date=${date}`;
    case "emotion":
    case "sectors":
    case "ladder":
      return `/market/${route}?date=${date}&limit=1000`;
    case "auction":
    case "intraday":
    case "noon":
    case "news-events":
    case "indices":
    case "dragon-tiger":
      return `/market/${route}?date=${date}`;
    default:
      return null;
  }
}

export function sameDayMarketPaths(
  routes: Iterable<string>,
  sourceRoute: string,
  date: string,
  viewDate: (route: string) => string,
) {
  return [...routes].flatMap((route) => {
    if (route === sourceRoute || viewDate(route) !== date) return [];
    const path = marketPrimaryReadPath(route, date);
    return path ? [path] : [];
  });
}

const quietMs = 1_800;
const retryMs = 90_000;
const activity = [
  "pointerdown",
  "keydown",
  "wheel",
  "touchstart",
  "click",
  "input",
  "change",
  "focusin",
  "focusout",
] as const;
const captureScopes = new Set([
  "market",
  "calendar",
  "tasks",
  "auction",
  "noon",
  "intraday",
]);

export function previousPublishedDays(date: string, published?: string[]) {
  if (!Array.isArray(published) || !published.includes(date)) return [];
  return [...new Set(published)]
    .filter(
      (day) =>
        typeof day === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(day) &&
        day < date,
    )
    .sort()
    .reverse()
    .slice(0, 2);
}

export function outsideAuctionWindow(now: Date) {
  const minutes = ((now.getUTCHours() + 8) % 24) * 60 + now.getUTCMinutes();
  return minutes < 9 * 60 + 23 || minutes >= 9 * 60 + 30;
}

function availableIdleInput() {
  const { scheduling, connection } = navigator as Navigator & {
    scheduling?: { isInputPending?: () => boolean };
    connection?: { saveData?: boolean; effectiveType?: string };
  };
  return (
    document.visibilityState === "visible" &&
    outsideAuctionWindow(new Date()) &&
    !connection?.saveData &&
    !["slow-2g", "2g", "3g"].includes(connection?.effectiveType || "") &&
    !scheduling?.isInputPending?.() &&
    !document.activeElement?.matches(
      "input, textarea, [contenteditable='true']",
    )
  );
}

export function useHistoricalPrefetch(
  ready: boolean,
  path: string | null,
  date: string,
  snapshotId: unknown,
  published?: string[],
  foregroundSettled = ready,
) {
  const active = usePageActive();
  const plan = useContext(MarketPrefetchContext);
  const days = useMemo(
    () => (ready ? previousPublishedDays(date, published) : []),
    [ready, date, published],
  );
  const lastAttempt = useRef<{ key: string; expires: number } | null>(null);
  useEffect(() => {
    if (
      !active ||
      !path ||
      !foregroundSettled ||
      !plan ||
      plan.date !== date ||
      plan.sourcePath !== path ||
      (!plan.currentPaths.length && !days.length)
    )
      return;
    const key = JSON.stringify([
      plan.marketSnapshotId,
      path,
      snapshotId,
      days,
      plan.currentPaths,
    ]);
    const coolingDown = () =>
      lastAttempt.current?.key === key &&
      lastAttempt.current.expires > performance.now();

    let timer: number | undefined;
    let idle: number | undefined;
    let work: AbortController | undefined;
    let deferrals = 0;
    const cancelScheduled = () => {
      window.clearTimeout(timer);
      if (idle !== undefined) window.cancelIdleCallback(idle);
    };
    const run = () => {
      if (coolingDown() || !availableIdleInput()) return;
      if (historicalPrefetchBusy()) {
        if (deferrals++ < 3) schedule();
        return;
      }
      const attempt = new AbortController();
      work = attempt;
      void prefetchMarketReads(
        path,
        plan.currentPaths,
        days,
        plan.marketSnapshotId,
        attempt.signal,
        availableIdleInput,
      )
        .then((completed) => {
          if (attempt.signal.aborted) return;
          if (completed)
            lastAttempt.current = { key, expires: performance.now() + retryMs };
          else if (historicalPrefetchBusy() && deferrals++ < 3) schedule();
        })
        .catch(() => {
          /* An unused prefetch can always fall back to a normal page read. */
        })
        .finally(() => {
          if (work === attempt) work = undefined;
        });
    };
    const schedule = () => {
      cancelScheduled();
      work?.abort();
      if (document.hidden) return;
      timer = window.setTimeout(() => {
        if (typeof window.requestIdleCallback === "function")
          idle = window.requestIdleCallback(run);
        else run();
      }, quietMs);
    };
    const reschedule = () => {
      deferrals = 0;
      schedule();
    };

    const unsubscribe = subscribeReadChanges((changes) => {
      if (changes.some(({ scope }) => captureScopes.has(scope))) {
        lastAttempt.current = null;
        reschedule();
      }
    });
    for (const event of activity)
      document.addEventListener(event, reschedule, {
        capture: true,
        passive: true,
      });
    document.addEventListener("visibilitychange", reschedule);
    schedule();
    return () => {
      cancelScheduled();
      work?.abort();
      unsubscribe();
      for (const event of activity)
        document.removeEventListener(event, reschedule, true);
      document.removeEventListener("visibilitychange", reschedule);
    };
  }, [active, path, date, days, snapshotId, foregroundSettled, plan]);
}
