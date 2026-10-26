export type ChartLineLabel = {
  id: string;
  kind: "support" | "resistance" | "reference" | "event";
  group: "annotation" | "baseline" | "history";
  price: number;
  color: string;
  detail: string;
  enabled: boolean;
};

/** Short text belongs in the reserved rail; the exact name/price stays in detail. */
export function shortChartLineLabel(line: ChartLineLabel) {
  const prefix = {
    support: "撑",
    resistance: "压",
    reference: "参",
    event: "事件",
  }[line.kind];
  return (
    prefix + line.price.toLocaleString("zh-CN", { maximumFractionDigits: 4 })
  );
}

export function chartLineRailWidth(width: number) {
  return width > 0 && width < 560 ? 92 : 112;
}

/** Greedy ordered rows with a reverse pass preserve price order without overlap. */
export function layoutChartLineLabels(
  values: Array<{ id: string; y: number }>,
  top: number,
  height: number,
) {
  const rowHeight = 21,
    gap = 3;
  const capacity = Math.max(1, Math.floor(height / (rowHeight + gap)));
  const ordered = values
    .filter((value) => Number.isFinite(value.y))
    .sort((a, b) => a.y - b.y || a.id.localeCompare(b.id));
  const limited = ordered.slice(
    0,
    ordered.length > capacity ? Math.max(0, capacity - 1) : capacity,
  );
  const bottom = top + Math.max(rowHeight, height) - rowHeight;
  let previous = top - rowHeight - gap;
  const rows = limited.map((value) => {
    const labelY = Math.max(
      top,
      Math.min(bottom, value.y - rowHeight / 2),
      previous + rowHeight + gap,
    );
    previous = labelY;
    return {
      ...value,
      labelY,
      outside:
        value.y < top ? "above" : value.y > top + height ? "below" : null,
    };
  });
  for (let index = rows.length - 1; index >= 0; index--) {
    rows[index].labelY = Math.min(
      rows[index].labelY,
      index === rows.length - 1
        ? bottom
        : rows[index + 1].labelY - rowHeight - gap,
    );
  }
  return { rows, overflow: ordered.length - rows.length, rowHeight };
}

export function visibleChartLineLabels(
  lines: ChartLineLabel[],
  hidden: ReadonlySet<string>,
) {
  return lines.filter((line) => line.enabled && !hidden.has(line.id));
}

/** Width limits actual candles, never indicator seeds or the stored date range. */
export function readableChartWindow<
  T extends { start: number; end: number; firstDate?: string | null },
>(
  base: T,
  period: "D" | "W" | "M",
  bars: Array<{ date?: string }>,
  plotWidth: number,
) {
  const first = Math.max(
    0,
    Math.min(
      bars.length - 1,
      Math.round((base.start / 100) * Math.max(1, bars.length - 1)),
    ),
  );
  if (!bars.length || !Number.isFinite(plotWidth) || plotWidth <= 0)
    return {
      ...base,
      firstDate: base.firstDate || bars[first]?.date || null,
      visibleBars: Math.max(0, bars.length - first),
      widthAdapted: false,
    };
  const minimum = period === "D" ? 7 : period === "W" ? 8 : 10;
  const count = Math.max(8, Math.floor(plotWidth / minimum));
  const start = Math.max(first, bars.length - count);
  return {
    ...base,
    start: bars.length > 1 ? (start / (bars.length - 1)) * 100 : 0,
    firstDate: bars[start]?.date || base.firstDate || null,
    visibleBars: bars.length - start,
    widthAdapted: start > first,
  };
}
