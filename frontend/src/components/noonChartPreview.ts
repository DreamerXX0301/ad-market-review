import type { Row } from "../lib/api";

export type NoonChartPreview = {
  snapshot_id?: string;
  trade_date: string;
  previous_trade_date?: string | null;
  collected_end_at?: string | null;
  quote: {
    instrument_id?: string;
    open: number;
    high: number;
    low: number;
    latest_price: number;
    prev_close?: number | null;
    volume?: number | null;
    amount?: number | null;
    change_pct?: number | null;
  } | null;
  reason?: string | null;
};

export function provisionalNoonBar(
  preview: NoonChartPreview | undefined,
  chart: Row | null,
  period: string,
): Row | null {
  const quote = preview?.quote;
  const prior = chart?.bars?.at(-1);
  if (
    !preview ||
    !quote ||
    period !== "D" ||
    !prior ||
    !chart ||
    !["none", "forward_event_affine"].includes(chart.price_basis) ||
    prior.date !== chart.as_of ||
    prior.date >= preview.trade_date ||
    prior.date !== preview.previous_trade_date ||
    (chart.instrument?.kind === "stock" &&
      quote.instrument_id !== chart.instrument.instrument_id)
  )
    return null;
  // The anchor bar stays on its original scale. A verified matching
  // reference close is required before using that scale for the noon bar.
  if (
    chart.price_basis === "forward_event_affine" &&
    (chart.adjustment?.usable !== true ||
      chart.adjustment.anchor !== prior.date ||
      prior.close !== prior.raw_close ||
      quote.prev_close == null)
  )
    return null;
  if (quote.prev_close != null) {
    const decimals = chart.instrument?.kind === "index" ? 3 : 2;
    const factor = 10 ** decimals;
    if (
      !Number.isFinite(prior.close) ||
      Math.round(prior.close * factor) !== Math.round(quote.prev_close * factor)
    )
      return null;
  }
  const { open, high, low, latest_price } = quote;
  if (
    ![open, high, low, latest_price].every(
      (price) => Number.isFinite(price) && price > 0,
    ) ||
    low > Math.min(open, latest_price) ||
    high < Math.max(open, latest_price)
  )
    return null;
  return {
    date: preview.trade_date,
    open,
    high,
    low,
    close: latest_price,
    volume: quote.volume ?? null,
    amount: quote.amount ?? null,
    change_pct: quote.change_pct ?? null,
    complete: false,
    noon_provisional: true,
  };
}

export function noonCollectionTime(preview: NoonChartPreview) {
  if (!preview.collected_end_at) return null;
  const timestamp = new Date(preview.collected_end_at);
  if (!Number.isFinite(timestamp.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(timestamp);
}
