import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";
import type { ChartLineLabel } from "./chartLineLabelLayout";

export type PriceVolumeProfile = "structure" | "short_week_v1";

export type PriceVolumeLevel = {
  price: number;
  distancePct: number | null;
  source: string;
  formedAt: string;
  confirmedOn: string;
  bufferAtr: number | null;
  bufferValue: number | null;
  basePrice: number | null;
  atrReferenceDate: string | null;
  breakoutDate: string | null;
  state: string;
};

export type PriceVolumeView = {
  profile: PriceVolumeProfile;
  maxRiskPct: number | null;
  supportReason: string;
  supportScope: "recent_20" | "historical_120" | null;
  supportDistanceState: "far" | "within_risk_distance" | null;
  notice: string;
  referenceDate: string | null;
  referencePrice: number | null;
  unit: "元" | "点";
  cycle: "日" | "周" | "月";
  reason: string;
  support: PriceVolumeLevel | null;
  resistance: PriceVolumeLevel | null;
  stop: PriceVolumeLevel | null;
  breakout: PriceVolumeLevel | null;
  signal: { state: string; label: string; reason: string };
  volume: {
    current: number | null;
    mean: number | null;
    ratio: number | null;
    state: string;
    reason: string;
    start: string | null;
    end: string | null;
    unit: string;
  };
  atr: {
    value: number | null;
    start: string | null;
    end: string | null;
    reason: string;
  };
  recentBreakout: {
    date: string;
    price: number;
    volumeRatio: number | null;
  } | null;
};

const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const nonnegative = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const text = (value: unknown) => (typeof value === "string" ? value : "");

/** Price levels and volume evidence must belong to the returned chart. */
export function chartPriceVolume(
  chart: Row | null | undefined,
  instrumentId: string,
  period: ChartPeriod,
  asOf?: string,
  profile: PriceVolumeProfile = "structure",
): PriceVolumeView | null {
  const short = profile === "short_week_v1";
  const source = short
    ? chart?.price_volume_levels?.short_term
    : chart?.price_volume_levels;
  if (
    chart?.instrument?.instrument_id !== instrumentId ||
    chart?.period !== period ||
    typeof chart?.as_of !== "string" ||
    (asOf && asOf !== chart.as_of) ||
    source?.algorithm !==
      (short ? "short-week-risk-levels-v1" : "structure-volume-levels-v1") ||
    (short &&
      (period !== "D" ||
        chart.instrument?.kind !== "stock" ||
        source.profile !== "short_week_v1" ||
        !finite(source.max_risk_pct) ||
        source.max_risk_pct < 0.5 ||
        source.max_risk_pct > 20)) ||
    (!short && source.profile != null && source.profile !== "structure") ||
    source.instrument_id !== instrumentId ||
    source.period !== period ||
    source.as_of !== chart.as_of ||
    typeof chart.snapshot_id !== "string" ||
    !chart.snapshot_id ||
    source.snapshot_id !== chart.snapshot_id ||
    !["none", "forward_event_affine"].includes(chart.price_basis) ||
    source.price_basis !== chart.price_basis ||
    !Array.isArray(chart.bars)
  )
    return null;

  const bars = new Map<string, Row>();
  for (const [index, bar] of chart.bars.entries()) {
    if (
      typeof bar.date !== "string" ||
      bar.date > chart.as_of ||
      (index > 0 && bar.date <= chart.bars[index - 1].date)
    )
      return null;
    bars.set(bar.date, bar);
  }
  const referenceDate = source.reference_date;
  const referenceBar = bars.get(referenceDate);
  const hasReference =
    referenceBar &&
    positive(source.reference_price) &&
    referenceBar.close === source.reference_price &&
    referenceBar.noon_provisional !== true &&
    referenceBar.week_closed !== false &&
    referenceBar.month_closed !== false &&
    referenceBar.weekly_quote_coverage_complete !== false &&
    referenceBar.monthly_quote_coverage_complete !== false;
  if (
    !hasReference &&
    (source.reference_date != null || source.reference_price != null)
  )
    return null;
  const knownDate = (value: unknown): value is string =>
    !!hasReference &&
    typeof value === "string" &&
    bars.has(value) &&
    value <= referenceDate;
  const level = (value: Row | null): PriceVolumeLevel | null => {
    if (
      !value ||
      !positive(value.price) ||
      !knownDate(value.formed_at) ||
      !knownDate(value.confirmed_on) ||
      value.formed_at > value.confirmed_on
    )
      return null;
    return {
      price: value.price,
      distancePct: finite(value.distance_pct) ? value.distance_pct : null,
      source: text(value.source),
      formedAt: value.formed_at,
      confirmedOn: value.confirmed_on,
      bufferAtr: nonnegative(value.buffer_atr) ? value.buffer_atr : null,
      bufferValue: nonnegative(value.buffer_value) ? value.buffer_value : null,
      basePrice: positive(value.base_price) ? value.base_price : null,
      atrReferenceDate: knownDate(value.atr_reference_date)
        ? value.atr_reference_date
        : null,
      breakoutDate: knownDate(value.breakout_date) ? value.breakout_date : null,
      state: text(value.state),
    };
  };
  const volume = source.volume;
  const volumeKnown =
    hasReference &&
    ["expanded", "contracted", "normal"].includes(volume?.state) &&
    nonnegative(volume.current) &&
    positive(volume.mean_previous20) &&
    nonnegative(volume.ratio) &&
    knownDate(volume.mean_start) &&
    knownDate(volume.mean_end) &&
    volume.mean_start <= volume.mean_end &&
    volume.mean_end < referenceDate;
  const atr = source.atr;
  const atrKnown =
    hasReference &&
    atr?.state === "ready" &&
    atr.period === 14 &&
    nonnegative(atr.value) &&
    knownDate(atr.window_start) &&
    knownDate(atr.window_end) &&
    atr.window_start <= atr.window_end &&
    atr.window_end === referenceDate;
  // A display-only structure never substitutes for the saved reminder basis.
  // Explicit null/invalid display data must not fall back to a different level.
  const hasDisplaySupport = short && Object.hasOwn(source, "display_support");
  const displaySupport = hasDisplaySupport
    ? level(source.display_support)
    : null;
  const validDisplayScope = ["recent_20", "historical_120"].includes(
    source.display_scope,
  );
  const support = hasDisplaySupport
    ? displaySupport &&
      validDisplayScope &&
      displaySupport.source === "confirmed_pivot_low" &&
      displaySupport.price <= source.reference_price
      ? {
          ...displaySupport,
          distancePct:
            (displaySupport.price / source.reference_price - 1) * 100,
        }
      : null
    : level(source.support);
  const supportScope =
    hasDisplaySupport && support
      ? (source.display_scope as "recent_20" | "historical_120")
      : null;
  const supportDistanceState =
    short && support?.distancePct != null
      ? -support.distancePct > source.max_risk_pct + 1e-9
        ? "far"
        : "within_risk_distance"
      : null;
  return {
    profile,
    maxRiskPct: short ? source.max_risk_pct : null,
    supportReason:
      supportScope && support
        ? `${supportScope === "historical_120" ? "历史结构支撑（120根）" : "近20日结构支撑"}；${supportDistanceState === "far" ? "距离较远，不能替代短线风险线" : "结构观察，不等于短线风险线"}`
        : text(source.support_reason),
    supportScope,
    supportDistanceState,
    notice: text(source.notice),
    referenceDate: hasReference ? referenceDate : null,
    referencePrice: hasReference ? source.reference_price : null,
    unit:
      chart.price_unit === "点" || chart.instrument?.kind === "index"
        ? "点"
        : "元",
    cycle: period === "D" ? "日" : period === "W" ? "周" : "月",
    reason:
      supportScope && text(source.reason).startsWith("暂无近端支撑")
        ? "已显示真实结构支撑；短线风险线和提醒基准分别计算。"
        : text(source.reason),
    support,
    resistance: level(source.resistance),
    stop:
      short && source.stop?.source !== "short_risk_boundary"
        ? null
        : level(source.stop),
    breakout: level(source.breakout),
    signal: {
      state: text(source.signal?.state) || "unknown",
      label: text(source.signal?.label) || "量价待核验",
      reason: text(source.signal?.reason),
    },
    volume: {
      current:
        hasReference && nonnegative(volume?.current) ? volume.current : null,
      mean: volumeKnown ? volume.mean_previous20 : null,
      ratio: volumeKnown ? volume.ratio : null,
      state: volumeKnown ? volume.state : "unknown",
      reason: text(volume?.reason),
      start: volumeKnown ? volume.mean_start : null,
      end: volumeKnown ? volume.mean_end : null,
      unit: text(chart.volume_unit) || "来源单位",
    },
    atr: {
      value: atrKnown ? atr.value : null,
      start: atrKnown ? atr.window_start : null,
      end: atrKnown ? atr.window_end : null,
      reason: text(atr?.reason),
    },
    recentBreakout:
      knownDate(source.recent_breakout?.date) &&
      positive(source.recent_breakout.price)
        ? {
            date: source.recent_breakout.date,
            price: source.recent_breakout.price,
            volumeRatio: nonnegative(source.recent_breakout.volume?.ratio)
              ? source.recent_breakout.volume.ratio
              : null,
          }
        : null,
  };
}

export function priceVolumeLineLabels(
  view: PriceVolumeView | null,
): ChartLineLabel[] {
  if (!view) return [];
  return (
    [
      ["stop", "参考止损", "自止", "support", "#c79136"],
      ["support", "支撑参考", "自撑", "support", "#39bd9b"],
      ["breakout", "突破参考", "自破", "resistance", "#ed6865"],
    ] as const
  ).flatMap(([key, label, shortLabel, kind, color]) => {
    const level = view[key];
    if (!level) return [];
    const supportTitle =
      view.supportScope === "historical_120"
        ? "历史结构支撑（120根）"
        : view.supportScope === "recent_20"
          ? "近20日结构支撑"
          : label;
    return [
      {
        id: `price-volume:${key}`,
        kind,
        group: "price-volume",
        price: level.price,
        color,
        enabled: true,
        shortLabel:
          key === "stop" && view.profile === "short_week_v1"
            ? "短险"
            : shortLabel,
        detail: `${key === "stop" ? (view.profile === "short_week_v1" ? "短线风险线" : "结构失效参考") : key === "support" ? supportTitle : key === "breakout" && ["held", "retest"].includes(level.state) ? "原突破参考" : label} ${level.price} ${view.unit} · ${view.referenceDate} 完整${view.cycle}K · ${level.source === "short_risk_boundary" ? `风险公式基准 ${level.formedAt}，不代表确认支撑` : `拐点 ${level.formedAt} · 确认 ${level.confirmedOn}`}${key === "support" && view.supportDistanceState === "far" ? " · 距离较远，不能替代短线风险线" : ""}`,
      },
    ];
  });
}
