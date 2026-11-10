import { useEffect, useMemo, useRef } from "react";
import { prefetchGainersReads, usePageActive } from "../../lib/api";

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
] as const;

export function previousGainersDays(date: string, published?: string[]) {
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

export function useGainersPrefetch(
  ready: boolean,
  date: string,
  snapshotId: unknown,
  published?: string[],
) {
  const active = usePageActive();
  const days = useMemo(
    () => (ready ? previousGainersDays(date, published).join(",") : ""),
    [ready, date, published],
  );
  const lastAttempt = useRef<{ key: string; expires: number } | null>(null);
  useEffect(() => {
    if (!active || !days || typeof snapshotId !== "string" || !snapshotId)
      return;
    const key = `${snapshotId}:${date}:${days}`;
    if (
      lastAttempt.current?.key === key &&
      lastAttempt.current.expires > performance.now()
    )
      return;

    let timer: number | undefined;
    let idle: number | undefined;
    let work: AbortController | undefined;
    const cancelScheduled = () => {
      window.clearTimeout(timer);
      if (idle !== undefined) window.cancelIdleCallback(idle);
    };
    const run = () => {
      if (!availableIdleInput()) return;
      work = new AbortController();
      lastAttempt.current = { key, expires: performance.now() + retryMs };
      void prefetchGainersReads(
        days.split(","),
        snapshotId,
        work.signal,
        availableIdleInput,
      ).catch(() => {
        /* An unused prefetch can always fall back to a normal page read. */
      });
    };
    const schedule = () => {
      cancelScheduled();
      if (work) {
        work.abort();
        return;
      }
      timer = window.setTimeout(() => {
        if (typeof window.requestIdleCallback === "function")
          idle = window.requestIdleCallback(run);
        else run();
      }, quietMs);
    };

    for (const event of activity)
      document.addEventListener(event, schedule, {
        capture: true,
        passive: true,
      });
    document.addEventListener("visibilitychange", schedule);
    schedule();
    return () => {
      cancelScheduled();
      work?.abort();
      for (const event of activity)
        document.removeEventListener(event, schedule, true);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, [active, date, days, snapshotId]);
}
