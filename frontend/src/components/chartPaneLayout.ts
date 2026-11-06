export function chartTextSizes(width: number, wideViewport: boolean) {
  const wide = wideViewport && width >= 1100;
  return { wide, axis: wide ? 12 : 10, tooltip: wide ? 13 : 11 };
}

/** Reserve hover text space by available chart width, never by the selected candle. */
export function chartHoverSummaryHeight(width: number, wide = false) {
  if (wide) return width >= 1500 ? 80 : 100;
  if (!Number.isFinite(width) || width <= 0) return 128;
  if (width >= 1500) return 66;
  if (width >= 1000) return 82;
  if (width >= 700) return 104;
  if (width >= 480) return 128;
  return 160;
}

/** Keep every selected indicator pane inside the canvas, including BOLL. */
export function chartPaneLayout(
  height: number,
  count: number,
  compact = false,
  paneMinimumHeights: readonly number[] = [],
) {
  const paneCount = Math.max(0, Math.min(5, Math.trunc(count) || 0));
  const gap = 27;
  const minimumSubHeight = 55;
  const minimums = Array.from({ length: paneCount }, (_, index) => {
    const value = paneMinimumHeights[index];
    return Number.isFinite(value)
      ? Math.max(minimumSubHeight, value)
      : minimumSubHeight;
  });
  const minimumPanes = minimums.reduce((sum, value) => sum + value, 0);
  const plotMinimum = Math.max(
    180 + minimumPanes + gap * paneCount,
    (minimumPanes + gap * paneCount) / 0.52,
  );
  const requested = Number.isFinite(height) ? height : 650;
  const actualHeight = Math.ceil(
    Math.max(requested, compact ? 480 : 300, 75 + plotMinimum),
  );
  const plotHeight = actualHeight - 75;
  const mainHeight = Math.max(180, plotHeight * (paneCount ? 0.48 : 0.9));
  const subHeight = paneCount
    ? (plotHeight - mainHeight - gap * paneCount) / paneCount
    : 0;
  const extraHeight = paneCount
    ? Math.max(0, (subHeight * paneCount - minimumPanes) / paneCount)
    : 0;
  const paneHeights = minimums.map((minimum) => minimum + extraHeight);
  let top = 24 + mainHeight + gap;
  const paneTops = paneHeights.map((paneHeight) => {
    const result = top;
    top += paneHeight + gap;
    return result;
  });
  return { actualHeight, mainHeight, subHeight, gap, paneHeights, paneTops };
}
