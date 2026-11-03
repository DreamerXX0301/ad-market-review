import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

const styles = {
  support: { role: "支撑线", stroke: "solid" },
  resistance: { role: "压力线", stroke: "dashed" },
} as const;

type LineType = keyof typeof styles;
type LineScope = "main" | "near";
type TrendLine = {
  id: string;
  scope: LineScope;
  kind: LineType;
  label: string;
  color: string;
  stroke: "solid" | "dashed";
  endPrice: number;
  endDate: string;
  values: (number | null)[];
};

type Zone = {
  id: string;
  kind: LineType;
  label: string;
  color: string;
  startDate: string;
  endDate: string;
  low: number;
  high: number;
  touches: number;
};
type TrendEvent = {
  date: string;
  confirmedOn?: string;
  price: number;
  label: string;
  line: TrendLine;
};

const positivePrice = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

/** Bind evidence to this chart and interpolate only between returned bar endpoints. */
export function chartAutomaticTrendlines(
  chart: Row | null | undefined,
  instrumentId: string,
  period: ChartPeriod,
  asOf?: string,
): {
  available: boolean;
  reason: string;
  lines: TrendLine[];
  zones: Zone[];
  events: TrendEvent[];
} {
  const source = chart?.automatic_trendlines;
  const lines: TrendLine[] = [];
  const zones: Zone[] = [];
  const events: TrendEvent[] = [];
  if (
    chart?.instrument?.instrument_id !== instrumentId ||
    chart?.period !== period ||
    typeof chart?.as_of !== "string" ||
    (asOf && asOf !== chart.as_of) ||
    source?.algorithm !== "confirmed-pivot-trendlines-v1" ||
    source.instrument_id !== instrumentId ||
    source.period !== period ||
    source.as_of !== chart.as_of ||
    typeof chart.snapshot_id !== "string" ||
    !chart.snapshot_id ||
    source.snapshot_id !== chart.snapshot_id ||
    !["none", "forward_event_affine"].includes(chart.price_basis) ||
    source.price_basis !== chart.price_basis ||
    !Array.isArray(source.items) ||
    !Array.isArray(chart.bars)
  )
    return { available: false, reason: "", lines, zones, events };

  const indices = new Map<string, number>();
  for (const [index, bar] of chart.bars.entries()) {
    if (
      typeof bar.date !== "string" ||
      bar.date > chart.as_of ||
      (index > 0 && bar.date <= chart.bars[index - 1].date)
    )
      return { available: false, reason: "", lines, zones, events };
    indices.set(bar.date, index);
  }

  const projectLine = (row: Row | null, scope: LineScope): TrendLine | null => {
    if (row?.line_type !== "support" && row?.line_type !== "resistance")
      return null;
    const kind: LineType = row.line_type;
    const start = indices.get(row.start_date);
    const end = indices.get(row.end_date);
    if (
      start == null ||
      end == null ||
      start >= end ||
      !positivePrice(row.start_price) ||
      !positivePrice(row.end_price) ||
      row.start_price === row.end_price ||
      typeof row.confirmed_on !== "string" ||
      row.confirmed_on < row.start_date ||
      row.confirmed_on > row.end_date
    )
      return null;

    const startPrice = row.start_price;
    const endPrice = row.end_price;
    const slope = (endPrice - startPrice) / (end - start);
    const { role, stroke } = styles[kind];
    const rising = slope > 0;
    return {
      id: `line:${scope}:${kind}`,
      kind,
      scope,
      label: `${scope === "near" ? "近端" : ""}${rising ? "上升" : "下降"}${role}`,
      color: rising ? "#5689e0" : "#d78336",
      stroke,
      endPrice,
      endDate: row.end_date,
      values: chart.bars.map((_: Row, index: number) =>
        index < start || index > end
          ? null
          : index === end
            ? endPrice
            : startPrice + (index - start) * slope,
      ),
    };
  };

  for (const [scope, rows] of [
    ["main", source.items],
    ["near", source.near_items],
  ] as const) {
    if (!Array.isArray(rows)) continue;
    for (const row of rows) {
      const line = projectLine(row, scope);
      if (
        line &&
        !lines.some((item) => item.kind === line.kind && item.scope === scope)
      )
        lines.push(line);
    }
  }

  if (Array.isArray(source.zones))
    for (const row of source.zones) {
      if (
        (row?.line_type !== "support" && row?.line_type !== "resistance") ||
        !indices.has(row.start_date) ||
        !indices.has(row.end_date) ||
        row.start_date > row.end_date ||
        !positivePrice(row.lower_price) ||
        !positivePrice(row.upper_price) ||
        row.lower_price > row.upper_price ||
        !Number.isInteger(row.touches) ||
        row.touches < 2
      )
        continue;
      const close = chart.bars[indices.get(row.end_date)!]?.close;
      const inside =
        positivePrice(close) &&
        close >= row.lower_price &&
        close <= row.upper_price;
      zones.push({
        id: `zone:${row.line_type}:${row.start_date}:${row.lower_price}`,
        kind: row.line_type,
        label: inside
          ? "水平观察区"
          : row.line_type === "support"
            ? "水平支撑区"
            : "水平压力区",
        color: inside
          ? "#a786e8"
          : row.line_type === "support"
            ? "#27a795"
            : "#d78336",
        startDate: row.start_date,
        endDate: row.end_date,
        low: row.lower_price,
        high: row.upper_price,
        touches: row.touches,
      });
    }

  if (Array.isArray(source.events))
    for (const row of source.events) {
      if (
        (row?.scope !== "main" && row?.scope !== "near") ||
        !indices.has(row.date) ||
        !positivePrice(row.price)
      )
        continue;
      const line = projectLine(row.line, row.scope);
      if (!line || line.kind !== row.line_type || line.endDate !== row.date)
        continue;
      const support = line.kind === "support";
      const label =
        row.state === "wick_cross"
          ? support
            ? "盘中跌破支撑，收盘收回"
            : "盘中越过压力，收盘回落"
          : row.state === "close_cross"
            ? support
              ? "收盘跌破支撑"
              : "收盘突破压力"
            : row.state === "confirmed_cross"
              ? support
                ? "连续两根收在支撑下方"
                : "连续两根收在压力上方"
              : "";
      if (label)
        events.push({
          date: row.date,
          confirmedOn:
            indices.has(row.confirmed_on) && row.confirmed_on > row.date
              ? row.confirmed_on
              : undefined,
          price: row.price,
          label,
          line,
        });
    }

  return {
    available: true,
    reason:
      typeof source.reason === "string" && source.reason
        ? source.reason
        : "由当前周期已核验转折点计算，属于图表观察，不写入手动画线或提醒。",
    lines,
    zones,
    events,
  };
}
