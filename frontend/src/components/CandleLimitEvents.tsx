import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

export type LimitKind = "limit_up" | "limit_down";
export type PatternKind =
  | "earth_to_sky"
  | "one_price_limit_up"
  | "one_price_limit_down"
  | "broken_board"
  | "resealed_board";
export type LimitDescription = {
  text: string;
  detail: string;
  source: string;
  tone: "up" | "down" | "muted" | "warning";
};
export const PATTERN_BASIS = "verified-source-close-pool-and-raw-ohlc-v1";
export const BOARD_BASIS = "verified-dated-close-pool-board-events-v1";
export const INTRADAY_BOARD_BASIS =
  "verified-intraday-snapshot-board-events-v1";
const patternLabels: Record<PatternKind, string> = {
  earth_to_sky: "地天板",
  one_price_limit_up: "一字涨停",
  one_price_limit_down: "一字跌停",
  broken_board: "炸板",
  resealed_board: "回封",
};
const patternKinds = Object.keys(patternLabels) as PatternKind[];

const sourceNames: Record<string, string> = {
  fuyao: "Fuyao",
  eastmoney: "东方财富公开数据",
  "eastmoney-public": "东方财富公开数据",
};

export function confirmedBarLimits(bar?: Row): Row[] {
  const unique = new Map<string, Row>();
  for (const event of bar?.limit_events || []) {
    if (
      !["limit_up", "limit_down"].includes(event.kind) ||
      event.basis !== "verified-source-close-pool"
    )
      continue;
    unique.set(event.date + ":" + event.kind, event);
  }
  return [...unique.values()].sort((a, b) => a.date.localeCompare(b.date));
}

function isoDay(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return false;
  const parsed = new Date(value + "T00:00:00Z");
  return (
    Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}

const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

/** Only backend-confirmed raw-day patterns; display OHLC never creates one. */
export function confirmedBarPatterns(bar?: Row): Row[] {
  const unique = new Map<string, Row>();
  for (const event of Array.isArray(bar?.pattern_events)
    ? bar.pattern_events
    : []) {
    const board = ["broken_board", "resealed_board"].includes(event?.kind);
    const intraday = board && event?.is_intraday === true;
    if (
      !event ||
      !patternKinds.includes(event.kind) ||
      event.basis !==
        (intraday
          ? INTRADAY_BOARD_BASIS
          : board
            ? BOARD_BASIS
            : PATTERN_BASIS) ||
      event.raw_price_basis !== "none" ||
      !isoDay(event.date) ||
      !isoDay(event.bar_date) ||
      event.date > event.bar_date ||
      (isoDay(bar?.date) &&
        (event.bar_date !== bar.date || event.date > bar.date)) ||
      (intraday
        ? bar?.noon_provisional !== true ||
          event.snapshot_id !== bar.snapshot_id ||
          event.date !== bar.date ||
          !/^[a-f0-9]{64}$/.test(event.evidence?.snapshot_sha256 || "") ||
          !positive(event.raw_close) ||
          event.raw_close !== bar.close ||
          !(positive(bar.volume) || positive(bar.amount))
        : !positive(event.raw_volume) ||
          !["open", "high", "low", "close"].every((field) =>
            positive(event.raw_ohlc?.[field]),
          ))
    )
      continue;
    unique.set(event.date + ":" + event.kind, event);
  }
  return [...unique.values()].sort(
    (a, b) => a.date.localeCompare(b.date) || a.kind.localeCompare(b.kind),
  );
}

/** One plot label per closing direction, rather than stacked shape/limit tags. */
export function barLimitMarker(bar: Row, kind: LimitKind) {
  const up = kind === "limit_up";
  const confirmed = confirmedBarPatterns(bar);
  const resealed =
    up && confirmed.some((event) => event.kind === "resealed_board");
  const patterns = confirmed.filter((event) =>
    up
      ? ["earth_to_sky", "one_price_limit_up"].includes(event.kind)
      : event.kind === "one_price_limit_down",
  );
  if (
    !patterns.length &&
    !resealed &&
    !confirmedBarLimits(bar).some((event) => event.kind === kind)
  )
    return null;
  const pattern =
    patterns.find((event) => event.kind === "earth_to_sky") || patterns[0];
  if (resealed)
    return {
      text: "回封",
      name: bar.noon_provisional ? "盘中回封" : "收盘涨停 · 回封",
    };
  return {
    text: pattern
      ? pattern.kind === "earth_to_sky"
        ? "地天"
        : up
          ? "一字涨"
          : "一字跌"
      : up
        ? "涨"
        : "跌",
    name: pattern
      ? `${patternLabels[pattern.kind as PatternKind]} · 收盘${up ? "涨停" : "跌停"}`
      : `收盘${up ? "涨停" : "跌停"}`,
  };
}

/** A broken-board event cannot become a closing limit-up marker. */
export function barBrokenMarker(bar: Row) {
  if (!confirmedBarPatterns(bar).some((event) => event.kind === "broken_board"))
    return null;
  return { text: "炸", name: bar.noon_provisional ? "盘中炸板" : "收盘炸板" };
}

function eventSources(events: Row[]) {
  return [
    ...new Set(
      events
        .flatMap((event) =>
          event.sources?.length ? event.sources : [event.source],
        )
        .filter(Boolean),
    ),
  ]
    .map((source) => sourceNames[String(source)] || String(source))
    .join(" / ");
}

function patternDayDetail(event: Row) {
  const price = (value: number) =>
    value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  if (["broken_board", "resealed_board"].includes(event.kind)) {
    const kind = event.kind === "broken_board" ? "炸板" : "回封";
    if (event.is_intraday)
      return `${event.date} · 所选时点${kind} · 仅依据该快照原始来源状态，不代表最终收盘`;
    return `${event.date} · 原始收盘 ${price(event.raw_close ?? event.raw_ohlc.close)} · ${kind === "炸板" ? "同日炸板来源池与原始收盘价精确核验" : "同日收盘涨停且有明确开板次数证据"}；不还原完整分时路径`;
  }
  const quote = event.raw_ohlc;
  const band = event.evidence?.limit_band;
  const reference =
    event.kind === "earth_to_sky" &&
    band?.basis ===
      "derived-symmetric-band-from-verified-close-and-previous-session-v1" &&
    positive(band.limit_down_price) &&
    positive(band.limit_up_price)
      ? `；派生跌停参考价 ${price(band.limit_down_price)} / 涨停参考价 ${price(band.limit_up_price)}（由已核验收盘与前一市场交易日价格推导，非交易所直接字段）`
      : "";
  const basis =
    event.kind === "earth_to_sky"
      ? "最低价触及有依据的当日跌停参考价，收盘经来源池核验为涨停；日线不还原首次触板、开板回封或持续封板路径"
      : `原始日K开高低收同价，且收盘经来源池核验为${event.kind === "one_price_limit_up" ? "涨停" : "跌停"}；不代表盘口封单稳定性`;
  return `${event.date} · 原始最低 ${price(quote.low)} · 原始收盘 ${price(quote.close)} · 判定依据：${basis}${reference}`;
}

export function describeBarPatterns(
  bar: Row | undefined,
  period: ChartPeriod,
  includeUnknown = false,
): LimitDescription[] {
  if (!bar) return [];
  const events = confirmedBarPatterns(bar);
  const cycle = period === "M" ? "月" : "周";
  const descriptions: LimitDescription[] = [];
  for (const kind of patternKinds) {
    const positives = events.filter((event) => event.kind === kind);
    if (!positives.length) continue;
    const source = eventSources(positives) || "已核验收盘池与原始日K";
    descriptions.push({
      text:
        period === "D"
          ? (positives.some((event) => event.is_intraday) ? "盘中" : "") +
            patternLabels[kind]
          : `${cycle}内${positives.length}日${patternLabels[kind]}`,
      detail:
        positives.map(patternDayDetail).join("；") +
        (period === "D"
          ? positives.some((event) => event.is_intraday)
            ? "；价格来自所选时点未复权原始快照"
            : "；价格依据为未复权原始日线，图上位置随展示口径换算"
          : `；只汇总具体交易日，不将整根${cycle}K称为${patternLabels[kind]}`),
      source,
      tone:
        kind === "broken_board"
          ? "warning"
          : kind === "one_price_limit_down"
            ? "down"
            : "up",
    });
  }
  if (includeUnknown) {
    const unknown = patternKinds.flatMap((kind) => {
      if (period === "D") {
        const state = bar.pattern_status?.[kind];
        return state?.status === "unknown" && state.state == null
          ? [
              `${patternLabels[kind]}：${state.reason || "原始报价或判定依据不足"}`,
            ]
          : [];
      }
      const count = bar.pattern_summary?.[kind]?.unknown_days;
      return Number.isInteger(count) && count > 0
        ? [`${patternLabels[kind]}有${count}日待核验`]
        : [];
    });
    if (unknown.length)
      descriptions.push({
        text:
          period === "D" ? "日形态仍有待核验" : `${cycle}内日形态仍有待核验`,
        detail: unknown.join("；") + "。未出现标记不等于已确认没有该形态。",
        source: "依据不足",
        tone: "muted",
      });
  }
  return descriptions;
}

export function describeBarLimits(
  bar: Row | undefined,
  period: ChartPeriod,
): LimitDescription[] {
  if (!bar) return [];
  const events = confirmedBarLimits(bar);
  const descriptions: LimitDescription[] = [];
  const cycle = period === "M" ? "月" : "周";
  for (const kind of ["limit_up", "limit_down"] as const) {
    const positives = events.filter((event) => event.kind === kind);
    if (!positives.length) continue;
    const name = kind === "limit_up" ? "涨停" : "跌停";
    const source = eventSources(positives) || "已核验来源池";
    descriptions.push({
      text:
        period !== "D"
          ? `${cycle}内${positives.length}日${name}`
          : "收盘" + name,
      detail: `${positives.map((event) => event.date).join("、")} · ${source} · 按来源收盘池与原始收盘价核验，不含触板或炸板`,
      source,
      tone: kind === "limit_up" ? "up" : "down",
    });
  }
  if (period !== "D") {
    const summary = bar.limit_event_summary || {};
    if (!descriptions.length)
      descriptions.push({
        text: `${cycle}内未留存已核验涨跌停事件`,
        detail: `只汇总本${cycle}具体交易日事件，不将整根${cycle}K称为涨跌停`,
        source: "逐日来源池",
        tone: "muted",
      });
    const unknown =
      Number(summary.unknown_up_days || 0) +
      Number(summary.unknown_down_days || 0);
    if (unknown)
      descriptions.push({
        text: `${cycle}内仍有未覆盖状态`,
        detail: `涨停状态待核验 ${summary.unknown_up_days || 0} 日；跌停状态待核验 ${summary.unknown_down_days || 0} 日。无事件标记不等于没有涨跌停。`,
        source: "覆盖不足",
        tone: "muted",
      });
    return descriptions;
  }
  if (descriptions.length) return descriptions;
  const up = bar.limit_up_state,
    down = bar.limit_down_state;
  const conflict = Object.values(bar.limit_event_status || {}).includes(
    "conflict",
  );
  if (up === false && down === false)
    return [
      {
        text: "收盘未入涨跌停池",
        detail:
          "涨停与跌停两类来源池的当日状态均已覆盖，此股票未入选；按供应商池定义，不代表交易所全A历史全集",
        source: "已核验来源池",
        tone: "muted",
      },
    ];
  return [
    {
      text: conflict
        ? "涨跌停状态存在冲突"
        : up === false
          ? "未入涨停池 · 跌停待核验"
          : down === false
            ? "涨停待核验 · 未入跌停池"
            : "涨跌停状态待核验",
      detail:
        "未覆盖或存在冲突的状态保持未知，不根据涨跌幅阈值猜测；无标记不等于未涨跌停。",
      source: conflict ? "来源冲突" : "覆盖不足",
      tone: "muted",
    },
  ];
}

function escapeHtml(value: string) {
  return value.replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ] || char,
  );
}

export function limitTooltipHtml(
  bar: Row | undefined,
  period: ChartPeriod,
  colors: { up: string; down: string; text: string; warning?: string },
) {
  return [
    ...(bar?.noon_provisional ? [] : describeBarLimits(bar, period)),
    ...describeBarPatterns(bar, period, !bar?.noon_provisional),
  ]
    .map(
      (item) =>
        `<div style="margin-top:5px;color:${item.tone === "muted" ? colors.text : item.tone === "warning" ? colors.warning || "#d89a37" : colors[item.tone]};max-width:260px;white-space:normal">${escapeHtml(item.text)} · ${escapeHtml(item.source)}<div style="font-size:10px;color:${colors.text}">${escapeHtml(item.detail)}</div></div>`,
    )
    .join("");
}

/** Reusable by the observed-day stock header; old reports remain empty. */
export function PatternStatus({
  bar,
  period = "D",
  showUnknown = false,
}: {
  bar?: Row;
  period?: ChartPeriod;
  showUnknown?: boolean;
}) {
  const descriptions = describeBarPatterns(bar, period, showUnknown);
  if (!descriptions.length) return null;
  return (
    <span className="candle-pattern-status" aria-label="当前K线日形态状态">
      {descriptions.map((item, index) => (
        <span
          key={index}
          className={item.tone}
          title={`${item.detail} · ${item.source}`}
        >
          {item.text}
        </span>
      ))}
    </span>
  );
}

export function LimitStatus({
  bar,
  period,
}: {
  bar?: Row;
  period: ChartPeriod;
}) {
  return (
    <span className="candle-limit-status" aria-label="当前K线收盘涨跌停状态">
      {describeBarLimits(bar, period).map((item, index) => (
        <span key={index} className={item.tone} title={item.detail}>
          {item.text}
          <small> · {item.source}</small>
        </span>
      ))}
    </span>
  );
}

export function limitCoverageText(coverage?: Row) {
  const counts = (name: string, record?: Row) => {
    if (!record) return name + "覆盖待核验";
    const known =
      Number(record.confirmed_days || 0) +
      Number(record.source_absent_days || 0);
    const total =
      known +
      Number(record.unknown_days || 0) +
      Number(record.conflict_days || 0);
    return `${name}状态可核验 ${known}/${total} 日`;
  };
  return counts("涨停", coverage?.up) + " · " + counts("跌停", coverage?.down);
}
