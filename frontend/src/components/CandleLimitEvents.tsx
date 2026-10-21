import type { Row } from "../lib/api";
import type { ChartPeriod } from "./candleChange";

export type LimitKind = "limit_up" | "limit_down";
export type LimitDescription = {
  text: string;
  detail: string;
  source: string;
  tone: "up" | "down" | "muted";
};

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
  colors: { up: string; down: string; text: string },
) {
  return describeBarLimits(bar, period)
    .map(
      (item) =>
        `<div style="margin-top:5px;color:${item.tone === "muted" ? colors.text : colors[item.tone]};max-width:260px;white-space:normal">${escapeHtml(item.text)} · ${escapeHtml(item.source)}<div style="font-size:10px;color:${colors.text}">${escapeHtml(item.detail)}</div></div>`,
    )
    .join("");
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
