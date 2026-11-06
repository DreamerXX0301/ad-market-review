import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  CheckCheck,
  CircleAlert,
  Clock3,
  Flag,
  Sparkles,
  Sunrise,
  TrendingUp,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Row } from "../../lib/api";
import { tone } from "../../lib/format";
import "./IntradayBriefCard.css";

type HighlightKind =
  | "leading"
  | "strengthening"
  | "weakening"
  | "sealed"
  | "broken"
  | "market"
  | "auction";
type BriefSection = {
  slot: string;
  label: string;
  status: string;
  summary: string;
  highlights: Row[];
  comparison_note?: string;
  legacy?: boolean;
};

const order = [
  "auction",
  "0935",
  "0945",
  "1000",
  "1030",
  "1100",
  "noon",
  "1330",
  "1400",
  "1430",
  "close",
];
const slotLabels: Record<string, string> = {
  auction: "09:25 竞价",
  "0935": "09:35 开盘观察",
  "0945": "09:45 早盘跟踪",
  "1000": "10:00 开盘首轮",
  "1030": "10:30 早盘一小时",
  "1100": "11:00 上午观察",
  noon: "午评",
  "1330": "13:30 午后首轮",
  "1400": "14:00 午后观察",
  "1430": "14:30 尾盘观察",
  close: "15:00 收盘复盘",
};
const kinds = {
  leading: { label: "领涨方向", icon: TrendingUp },
  strengthening: { label: "相对走强", icon: ArrowUpRight },
  weakening: { label: "相对回落", icon: ArrowDownRight },
  sealed: { label: "封板变化", icon: CheckCheck },
  broken: { label: "炸板观察", icon: CircleAlert },
  market: { label: "市场观察", icon: Activity },
  auction: { label: "竞价方向", icon: Sunrise },
} satisfies Record<HighlightKind, Row>;
const stockCount = String.raw`[ \t]*(?:(?:至少|约|≥|≤)[ \t]*)?\d[\d,，]*[ \t]*(?:只|家)`;
const metrics = new RegExp(
  [
    String.raw`(?<signed>[+\-−]\d[\d,，]*(?:\.\d+)?[ \t]*(?:%|％|(?:个)?百分点|万亿元|亿元|万元|只|家))`,
    `(?<up>(?:上涨|高开|涨幅扩大|首轮转涨|封板|涨停)${stockCount}|放量)`,
    `(?<down>(?:下跌|低开|回落|跌停)${stockCount}|缩量)`,
    `(?<warning>炸板${stockCount})`,
  ].join("|"),
  "gu",
);

/** Text remains React text. In particular source HTML and Markdown never become HTML. */
export function BriefText({ text }: { text: string }) {
  if (typeof text !== "string") return null;
  const pieces: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(metrics)) {
    const offset = match.index;
    const value = match[0];
    const count = value.match(/\d[\d,，]*/)?.[0];
    const zero = count != null && Number(count.replace(/[,，]/g, "")) === 0;
    const color = match.groups?.signed
      ? tone(Number.parseFloat(value.replace(/[,，]/g, "").replace("−", "-")))
      : zero
        ? "muted"
        : match.groups?.up
          ? "up"
          : match.groups?.down
            ? "down"
            : "intraday-brief-warning";
    if (offset > cursor) pieces.push(text.slice(cursor, offset));
    pieces.push(
      color === "muted" ? (
        value
      ) : (
        <span key={offset} className={`intraday-brief-value ${color}`}>
          {value}
        </span>
      ),
    );
    cursor = offset + value.length;
  }
  pieces.push(text.slice(cursor));
  return <>{pieces}</>;
}

const string = (value: unknown) => (typeof value === "string" ? value : "");
const escapePattern = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const sectionLabel = (slot: string, label?: unknown) =>
  string(label) || slotLabels[slot] || "本时点观察";

/** Use structured facts first. Historical plain bodies keep their original wording. */
export function briefSections(brief: Row): BriefSection[] {
  const context: Row[] = Array.isArray(brief.context)
    ? brief.context.filter((item: Row) => item && typeof item === "object")
    : [];
  const structured: BriefSection[] = [];
  const seen = new Set<string>();
  for (const row of Array.isArray(brief.sections) ? brief.sections : []) {
    if (!row || typeof row.slot !== "string" || seen.has(row.slot)) continue;
    seen.add(row.slot);
    structured.push({
      slot: row.slot,
      label: sectionLabel(row.slot, row.label),
      status: string(row.status) || "partial",
      summary: string(row.summary),
      comparison_note: string(row.comparison_note),
      highlights: (Array.isArray(row.highlights) ? row.highlights : []).filter(
        (item: Row) =>
          item && typeof item.text === "string" && item.text.trim(),
      ),
    });
  }
  if (structured.length) {
    for (const entry of context) {
      if (typeof entry.slot !== "string" || seen.has(entry.slot)) continue;
      structured.push({
        slot: entry.slot,
        label: sectionLabel(entry.slot, entry.label),
        status: entry.status === "missing" ? "missing" : "unavailable",
        summary:
          entry.status === "missing"
            ? "该时点未留存观察，无法补述当时表现。"
            : "该时点暂无可展示的分段重点。",
        highlights: [],
      });
    }
    return structured.sort((first, second) => {
      const a = order.indexOf(first.slot),
        b = order.indexOf(second.slot);
      return (a < 0 ? order.length : a) - (b < 0 ? order.length : b);
    });
  }

  const groups = new Map<string, BriefSection>();
  const body = string(brief.body);
  let current: string | null = null;
  const addLine = (
    slot: string,
    label: string,
    line: string,
    status = "complete",
  ) => {
    const existing = groups.get(slot);
    if (existing) existing.summary += (existing.summary ? "\n" : "") + line;
    else
      groups.set(slot, {
        slot,
        label,
        status,
        summary: line,
        highlights: [],
        legacy: true,
      });
  };
  for (const line of body.split(/\r?\n/)) {
    if (!line.trim()) {
      if (current && groups.has(current)) groups.get(current)!.summary += "\n";
      continue;
    }
    const entry = context.find(
      (item) =>
        typeof item.slot === "string" &&
        typeof item.label === "string" &&
        new RegExp(
          `^${escapePattern(item.label)}(?:[ \\t]*[·:：]|[ \\t]+|$)`,
        ).test(line),
    );
    if (entry) {
      current = entry.slot;
      addLine(
        entry.slot,
        entry.label,
        line.replace(
          new RegExp(`^${escapePattern(entry.label)}[ \\t]*[·:：]?[ \\t]*`),
          "",
        ),
        entry.status || "complete",
      );
      continue;
    }
    const prefix = line.match(
      /^(09:25|09:35|09:45|10:00|10:30|11:00|11:30|11:35|13:30|14:00|14:30|15:00)[ \t]*([^·:：]*?)[ \t]*[·:：][ \t]*/,
    );
    if (prefix) {
      current =
        prefix[1] === "09:25"
          ? "auction"
          : ["11:30", "11:35"].includes(prefix[1])
            ? "noon"
            : prefix[1] === "15:00"
              ? "close"
              : prefix[1].replace(":", "");
      addLine(
        current,
        `${prefix[1]} ${prefix[2]}`.trim(),
        line.slice(prefix[0].length),
      );
    } else {
      current ||= string(brief.slot) || "legacy";
      addLine(
        current,
        sectionLabel(
          current,
          context.find((item) => item.slot === current)?.label,
        ),
        line,
      );
    }
  }
  for (const entry of context) {
    if (typeof entry.slot !== "string" || groups.has(entry.slot)) continue;
    groups.set(entry.slot, {
      slot: entry.slot,
      label: sectionLabel(entry.slot, entry.label),
      status: entry.status === "missing" ? "missing" : "unavailable",
      summary:
        entry.status === "missing"
          ? "该时点未留存观察。"
          : "该时点暂无分段文字。",
      highlights: [],
      legacy: true,
    });
  }
  const result = [...groups.values()];
  if (
    result.length > 1 &&
    result.every((section) => order.includes(section.slot))
  )
    result.sort((a, b) => order.indexOf(a.slot) - order.indexOf(b.slot));
  return result;
}

/** Only this scroll box moves: neither chips nor changed observation slots scroll the document. */
export function briefSectionScrollTop(
  viewport: HTMLElement,
  target: HTMLElement,
) {
  return Math.max(
    0,
    viewport.scrollTop +
      target.getBoundingClientRect().top -
      viewport.getBoundingClientRect().top -
      12,
  );
}

/** The reading line belongs to the brief viewport, independent of page scrolling. */
export function activeBriefSection(
  viewport: HTMLElement,
  sections: Array<{ slot: string; element: HTMLElement }>,
) {
  if (viewport.clientHeight <= 0) return null;
  const visible = sections.filter(
    ({ element }) => element.getBoundingClientRect().height > 0,
  );
  if (!visible.length) return null;
  if (viewport.scrollTop <= 1) return visible[0].slot;
  // A short final section cannot always reach the reading line.
  if (viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 2)
    return visible.at(-1)!.slot;
  const readingTop = viewport.getBoundingClientRect().top + 14;
  return (
    visible
      .filter(
        ({ element }) => element.getBoundingClientRect().top <= readingTop,
      )
      .at(-1)?.slot || visible[0].slot
  );
}

function statusLabel(status: string) {
  return status === "missing"
    ? "未留存"
    : status === "partial"
      ? "部分观察"
      : status === "unavailable"
        ? "暂无重点"
        : "";
}

/** Only explicit source bindings can become links; wording never resolves a stock or sector. */
export function briefDirectionBindings(
  item: Row,
): Array<{ id: string; name: string }> {
  const supplied = [
    { sector_id: item.sector_id, name: item.sector_name },
    ...(Array.isArray(item.current_sector_counts)
      ? item.current_sector_counts.slice(0, 3)
      : []),
  ];
  const byId = new Map<string, { id: string; name: string }>();
  for (const row of supplied) {
    if (
      !row ||
      typeof row.sector_id !== "string" ||
      typeof row.name !== "string" ||
      !row.sector_id.trim() ||
      !row.name.trim() ||
      row.sector_id.length > 120 ||
      row.name.length > 100
    )
      continue;
    byId.set(row.sector_id, { id: row.sector_id, name: row.name });
  }
  const names = new Map<string, number>();
  for (const row of byId.values())
    names.set(row.name, (names.get(row.name) || 0) + 1);
  return [...byId.values()].filter((row) => names.get(row.name) === 1);
}

export function BriefDirectionText({
  item,
  slot,
  label,
  onSelectDirection,
}: {
  item: Row;
  slot: string;
  label: string;
  onSelectDirection?: (slot: string, instrumentId: string) => void;
}) {
  const text = string(item.text);
  if (!onSelectDirection || !order.includes(slot))
    return <BriefText text={text} />;
  const bindings = briefDirectionBindings(item);
  const matches = bindings
    .flatMap((binding) => {
      const found: Array<{
        start: number;
        end: number;
        id: string;
        name: string;
      }> = [];
      let start = text.indexOf(binding.name);
      while (start >= 0 && found.length < 8) {
        found.push({ ...binding, start, end: start + binding.name.length });
        start = text.indexOf(binding.name, start + binding.name.length);
      }
      return found;
    })
    .sort((a, b) => a.start - b.start || b.end - a.end);
  const nodes: ReactNode[] = [];
  let offset = 0;
  for (const match of matches) {
    if (match.start < offset) continue;
    nodes.push(
      <BriefText
        key={`text-${offset}`}
        text={text.slice(offset, match.start)}
      />,
    );
    nodes.push(
      <button
        type="button"
        className="ibt-direction-link"
        key={`${match.id}:${match.start}`}
        title={`查看 ${label} 的${match.name}个股`}
        aria-label={`查看 ${label} ${match.name}个股`}
        onClick={() => onSelectDirection(slot, match.id)}
      >
        {match.name}
        <ArrowUpRight size={11} aria-hidden="true" />
      </button>,
    );
    offset = match.end;
  }
  nodes.push(<BriefText key={`text-${offset}`} text={text.slice(offset)} />);
  return <>{nodes}</>;
}

export default function BriefCard({
  brief,
  selectedSlot,
  onSelectDirection,
}: {
  brief: Row;
  selectedSlot?: string;
  onSelectDirection?: (slot: string, instrumentId: string) => void;
}) {
  const sections = briefSections(brief);
  const preferred = selectedSlot || string(brief.slot);
  if (preferred && !sections.some((section) => section.slot === preferred)) {
    sections.push({
      slot: preferred,
      label: sectionLabel(preferred),
      status: "unavailable",
      summary: "该时点暂未生成简报，可查看此前已留存的观察。",
      highlights: [],
    });
  }
  const initial =
    sections.find((section) => section.slot === preferred)?.slot ||
    sections.at(-1)?.slot ||
    "";
  const [active, setActive] = useState(initial);
  const viewport = useRef<HTMLDivElement>(null);
  const targets = useRef(new Map<string, HTMLLIElement>());
  const scrollFrame = useRef<number | null>(null);
  const navigation = useRef<{
    slot: string;
    top: number;
    reached: boolean;
  } | null>(null);
  const id = useId();
  const signature = sections
    .map((section) => `${section.slot}:${section.status}:${section.label}`)
    .join("|");

  const navigate = (slot: string, behavior: ScrollBehavior = "smooth") => {
    const scroll = viewport.current,
      target = targets.current.get(slot);
    if (!scroll || !target) return;
    const top = Math.min(
      briefSectionScrollTop(scroll, target),
      Math.max(0, scroll.scrollHeight - scroll.clientHeight),
    );
    navigation.current = {
      slot,
      top,
      reached: Math.abs(scroll.scrollTop - top) <= 2,
    };
    setActive(slot);
    scroll.scrollTo({
      top,
      left: scroll.scrollLeft,
      behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : behavior,
    });
  };
  const syncActiveSection = () => {
    if (scrollFrame.current !== null) return;
    scrollFrame.current = window.requestAnimationFrame(() => {
      scrollFrame.current = null;
      const scroll = viewport.current;
      if (!scroll) return;
      const selected = navigation.current;
      if (selected && Math.abs(scroll.scrollTop - selected.top) <= 2) {
        selected.reached = true;
        setActive(selected.slot);
        return;
      }
      if (selected?.reached) navigation.current = null;
      const slot = activeBriefSection(
        scroll,
        sections.flatMap(({ slot }) => {
          const element = targets.current.get(slot);
          return element ? [{ slot, element }] : [];
        }),
      );
      if (slot) setActive(slot);
    });
  };
  const releaseNavigation = () => {
    navigation.current = null;
  };
  useEffect(() => {
    navigation.current = null;
    setActive(initial);
    const frame = window.requestAnimationFrame(() => navigate(initial, "auto"));
    return () => {
      window.cancelAnimationFrame(frame);
      if (scrollFrame.current !== null)
        window.cancelAnimationFrame(scrollFrame.current);
      scrollFrame.current = null;
    };
  }, [preferred, brief.slot, brief.id, brief.date, signature]);

  const evidence: Row[] = Array.isArray(brief.evidence) ? brief.evidence : [];
  const currentSection =
    Array.isArray(brief.sections) && brief.sections.length
      ? sections.find((section) => section.slot === brief.slot)
      : undefined;
  const headline = currentSection
    ? `${currentSection.label}累计简报`
    : string(brief.headline) || "盘面分时观察";
  return (
    <section
      className="panel intraday-brief intraday-brief-timeline"
      aria-label="累计盘面简报"
    >
      <header className="ibt-header">
        <div className="ibt-eyebrow">
          <span>
            <Sparkles size={14} aria-hidden="true" />
            累计盘面简报
          </span>
          <small>串联截至当前时点的观察</small>
        </div>
        <h2>
          <BriefText text={headline} />
        </h2>
        {sections.length > 0 && (
          <nav className="ibt-slots" aria-label="简报时段">
            {sections.map((section, index) => (
              <button
                type="button"
                key={section.slot}
                aria-current={active === section.slot ? "step" : undefined}
                aria-controls={`${id}-section-${index}`}
                data-status={section.status}
                title="仅浏览本简报，不切换盘面观察时点"
                onClick={() => navigate(section.slot)}
              >
                <span className="ibt-slot-dot" aria-hidden="true" />
                {section.label}
                {statusLabel(section.status) && (
                  <small>{statusLabel(section.status)}</small>
                )}
              </button>
            ))}
          </nav>
        )}
      </header>
      <div
        className="ibt-scroll"
        ref={viewport}
        tabIndex={0}
        role="region"
        aria-label="分时段简报内容"
        onScroll={(event) => {
          if (event.target === event.currentTarget) syncActiveSection();
        }}
        onWheel={releaseNavigation}
        onTouchStart={releaseNavigation}
        onPointerDown={releaseNavigation}
        onKeyDown={(event) => {
          if (
            [
              "ArrowUp",
              "ArrowDown",
              "PageUp",
              "PageDown",
              "Home",
              "End",
              " ",
            ].includes(event.key)
          )
            releaseNavigation();
        }}
      >
        {sections.length ? (
          <ol className="ibt-sections">
            {sections.map((section, index) => (
              <li
                key={section.slot}
                id={`${id}-section-${index}`}
                className={`ibt-section${active === section.slot ? " is-active" : ""}`}
                data-slot={section.slot}
                data-status={section.status}
                ref={(node) => {
                  if (node) targets.current.set(section.slot, node);
                  else targets.current.delete(section.slot);
                }}
              >
                <div className="ibt-section-heading">
                  <h3>
                    <Clock3 size={13} aria-hidden="true" />
                    {section.label}
                  </h3>
                  {statusLabel(section.status) && (
                    <span>{statusLabel(section.status)}</span>
                  )}
                  {active === section.slot && (
                    <span className="ibt-active-label">正在查看</span>
                  )}
                </div>
                {section.summary && (
                  <p
                    className={`ibt-summary${section.legacy ? " is-legacy" : ""}`}
                  >
                    <BriefText text={section.summary} />
                  </p>
                )}
                {section.highlights.length > 0 && (
                  <ul className="ibt-highlights">
                    {section.highlights.map((item, number) => {
                      const kind: HighlightKind =
                        typeof item.kind === "string" &&
                        Object.hasOwn(kinds, item.kind)
                          ? (item.kind as HighlightKind)
                          : "market";
                      const { icon: Icon, label } = kinds[kind];
                      return (
                        <li
                          className={`ibt-highlight ibt-${kind}`}
                          key={item.id || number}
                        >
                          <span className="ibt-kind">
                            <Icon size={13} aria-hidden="true" />
                            {label}
                          </span>
                          <p>
                            <BriefDirectionText
                              item={item}
                              slot={section.slot}
                              label={section.label}
                              onSelectDirection={onSelectDirection}
                            />
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                )}
                {section.comparison_note && (
                  <p className="ibt-comparison">
                    <Flag size={12} aria-hidden="true" />
                    <BriefText text={section.comparison_note} />
                  </p>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="ibt-empty">该时点暂未生成可展示的简报。</p>
        )}
        {evidence.length > 0 && (
          <details className="intraday-brief-evidence ibt-evidence">
            <summary>
              查看核验依据 <small>{evidence.length} 条</small>
            </summary>
            <ul>
              {evidence.map((item, index) => (
                <li key={item.id || index}>
                  {item.label && <strong>{string(item.label)} </strong>}
                  <BriefText text={string(item.text)} />
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </section>
  );
}

export { BriefCard };
