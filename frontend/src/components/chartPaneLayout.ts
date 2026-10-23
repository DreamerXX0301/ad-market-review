/** Keep every selected indicator pane inside the canvas, including BOLL. */
export function chartPaneLayout(
  height: number,
  count: number,
  compact = false,
) {
  const paneCount = Math.max(0, Math.min(5, Math.trunc(count) || 0));
  const gap = 27;
  const minimumSubHeight = 55;
  const plotMinimum = Math.max(
    180 + (minimumSubHeight + gap) * paneCount,
    ((minimumSubHeight + gap) * paneCount) / 0.52,
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
  return { actualHeight, mainHeight, subHeight, gap };
}
