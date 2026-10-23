import type { CustomSeriesOption } from "echarts";
import type { IndexPriceProfile } from "./indexPriceProfile";

export type IndexProfilePalette = {
  center: string;
  valueArea: string;
  outside: string;
  boundary: string;
  text?: string;
};

export type IndexProfileGuide = {
  kind: "center" | "val" | "vah";
  price: number;
  label: string;
};

const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const numberText = (value: number) =>
  value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const escapeHtml = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character]!,
  );

/** Prices share the main chart's numeric Y range; they never use a date axis. */
export function indexProfileGuides(
  profile: IndexPriceProfile,
): IndexProfileGuide[] {
  if (profile.state === "unavailable" || !profile.rows.length) return [];
  return [
    { kind: "center" as const, price: profile.center, label: "活动中心" },
    { kind: "val" as const, price: profile.val, label: "主要区下沿" },
    { kind: "vah" as const, price: profile.vah, label: "主要区上沿" },
  ].filter((guide): guide is IndexProfileGuide => positive(guide.price));
}

/** The denominator is real quoted days, not intraday TPO or traded volume. */
export function indexProfileTooltipHtml(
  profile: IndexPriceProfile,
  dataIndex: number,
): string {
  if (
    profile.state === "unavailable" ||
    !Number.isInteger(dataIndex) ||
    dataIndex < 0
  )
    return "";
  const row = profile.rows[dataIndex];
  if (
    !row ||
    !positive(row.low) ||
    !positive(row.high) ||
    row.high < row.low ||
    !Number.isInteger(row.count) ||
    row.count <= 0
  )
    return "";
  const center = row.index === profile.centerIndex;
  const inArea =
    profile.valueAreaStartIndex != null &&
    profile.valueAreaEndIndex != null &&
    row.index >= profile.valueAreaStartIndex &&
    row.index <= profile.valueAreaEndIndex;
  const role = center ? "活动中心所在档" : inArea ? "主要活动区" : "区间外";
  return [
    `<strong>指数价格分布 · ${role}</strong>`,
    `${numberText(row.low)} — ${numberText(row.high)} 点`,
    `区间覆盖该档：${row.count} / ${profile.observed} 个报价日`,
    `${escapeHtml(profile.startDate || "—")} → ${escapeHtml(profile.endDate || "—")}`,
    "每个报价日每档至多计一次，非成交量或筹码",
  ].join("<br/>");
}

/**
 * Independent horizontal distribution. Counts use X; actual price bands use Y.
 * The caller owns a dedicated grid/axes and must exclude them from time zoom.
 */
export function buildIndexProfileSeries(
  profile: IndexPriceProfile,
  options: { axisIndex: number; palette: IndexProfilePalette },
): CustomSeriesOption {
  const { axisIndex, palette } = options;
  return {
    id: "index-price-profile",
    name: "日线价格区间分布",
    type: "custom",
    coordinateSystem: "cartesian2d",
    xAxisIndex: axisIndex,
    yAxisIndex: axisIndex,
    dimensions: ["low", "high", "count", "profileIndex"],
    encode: { x: 2, y: [0, 1], tooltip: [0, 1, 2] },
    data:
      profile.state === "unavailable"
        ? []
        : profile.rows.map((row) => [row.low, row.high, row.count, row.index]),
    animation: false,
    clip: true,
    z: 3,
    tooltip: {
      trigger: "item",
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params;
        return indexProfileTooltipHtml(profile, item.dataIndex);
      },
    },
    renderItem: (params, api) => {
      const low = Number(api.value(0));
      const high = Number(api.value(1));
      const count = Number(api.value(2));
      const rowIndex = Number(api.value(3));
      if (
        !positive(low) ||
        !positive(high) ||
        high < low ||
        !Number.isFinite(count) ||
        count <= 0
      )
        return;
      const grid = params.coordSys as unknown as {
        x: number;
        y: number;
        width: number;
        height: number;
      };
      if (![grid.x, grid.y, grid.width, grid.height].every(Number.isFinite))
        return;
      const bottom = api.coord([0, low]);
      const top = api.coord([0, high]);
      const end = api.coord([count, (low + high) / 2]);
      if (![...bottom, ...top, ...end].every(Number.isFinite)) return;
      // A genuinely flat whole window has a single price bucket. Draw a small
      // glyph at that exact coordinate instead of inventing a price interval.
      const flat = low === high;
      const y0 = Math.min(top[1], bottom[1]) - (flat ? 1.5 : 0);
      const y1 = Math.max(top[1], bottom[1]) + (flat ? 1.5 : 0);
      const gap = y1 - y0 > 3 ? 0.65 : 0;
      const left = Math.max(grid.x, Math.min(bottom[0], end[0]));
      const right = Math.min(grid.x + grid.width, Math.max(bottom[0], end[0]));
      const upper = Math.max(grid.y, y0 + gap);
      const lower = Math.min(grid.y + grid.height, y1 - gap);
      if (right <= left || lower <= upper) return;
      const center = rowIndex === profile.centerIndex;
      const inArea =
        profile.valueAreaStartIndex != null &&
        profile.valueAreaEndIndex != null &&
        rowIndex >= profile.valueAreaStartIndex &&
        rowIndex <= profile.valueAreaEndIndex;
      return {
        type: "rect",
        shape: {
          x: left,
          y: upper,
          width: right - left,
          height: lower - upper,
        },
        style: {
          fill: center
            ? palette.center
            : inArea
              ? palette.valueArea
              : palette.outside,
          opacity: center ? 0.95 : inArea ? 0.72 : 0.42,
        },
        emphasis: {
          style: { opacity: 1, stroke: palette.boundary, lineWidth: 1 },
        },
      };
    },
  };
}
