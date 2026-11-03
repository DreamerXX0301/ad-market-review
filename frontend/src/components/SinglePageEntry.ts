import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { chartSessionDistance } from "./chartSessionDistance";
import { stockMetricDate } from "./stockExtraMetrics";

/** Single scroll page: place the first settled view at the chart, never after user input. */
export function useSinglePageEntry(
  root: RefObject<HTMLDivElement | null>,
  stage: RefObject<HTMLElement | null>,
  token: string,
  enabled: boolean,
  ready: boolean,
) {
  const [height, setHeight] = useState(620);
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const checkRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!enabled || !root.current || !stage.current) return;
    const chart = root.current,
      anchor = stage.current;
    const scroller = chart.closest<HTMLElement>(".entry-page-scroll");
    if (!scroller) return;
    const interactionRoot =
      scroller.closest<HTMLElement>(".stock-drawer,.modal") || scroller;
    let done = false,
      userMoved = false,
      disposed = false;
    let firstFrame = 0,
      secondFrame = 0;
    anchor.dataset.entryAnchor = "waiting";
    const settled = () =>
      readyRef.current &&
      scroller.dataset.detailLoading !== "true" &&
      !chart.querySelector('[data-summary-loading="true"]');
    const stop = () => {
      userMoved = true;
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
      anchor.dataset.entryAnchor = "manual";
    };
    const key = (event: KeyboardEvent) => {
      if (
        [
          "ArrowUp",
          "ArrowDown",
          "PageUp",
          "PageDown",
          "Home",
          "End",
          " ",
          "Tab",
        ].includes(event.key)
      )
        stop();
    };
    const align = () => {
      if (disposed || done || userMoved || !settled()) return;
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
      firstFrame = requestAnimationFrame(() => {
        secondFrame = requestAnimationFrame(() => {
          if (disposed || done || userMoved || !settled()) return;
          const target =
            scroller.scrollTop +
            anchor.getBoundingClientRect().top -
            scroller.getBoundingClientRect().top;
          scroller.scrollTo({ top: Math.max(0, target), behavior: "auto" });
          done = true;
          anchor.dataset.entryAnchor = "positioned";
        });
      });
    };
    const measure = () => {
      if (disposed) return;
      const plot = anchor.querySelector<HTMLElement>(".candle-plot");
      const overhead =
        anchor.getBoundingClientRect().height -
        (plot?.getBoundingClientRect().height || 0);
      const next = Math.max(
        480,
        Math.min(1400, Math.floor(scroller.clientHeight - overhead - 12)),
      );
      setHeight((previous) => (previous === next ? previous : next));
      align();
    };
    checkRef.current = measure;
    const resize = new ResizeObserver(measure);
    resize.observe(scroller);
    resize.observe(chart);
    resize.observe(anchor);
    const attributes = new MutationObserver(measure);
    attributes.observe(scroller, {
      attributes: true,
      subtree: true,
      attributeFilter: ["data-summary-loading", "data-detail-loading"],
    });
    interactionRoot.addEventListener("wheel", stop, {
      capture: true,
      passive: true,
    });
    interactionRoot.addEventListener("touchstart", stop, {
      capture: true,
      passive: true,
    });
    interactionRoot.addEventListener("pointerdown", stop, true);
    interactionRoot.addEventListener("keydown", key, true);
    measure();
    return () => {
      disposed = true;
      checkRef.current = null;
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
      resize.disconnect();
      attributes.disconnect();
      interactionRoot.removeEventListener("wheel", stop, true);
      interactionRoot.removeEventListener("touchstart", stop, true);
      interactionRoot.removeEventListener("pointerdown", stop, true);
      interactionRoot.removeEventListener("keydown", key, true);
    };
  }, [root, stage, enabled, token]);
  useEffect(() => {
    checkRef.current?.();
  }, [ready, height]);
  return height;
}

/** All requested panes stay in one canvas; short windows can still scroll the same page. */
export function singlePagePaneLayout(
  height: number,
  chosen: readonly string[],
  fitToWindow = false,
) {
  const gap = 22;
  const minimumMainHeight = fitToWindow ? 135 : 300;
  const minimums = chosen.map((key) => {
    if (fitToWindow) return key === "boll" ? 70 : key === "volume" ? 32 : 40;
    return key === "boll" ? 145 : key === "volume" ? 96 : 104;
  });
  const paneMinimum = minimums.reduce((sum, value) => sum + value, 0);
  const actualHeight = Math.max(
    480,
    Math.floor(height),
    75 + gap * chosen.length + paneMinimum + minimumMainHeight,
  );
  const available = actualHeight - 75 - gap * chosen.length;
  const mainHeight = chosen.length
    ? Math.min(
        available - paneMinimum,
        Math.max(minimumMainHeight, available * (fitToWindow ? 0.55 : 0.62)),
      )
    : available;
  const extra = Math.max(0, available - mainHeight - paneMinimum);
  const weights = chosen.map((key) => (key === "boll" ? 2 : 1));
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  const paneHeights = minimums.map(
    (value, i) => value + (extra * weights[i]) / (totalWeight || 1),
  );
  let top = 24 + mainHeight + gap;
  const paneTops = paneHeights.map((value) => {
    const previous = top;
    top += value + gap;
    return previous;
  });
  return { actualHeight, mainHeight, paneHeights, paneTops };
}

/** Crop display only; indicators still consume the full published history. */
export function singlePageDefaultWindow(
  period: "D" | "W" | "M",
  bars: Record<string, any>[],
  data: Record<string, any> | null,
) {
  const sessions = period === "D" ? 120 : period === "W" ? 800 : 1500;
  const fallback = (label: string, reason: string) => ({
    start: 0,
    end: 100,
    firstDate: null as string | null,
    sessions,
    label,
    reason,
    verified: false,
  });
  if (!bars.length)
    return fallback("暂无已留存报价", "没有可展示的报价，不生成K线。");
  const evidence = data?.trading_day_distance;
  if (
    evidence?.state !== "available" ||
    evidence?.basis !== "market_calendar" ||
    evidence?.anchor_date !== data?.as_of ||
    data?.period !== period
  )
    return fallback(
      "交易日窗口待核验 · 显示留存范围",
      `本图日历、截止日或周期未核验，不改用自然日或报价根数推算${sessions}交易日。`,
    );
  if (
    !stockMetricDate(data?.as_of) ||
    bars.some((bar) => !stockMetricDate(bar.date) || bar.date > data!.as_of)
  )
    return fallback(
      "交易日窗口待核验 · 显示留存范围",
      "报价日期或截止日不是真实有效日期，不能定位窗口。",
    );
  const distances = bars.map((bar) => chartSessionDistance(bar, data).days);
  if (
    bars.some(
      (bar, index) =>
        index > 0 && String(bars[index - 1].date) >= String(bar.date),
    )
  )
    return fallback(
      "交易日窗口待核验 · 显示留存范围",
      "报价日期无序或重复，不能定位交易日窗口。",
    );
  const firstKnown = distances.findIndex((value) => value !== null);
  if (
    firstKnown < 0 ||
    distances.slice(firstKnown).some((value) => value === null) ||
    (firstKnown > 0 && distances[firstKnown]! < sessions)
  )
    return fallback(
      "交易日窗口待核验 · 显示留存范围",
      "存在无法证明位于窗口之外的未核验报价日期，不以根数或自然日推算。",
    );
  const known = distances.slice(firstKnown) as number[];
  if (known.some((value, index) => index > 0 && known[index - 1] <= value))
    return fallback(
      "交易日窗口待核验 · 显示留存范围",
      "交易日距离与报价日期顺序冲突，不能定位窗口。",
    );
  const first = distances.findIndex(
    (value) => value !== null && value >= 0 && value < sessions,
  );
  if (first < 0)
    return fallback(
      `近${sessions}交易日无报价 · 显示留存历史`,
      `未找到距截止日0至${sessions - 1}个交易日的实际报价；停牌不补K，也不向前凑报价根数。`,
    );
  if (
    distances
      .slice(first)
      .some((value) => value === null || value < 0 || value >= sessions)
  )
    return fallback(
      "交易日窗口待核验 · 显示留存范围",
      "窗口内有未核验的交易日距离，不使用自然日或K线根数替代。",
    );
  return {
    start: bars.length > 1 ? (first / (bars.length - 1)) * 100 : 0,
    end: 100,
    firstDate: String(bars[first].date),
    sessions,
    label:
      (period === "D"
        ? `默认最近${sessions}个交易日`
        : `默认近${sessions}交易日涉及的${period === "W" ? "周" : "月"}K`) +
      (known[0] < sessions - 1 ? " · 现有留存不足全窗" : ""),
    reason: `含行情截止日在内，按已核验市场日历的0至${sessions - 1}日距离定位；停牌不补报价，周/月保留完整柱，指标仍用完整历史预热。`,
    verified: true,
  };
}
