import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

/** A frozen tracking-round reference, never an inferred execution price. */
export type ChartEventBaseline = {
  date: string;
  rawClose: number;
  priceBasis?: string;
  label?: string;
  roundId?: string;
};

export function defaultChartWindow(period: ChartPeriod, count: number) {
  return {
    start:
      period === "D" && count > 100 ? ((count - 100) / (count - 1)) * 100 : 0,
    end: 100,
  };
}

function validDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}
function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}
function nonnegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/** Match the backend's event_affine model, using this chart's verified anchor.
 * A period bar's close is never substituted for the frozen entry-day price.
 */
export function projectEventBaseline(
  baseline: ChartEventBaseline | undefined,
  chart: Row | null | undefined,
) {
  const unavailable = (reason: string) => ({
    price: null,
    reason,
    converted: false,
    anchor: null as string | null,
  });
  if (!baseline) return unavailable("未提供事件基准");
  if (!validDate(baseline.date))
    return unavailable("事件日期无效，未显示基准线");
  if (!positive(baseline.rawClose))
    return unavailable("冻结事件收盘价无效，未显示基准线");
  if ((baseline.priceBasis ?? "none") !== "none")
    return unavailable("事件价格不是已知的未复权原始价，未显示基准线");
  if (!chart || !validDate(chart.as_of))
    return unavailable("图表观察日尚未核验，未显示基准线");
  if (baseline.date > chart.as_of)
    return unavailable("事件日晚于图表观察日，未显示基准线");
  const bars: Row[] = Array.isArray(chart.bars) ? chart.bars : [];
  const dates = bars
    .map((bar) => bar.date)
    .filter(validDate)
    .sort();
  if (!dates.length) return unavailable("暂无可核验行情，未显示基准线");
  if (baseline.date < dates[0])
    return unavailable(
      "事件日早于本图可核验边界；周/月首柱边界从严处理，未显示基准线",
    );
  if (baseline.date > dates[dates.length - 1])
    return unavailable("事件日尚无本图可核验报价，未显示基准线");
  if (chart.period === "D") {
    const entryBar = bars.find((bar) => bar.date === baseline.date);
    const rawClose =
      chart.price_basis === "none" ? entryBar?.close : entryBar?.raw_close;
    if (!positive(rawClose))
      return unavailable("事件日原始报价缺失，未显示基准线");
    if (Math.abs(rawClose - baseline.rawClose) > 0.00005)
      return unavailable("冻结事件收盘价与本图事件日原价不一致，未显示基准线");
  } else if (
    bars.some((bar) =>
      [
        ...(bar.weekly_missing_market_dates || []),
        ...(bar.monthly_missing_market_dates || []),
      ].includes(baseline.date),
    )
  ) {
    return unavailable("事件日处于本图报价缺口，未显示基准线");
  }
  if (chart.price_basis === "none")
    return {
      price: baseline.rawClose,
      reason: "冻结入池事件日的未复权收盘价；仅作跟踪参照，不代表成交买价",
      converted: false,
      anchor: null,
    };
  if (chart.price_basis !== "forward_event_affine")
    return unavailable("图表价格口径未知，未显示基准线");
  const plan = chart.adjustment;
  if (
    !plan ||
    plan.usable !== true ||
    plan.model !== "event_affine" ||
    !validDate(plan.anchor) ||
    plan.anchor !== chart.as_of ||
    !Array.isArray(plan.events) ||
    !Array.isArray(plan.quarantined) ||
    plan.quarantined.length ||
    plan.events_count !== plan.events.length
  )
    return unavailable("当前前复权锚或公司行动未通过完整校验，未显示基准线");
  if (
    bars.some(
      (bar) =>
        bar.adjustment_anchor != null && bar.adjustment_anchor !== plan.anchor,
    )
  )
    return unavailable("行情与事件价格的复权锚不一致，未显示基准线");
  const events: Row[] = [...plan.events].sort((a, b) =>
    String(a.date).localeCompare(String(b.date)),
  );
  let previousDate: string | null = null;
  let price = baseline.rawClose;
  for (const event of events) {
    if (
      !validDate(event.date) ||
      event.date > plan.anchor ||
      event.date === previousDate ||
      ![event.cash, event.bonus, event.rights, event.rights_price].every(
        nonnegative,
      )
    )
      return unavailable("公司行动日期或换算字段无效，未显示基准线");
    previousDate = event.date;
    if (event.date <= baseline.date) continue;
    price =
      (price - event.cash + event.rights_price * event.rights) /
      (1 + event.bonus + event.rights);
    if (!positive(price))
      return unavailable("事件价格换算结果无效，未显示基准线");
  }
  return {
    price,
    reason: `冻结原价按已核验公司行动换算至 ${plan.anchor}，与本图前复权口径一致；不代表成交买价`,
    converted: true,
    anchor: plan.anchor as string,
  };
}
