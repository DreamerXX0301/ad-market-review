import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Clock3,
  RefreshCw,
  ArrowUpRight,
  Layers3,
  CircleAlert,
  Filter,
  GitCompareArrows,
  TrendingUp,
} from "lucide-react";
import type { ReactNode } from "react";
import { Empty, ErrorBox, Loading, Panel, Warning } from "../../components/UI";
import CopyStockCode from "../../components/CopyStockCode";
import BriefCard from "./IntradayBriefCard";
export { default as BriefCard } from "./IntradayBriefCard";
import IntradayStockStateBadge, {
  intradayStockStateBadges,
} from "./IntradayStockStateBadge";
import PageScrollEdges from "../../components/PageScrollEdges";
import AIEntry from "../ai/AIEntry";
import { aiMarketStage } from "../ai/aiNavigation";
import { post, requestKey, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import { amount, integer, num, percent, tone } from "../../lib/format";
import {
  boardLabel,
  boardOf,
  filterRows,
  NOON_BOARD_OPTIONS,
} from "./noonBoardPresentation";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import { MarketSortHeader } from "./MarketTable";
import { sortMarketRows } from "./marketPresentation";
import type { MarketSort } from "./marketPresentation";
import IntradayAutomation from "./IntradayAutomation";
import AuctionHeightReference from "./AuctionHeightReference";
import AuctionDiscovery from "./AuctionDiscovery";
import { isStructuralLadderTopic } from "./ladderBoardPresentation";
import "./IntradayObservation.css";

const activeStatus = (value: unknown) =>
  value === "queued" || value === "running";
const clock = (value: unknown) =>
  typeof value === "string"
    ? value.match(/T(\d\d:\d\d:\d\d)/)?.[1] || "—"
    : "—";
const delta = (value: number | null | undefined, unit: string) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${num(value)} ${unit}`;

const evidenceCount = (value: number | null | undefined, state: unknown) =>
  value == null ? "—" : `${state === "partial" ? "≥" : ""}${integer(value)}`;

type OpenStock = (
  id: string,
  context?: { slot: string; snapshotId?: string },
) => void;
type StockHistoryScope = { date: string; slot: string };
type MemberScope = StockHistoryScope & {
  snapshotId: string;
  topicCode: string;
};

const stockHistoryQuery = ({ date, slot }: StockHistoryScope, rows: Row[]) =>
  `/market/intraday/history?${new URLSearchParams({
    date,
    slot,
    symbols: rows
      .map((row) => row.instrument_id)
      .sort()
      .join(","),
  })}`;

const historySortFields = [
  "change_30d_pct",
  "return_ytd_pct",
  "last_limit_up_date",
  "limit_up_1y",
];

function ThemeChips({
  row,
  showMissing = false,
}: {
  row: Row;
  showMissing?: boolean;
}) {
  const themes: Row[] =
    row.themes?.length > 0
      ? row.themes
      : ((row.board_names || []) as string[]).map((name) => ({ name }));
  if (!themes.length) {
    return showMissing ? (
      <small className="intraday-theme-missing">题材未核验</small>
    ) : null;
  }
  return (
    <span className="intraday-theme-chips">
      {themes.slice(0, 3).map((theme, i) => (
        <span
          key={`${theme.name}:${i}`}
          data-kind={theme.kind}
          title={
            theme.member_date ? `名单留存日 ${theme.member_date}` : undefined
          }
        >
          {theme.name}
        </span>
      ))}
      {themes.length > 3 && (
        <span
          title={themes
            .slice(3)
            .map((theme) => theme.name)
            .join("、")}
        >
          +{themes.length - 3}
        </span>
      )}
    </span>
  );
}

function TurnoverComparison({
  comparison,
  period,
}: {
  comparison?: Row;
  period: "同档" | "同段";
}) {
  if (!comparison || comparison.change_from_previous == null)
    return <small>前一交易日{period}暂无可比成交额</small>;
  const difference = comparison.change_from_previous;
  return (
    <small
      className="intraday-turnover-change"
      title={[
        comparison.previous_date && `对照 ${comparison.previous_date}`,
        `昨日可比成交额 ${amount(comparison.previous_amount)}`,
        `今日可比成交额 ${amount(comparison.comparison_amount)}`,
      ]
        .filter(Boolean)
        .join(" · ")}
    >
      {comparison.label || `较前一交易日${period}`}{" "}
      <b className="intraday-turnover-delta" data-direction={tone(difference)}>
        {difference === 0
          ? "持平"
          : `${difference > 0 ? "放量" : "缩量"} ${amount(Math.abs(difference))}`}
      </b>
    </small>
  );
}

function MarketPulse({ pulse, interval }: { pulse: Row; interval?: Row }) {
  const breadth = pulse.breadth;
  const turnover = pulse.turnover;
  return (
    <section className="intraday-pulse-panel" aria-label="市场背景">
      <div className="intraday-market-pulse">
        {(pulse.indices || []).map((row: Row) => (
          <div key={row.instrument_id} className="intraday-metric">
            <span>{row.name}</span>
            <strong className={tone(row.change_pct)}>
              {percent(row.change_pct)}
            </strong>
            <small>
              {row.latest_price == null ? "点位未取得" : num(row.latest_price)}
            </small>
            <div className="intraday-turnover-lines">
              <small>
                累计成交额 <b>{amount(row.amount)}</b>
              </small>
              <TurnoverComparison
                comparison={row.amount_comparison}
                period="同档"
              />
              {row.interval_amount?.amount != null && (
                <>
                  <small>
                    本段成交额 <b>{amount(row.interval_amount.amount)}</b>
                  </small>
                  <TurnoverComparison
                    comparison={row.interval_amount}
                    period="同段"
                  />
                </>
              )}
            </div>
          </div>
        ))}
        {breadth && (
          <div className="intraday-metric">
            <span>上涨 / 下跌 / 平盘</span>
            <strong>
              <span className="up">{integer(breadth.up)}</span> /{" "}
              <span className="down">{integer(breadth.down)}</span> /{" "}
              {integer(breadth.flat)}
            </strong>
            <small>
              可比 {integer(breadth.valid)} / {integer(breadth.observed)} 只
            </small>
            {breadth.valid > 0 && (
              <div className="intraday-breadth-bar" aria-hidden="true">
                <i style={{ flexGrow: breadth.up }} data-kind="up" />
                <i style={{ flexGrow: breadth.down }} data-kind="down" />
                <i style={{ flexGrow: breadth.flat }} data-kind="flat" />
              </div>
            )}
          </div>
        )}
        {turnover && (
          <div className="intraday-metric">
            <span>样本累计成交额</span>
            <strong>{amount(turnover.amount)}</strong>
            <small>已取得 {integer(turnover.valid)} 只</small>
            <TurnoverComparison
              comparison={turnover.amount_comparison}
              period="同档"
            />
          </div>
        )}
        {interval && (
          <div className="intraday-metric">
            <span>本段新增成交额</span>
            <strong>{amount(interval.amount)}</strong>
            {interval.amount == null ? (
              <small>区间端点未核验</small>
            ) : (
              <TurnoverComparison comparison={interval} period="同段" />
            )}
          </div>
        )}
      </div>
    </section>
  );
}

const relayLabels: Record<string, string> = {
  promoted: "已核验晋级",
  sealed: "本轮封板",
  not_in_verified_pool: "本轮未入池",
  unknown: "状态待核验",
};

function StockSamples({
  rows,
  onStock,
  broken = false,
}: {
  rows: Row[];
  onStock?: (id: string) => void;
  broken?: boolean;
}) {
  if (!rows.length) return null;
  return (
    <ul className="intraday-feedback-stocks">
      {rows.map((row) => (
        <li key={row.instrument_id}>
          <div className="intraday-feedback-stock-identity">
            {onStock ? (
              <button
                type="button"
                className="stock-link"
                onClick={() => onStock(row.instrument_id)}
              >
                {row.name || row.instrument_id}
              </button>
            ) : (
              <span>{row.name || row.instrument_id}</span>
            )}
            <CopyStockCode instrumentId={row.instrument_id} />
          </div>
          <strong className={tone(row.change_pct)}>
            {percent(row.change_pct)}
          </strong>
          {(intradayStockStateBadges(row, broken).length > 0 ||
            row.previous_board_count != null ||
            Array.isArray(row.themes) ||
            !!row.board_names?.length) && (
            <div className="intraday-feedback-stock-meta">
              <IntradayStockStateBadge row={row} broken={broken} />
              <ThemeChips row={row} showMissing={Array.isArray(row.themes)} />
              {row.previous_board_count != null && (
                <small>
                  前日 {integer(row.previous_board_count)} 板 ·{" "}
                  {relayLabels[row.state] || "待核验"}
                </small>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

type FeedbackGrouping = "original" | "industry" | "concept";
type FeedbackThemeKind = Exclude<FeedbackGrouping, "original">;

export function feedbackThemeGroups(rows: Row[], kind: FeedbackThemeKind) {
  const groups = new Map<string, Row[]>();
  const missing: Row[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    if (seen.has(row.instrument_id)) continue;
    seen.add(row.instrument_id);
    const names = new Set(
      ((row.themes || []) as Row[])
        .filter((theme) => theme.kind === kind && theme.name)
        .map((theme) => String(theme.name)),
    );
    if (!names.size) missing.push(row);
    for (const name of names) {
      const group = groups.get(name);
      if (group) group.push(row);
      else groups.set(name, [row]);
    }
  }
  const sorted = [...groups].sort(
    ([a, first], [b, second]) =>
      second.length - first.length || a.localeCompare(b, "zh-CN"),
  );
  const attributes =
    kind === "concept"
      ? sorted.filter(([name]) => isStructuralLadderTopic({ name }))
      : [];
  const ordered =
    kind === "concept"
      ? sorted.filter(([name]) => !isStructuralLadderTopic({ name }))
      : sorted;
  const featured = ordered
    .filter(([, items]) => kind === "industry" || items.length >= 2)
    .slice(0, 3);
  return {
    groups,
    ordered,
    featured,
    remaining: ordered.slice(featured.length),
    attributes,
    missing,
  };
}

export function filterFeedbackTheme(
  rows: Row[],
  kind: FeedbackThemeKind,
  name: string | null,
) {
  return name ? (feedbackThemeGroups(rows, kind).groups.get(name) ?? []) : rows;
}

function FeedbackThemeLeaders({
  rows,
  label,
  selected,
  onSelect,
}: {
  rows: Row[];
  label: string;
  selected: string | null;
  onSelect: (name: string | null) => void;
}) {
  const { featured, remaining, attributes } = feedbackThemeGroups(
    rows,
    "concept",
  );
  if (!featured.length && !remaining.length && !attributes.length) return null;
  const renderChip = ([name, items]: [string, Row[]]) => {
    const priced = items.filter((row) => Number.isFinite(row.change_pct));
    const average = priced.length
      ? priced.reduce((sum, row) => sum + row.change_pct, 0) / priced.length
      : null;
    return (
      <button
        type="button"
        className="intraday-feedback-leader"
        key={name}
        aria-pressed={selected === name}
        onClick={() => onSelect(selected === name ? null : name)}
      >
        <Filter size={11} aria-hidden="true" />
        <span>{name}</span>
        <b>{integer(items.length)} 只</b>
        {average != null && (
          <small title={`按 ${integer(priced.length)} 只有效报价计算`}>
            样本均涨幅 <b className={tone(average)}>{percent(average)}</b>
          </small>
        )}
      </button>
    );
  };
  return (
    <div
      className="intraday-feedback-leaders"
      role="group"
      aria-label={label + "筛选"}
    >
      <span>{label} · 点击筛选本卡</span>
      {featured.map(renderChip)}
      {remaining.length > 0 && (
        <details className="intraday-feedback-more intraday-feedback-leaders-more">
          <summary>更多概念 · {integer(remaining.length)} 个</summary>
          <div className="intraday-feedback-leaders">
            {remaining.map(renderChip)}
          </div>
        </details>
      )}
      {attributes.length > 0 && (
        <details className="intraday-feedback-more intraday-feedback-leaders-more">
          <summary>更多属性 · {integer(attributes.length)} 个</summary>
          <div className="intraday-feedback-leaders">
            {attributes.map(renderChip)}
          </div>
        </details>
      )}
    </div>
  );
}

function FeedbackGroupingPicker({
  grouping,
  onChange,
}: {
  grouping: FeedbackGrouping;
  onChange: (grouping: FeedbackGrouping) => void;
}) {
  const options = [
    ["original", "原分组"],
    ["industry", "按行业"],
    ["concept", "按概念"],
  ] as const;
  return (
    <div
      className="intraday-feedback-grouping intraday-filters"
      role="group"
      aria-label="留存个股分组"
    >
      {options.map(([value, label]) => (
        <button
          type="button"
          className="btn small"
          key={value}
          aria-pressed={grouping === value}
          onClick={() => onChange(value)}
        >
          {label}
        </button>
      ))}
      {grouping !== "original" && (
        <small>
          {grouping === "concept"
            ? "按留存个股统计，同一股票可归入多个概念。"
            : "按留存个股统计，行业取已核验的主要归属。"}
        </small>
      )}
    </div>
  );
}

function FeedbackSamples({
  grouping = "original",
  ...props
}: {
  rows: Row[];
  onStock?: (id: string) => void;
  broken?: boolean;
  grouping?: FeedbackGrouping;
}) {
  const { rows } = props;
  if (grouping !== "original" && rows.length > 0) {
    const { ordered, featured, remaining, attributes, missing } =
      feedbackThemeGroups(rows, grouping);
    const renderGroup = ([name, items]: [string, Row[]], index: number) => (
      <details
        className="intraday-feedback-theme"
        key={name}
        open={index === 0 || undefined}
      >
        <summary>
          <span>{name}</span>
          <span>{integer(items.length)} 只</span>
        </summary>
        <FeedbackSamples {...props} rows={items} />
      </details>
    );
    return (
      <div className="intraday-feedback-themes">
        {(grouping === "concept" ? featured : ordered).map(renderGroup)}
        {grouping === "concept" && remaining.length > 0 && (
          <details className="intraday-feedback-more intraday-feedback-more-themes">
            <summary>更多概念 · {integer(remaining.length)} 个</summary>
            <div className="intraday-feedback-themes">
              {remaining.map((group) => renderGroup(group, -1))}
            </div>
          </details>
        )}
        {attributes.length > 0 && (
          <details className="intraday-feedback-more intraday-feedback-more-themes">
            <summary>更多属性 · {integer(attributes.length)} 个</summary>
            <div className="intraday-feedback-themes">
              {attributes.map((group) => renderGroup(group, -1))}
            </div>
          </details>
        )}
        {missing.length > 0 &&
          renderGroup(
            [grouping === "industry" ? "行业未核验" : "概念未核验", missing],
            -1,
          )}
      </div>
    );
  }
  const remaining = rows.slice(4);
  return (
    <>
      <StockSamples {...props} rows={rows.slice(0, 4)} />
      {remaining.length > 0 && (
        <details className="intraday-feedback-more">
          <summary>其余 {integer(remaining.length)} 只留存个股</summary>
          <StockSamples {...props} rows={remaining} />
        </details>
      )}
    </>
  );
}

function FeedbackCard({
  title,
  icon,
  stats,
  empty,
  themeLeaders,
  children,
}: {
  title: string;
  icon: ReactNode;
  stats: Array<{ label: string; value: string; kind?: string }>;
  empty?: string;
  themeLeaders?: ReactNode;
  children: ReactNode;
}) {
  return (
    <details className="intraday-feedback-card" open>
      <summary>
        <span className="intraday-feedback-title">
          {icon}
          <strong>{title}</strong>
        </span>
        {empty ? (
          <span className="intraday-feedback-note">{empty}</span>
        ) : (
          <span className="intraday-feedback-stats">
            {stats.map(({ label, value, kind }) => (
              <span key={label} data-kind={kind}>
                {label} <b>{value}</b>
              </span>
            ))}
          </span>
        )}
      </summary>
      <div className="intraday-feedback-body">
        {themeLeaders}
        {children}
      </div>
    </details>
  );
}

function BrokenBoardDetails({
  pool,
  onStock,
}: {
  pool: Row;
  onStock?: (id: string) => void;
}) {
  const [kind, setKind] = useState<FeedbackThemeKind>("industry");
  const [selected, setSelected] = useState<string | null>(null);
  const rows = (pool.items || []) as Row[];
  const { groups, featured, remaining, attributes, missing } =
    feedbackThemeGroups(rows, kind);
  const visible = [...(selected ? (groups.get(selected) ?? []) : rows)].sort(
    (a, b) =>
      (Number.isFinite(b.change_pct) ? b.change_pct : -Infinity) -
      (Number.isFinite(a.change_pct) ? a.change_pct : -Infinity),
  );
  const renderRank = ([name, members]: [string, Row[]]) => {
    const share = (members.length / rows.length) * 100;
    return (
      <li key={name}>
        <button
          type="button"
          aria-pressed={selected === name}
          onClick={() => setSelected(selected === name ? null : name)}
        >
          <span>{name}</span>
          <b>
            {integer(members.length)} 只 <small>{share.toFixed(1)}%</small>
          </b>
          <i aria-hidden="true">
            <span style={{ width: `${share}%` }} />
          </i>
        </button>
      </li>
    );
  };
  return (
    <FeedbackCard
      title="盘中炸板"
      icon={<CircleAlert size={16} aria-hidden="true" />}
      stats={[
        {
          label: "炸板",
          value: evidenceCount(pool.count, pool.state),
          kind: "broken",
        },
      ]}
    >
      {pool.conflict_count > 0 && (
        <p className="intraday-feedback-note">
          {integer(pool.conflict_count)} 只池间状态冲突，待核验。
        </p>
      )}
      {rows.length > 0 ? (
        <>
          <div className="intraday-breakout-compare">
            <div className="intraday-breakout-heading">
              <b>哪里炸板多</b>
              <div
                className="intraday-filters"
                role="group"
                aria-label="炸板分布口径"
              >
                {(
                  [
                    ["industry", "行业"],
                    ["concept", "概念"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className="btn small"
                    aria-pressed={kind === value}
                    onClick={() => {
                      setKind(value);
                      setSelected(null);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <p className="intraday-feedback-note">
              按留存标签统计 · 分母为已列出的 {integer(rows.length)} 只
              {kind === "concept" ? " · 概念可重叠" : ""}
            </p>
            {featured.length > 0 && (
              <ol className="intraday-breakout-ranks">
                {featured.map(renderRank)}
              </ol>
            )}
            {remaining.length > 0 && (
              <details className="intraday-feedback-more intraday-feedback-more-themes">
                <summary>
                  更多{kind === "industry" ? "行业" : "概念"} ·{" "}
                  {integer(remaining.length)} 个
                </summary>
                <ol className="intraday-breakout-ranks">
                  {remaining.map(renderRank)}
                </ol>
              </details>
            )}
            {attributes.length > 0 && (
              <details className="intraday-feedback-more intraday-feedback-more-themes">
                <summary>更多属性 · {integer(attributes.length)} 个</summary>
                <ol className="intraday-breakout-ranks">
                  {attributes.map(renderRank)}
                </ol>
              </details>
            )}
            {!groups.size && (
              <p className="intraday-feedback-note">
                这批个股暂无可核验的{kind === "industry" ? "行业" : "概念"}
                归属。
              </p>
            )}
            {missing.length > 0 && (
              <small className="intraday-feedback-note">
                {integer(missing.length)} 只归属未核验
              </small>
            )}
          </div>
          <details
            className="intraday-feedback-more intraday-breakout-stocks"
            open={selected != null || undefined}
          >
            <summary>
              {selected
                ? `${selected} · ${integer(visible.length)} 只`
                : `查看 ${integer(visible.length)} 只炸板个股`}
            </summary>
            <StockSamples rows={visible} onStock={onStock} broken />
          </details>
        </>
      ) : (
        <p className="intraday-feedback-note">
          {pool.state === "unknown"
            ? "本轮炸板池待核验。"
            : pool.count === 0
              ? "本轮已核验的炸板池为空。"
              : "这个时点仅有已核验家数，未留存可展示的个股。"}
        </p>
      )}
    </FeedbackCard>
  );
}

function LadderCard({
  ladder,
  onStock,
}: {
  ladder: Row;
  onStock?: (id: string) => void;
}) {
  const [grouping, setGrouping] = useState<FeedbackGrouping>("original");
  const [selected, setSelected] = useState<string | null>(null);
  const groups: Row[] = ladder.groups || [];
  const rows = groups.flatMap((group) => (group.items || []) as Row[]);
  return (
    <FeedbackCard
      title="盘中封板梯队"
      icon={<Layers3 size={16} aria-hidden="true" />}
      themeLeaders={
        <FeedbackThemeLeaders
          rows={rows}
          label="封板题材"
          selected={selected}
          onSelect={setSelected}
        />
      }
      stats={[
        {
          label: "封板",
          value: evidenceCount(ladder.sealed_count, ladder.state),
          kind: "sealed",
        },
        {
          label: "首板",
          value: evidenceCount(ladder.first_board_count, ladder.height_state),
        },
        {
          label: "连板",
          value: evidenceCount(ladder.multi_board_count, ladder.height_state),
        },
        {
          label: "最高",
          value:
            ladder.highest_board == null
              ? "—"
              : `${evidenceCount(ladder.highest_board, ladder.height_state)} 板`,
        },
      ]}
    >
      <p className="intraday-feedback-note">
        本轮采集区间的已核验封板，≥ 表示已确认下界，— 表示待核验。
        {ladder.unknown_height_count > 0 &&
          ` ${evidenceCount(ladder.unknown_height_count, ladder.state)} 只封板股高度待核验。`}
      </p>
      {rows.length > 0 && (
        <FeedbackGroupingPicker
          grouping={selected ? "original" : grouping}
          onChange={(next) => {
            setSelected(null);
            setGrouping(next);
          }}
        />
      )}
      {!selected && grouping !== "original" ? (
        <FeedbackSamples rows={rows} onStock={onStock} grouping={grouping} />
      ) : (
        <div className="intraday-ladder-groups">
          {groups.map((group) => {
            const items = filterFeedbackTheme(
              group.items || [],
              "concept",
              selected,
            );
            if (selected && !items.length) return null;
            return (
              <div className="intraday-feedback-group" key={group.board_count}>
                <strong>
                  {integer(group.board_count)} 板 ·{" "}
                  {selected
                    ? `匹配 ${integer(items.length)}`
                    : evidenceCount(group.count, ladder.height_state)}{" "}
                  只
                </strong>
                <FeedbackSamples rows={items} onStock={onStock} />
              </div>
            );
          })}
        </div>
      )}
      {ladder.omitted_groups > 0 && (
        <p className="intraday-feedback-note">仅展示主要梯队和代表股。</p>
      )}
    </FeedbackCard>
  );
}

function PriorSessionCard({
  prior,
  priorBroken,
  onStock,
}: {
  prior: Row;
  priorBroken?: Row;
  onStock?: (id: string) => void;
}) {
  const [grouping, setGrouping] = useState<FeedbackGrouping>("original");
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <FeedbackCard
      title="前日涨停反馈"
      icon={<TrendingUp size={16} aria-hidden="true" />}
      themeLeaders={
        <FeedbackThemeLeaders
          rows={prior.items || []}
          label="前日涨停题材"
          selected={selected}
          onSelect={setSelected}
        />
      }
      empty={prior.state === "unknown" ? "前日收盘样本待核验" : undefined}
      stats={[
        { label: "样本", value: evidenceCount(prior.total, prior.state) },
        {
          label: "本轮封板",
          value: evidenceCount(prior.sealed_count, prior.sealed_state),
          kind: "sealed",
        },
        {
          label: "晋级",
          value: evidenceCount(prior.promotion_count, prior.promotion_state),
          kind: "sealed",
        },
      ]}
    >
      <p className="intraday-feedback-note">
        {prior.date
          ? `前一交易日 ${prior.date} 收盘涨停样本。`
          : "未取得前一交易日的收盘涨停样本。"}
        {prior.state !== "unknown" &&
          `本轮有可比报价 ${integer(prior.priced)} 只，上涨 ${integer(prior.up)}、下跌 ${integer(prior.down)}、平盘 ${integer(prior.flat)}，平均 ${percent(prior.average_change_pct)}。`}
        晋级要求本轮已核验封板且板数比前日增加一板。
      </p>
      {!!prior.groups?.length && (
        <div className="intraday-table-scroll intraday-feedback-table">
          <table>
            <thead>
              <tr>
                <th scope="col">前日梯队</th>
                <th scope="col">可比 / 样本</th>
                <th scope="col">涨 / 跌 / 平</th>
                <th scope="col">平均涨幅</th>
                <th scope="col">本轮封板</th>
                <th scope="col">本轮晋级</th>
              </tr>
            </thead>
            <tbody>
              {prior.groups.map((group: Row) => (
                <tr key={group.key}>
                  <th scope="row">{group.label}</th>
                  <td>
                    {integer(group.priced)} /{" "}
                    {evidenceCount(group.total, prior.state)}
                  </td>
                  <td>
                    {integer(group.up)} / {integer(group.down)} /{" "}
                    {integer(group.flat)}
                  </td>
                  <td className={tone(group.average_change_pct)}>
                    {percent(group.average_change_pct)}
                  </td>
                  <td>
                    {evidenceCount(group.sealed_count, group.sealed_state)}
                  </td>
                  <td>
                    {evidenceCount(
                      group.promotion_count,
                      group.promotion_state,
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <FeedbackGroupingPicker
        grouping={selected ? "original" : grouping}
        onChange={(next) => {
          setSelected(null);
          setGrouping(next);
        }}
      />
      <FeedbackSamples
        rows={filterFeedbackTheme(prior.items || [], "concept", selected)}
        onStock={onStock}
        grouping={selected ? "original" : grouping}
      />
      {!selected && priorBroken && (
        <div className="intraday-feedback-group intraday-prior-broken">
          <strong>前日炸板反馈</strong>
          <p className="intraday-feedback-note">
            {priorBroken.state === "unknown"
              ? "前日收盘炸板样本待核验。"
              : `样本 ${evidenceCount(priorBroken.total, priorBroken.state)} · 可比 ${integer(priorBroken.priced)} 只 · 平均 ${percent(priorBroken.average_change_pct)} · 本轮封板 ${evidenceCount(priorBroken.sealed_count, priorBroken.sealed_state)}`}
          </p>
          <FeedbackSamples
            rows={priorBroken.items || []}
            onStock={onStock}
            grouping={grouping}
          />
        </div>
      )}
    </FeedbackCard>
  );
}

function SameDayCard({
  comparison,
  onStock,
}: {
  comparison: Row;
  onStock?: (id: string) => void;
}) {
  const [grouping, setGrouping] = useState<FeedbackGrouping>("original");
  const [selected, setSelected] = useState<string | null>(null);
  const groups = [
    ["entered", "本轮新入池"],
    ["absent", "本轮未入池"],
    ["held", "两轮均入池"],
  ];
  const count = (key: string) =>
    evidenceCount(
      comparison[`${key}_count`],
      comparison[`${key}_state`] ?? comparison.state,
    );
  return (
    <FeedbackCard
      title="同日封板变化"
      icon={<GitCompareArrows size={16} aria-hidden="true" />}
      themeLeaders={
        <FeedbackThemeLeaders
          rows={groups.flatMap(([key]) => (comparison[key] || []) as Row[])}
          label="封板变化题材"
          selected={selected}
          onSelect={setSelected}
        />
      }
      empty={
        comparison.state === "unknown"
          ? comparison.collected_end_at
            ? "两轮池状态待核验"
            : "等待同日两份快照"
          : undefined
      }
      stats={groups.map(([key, label]) => ({
        label,
        value: count(key),
        kind:
          key === "entered"
            ? "sealed"
            : key === "absent"
              ? "broken"
              : undefined,
      }))}
    >
      <p className="intraday-feedback-note">
        {comparison.collected_end_at
          ? `对照 ${comparison.label || "上一份快照"} ${clock(comparison.collected_end_at)}。比较仅覆盖两次采样，具体开板时间待核验。`
          : "同一交易日取得两份盘中快照后显示。"}
      </p>
      <FeedbackGroupingPicker
        grouping={selected ? "original" : grouping}
        onChange={(next) => {
          setSelected(null);
          setGrouping(next);
        }}
      />
      {groups.map(([key, label]) => {
        const items = filterFeedbackTheme(
          comparison[key] || [],
          "concept",
          selected,
        );
        return (
          items.length > 0 && (
            <div className="intraday-feedback-group" key={key}>
              <strong>
                {label} ·{" "}
                {selected ? `匹配 ${integer(items.length)}` : count(key)} 只
              </strong>
              <FeedbackSamples
                rows={items}
                onStock={onStock}
                grouping={selected ? "original" : grouping}
              />
            </div>
          )
        );
      })}
    </FeedbackCard>
  );
}

const boardChangeKinds = [
  {
    key: "new_sealed",
    label: "新增封板",
    kind: "sealed",
    mode: "sealed_stocks",
  },
  {
    key: "new_limit_down",
    label: "新增跌停",
    kind: "down",
    mode: "candidates",
  },
  {
    key: "new_broken",
    label: "新增炸板",
    kind: "broken",
    mode: "broken_stocks",
  },
  { key: "opened", label: "封板打开", kind: "broken", mode: "broken_stocks" },
  { key: "resealed", label: "炸板回封", kind: "sealed", mode: "sealed_stocks" },
] as const;

function boardChangeReason(group?: Row) {
  if (group?.state === "observed")
    return group.count === 0
      ? "两档核对后，没有该类变化。"
      : "两档完整名单已核对。";
  if (group?.state === "partial") return "以下为已确认变化，家数是已确认下界。";
  if (group?.reason_code === "no_saved_down_pool")
    return "这两档没有完整的跌停池记录，暂不能核对新增跌停。";
  if (group?.reason_code === "missing_adjacent_snapshot")
    return "取得同日相邻两档快照后显示，不跨缺档比较。";
  return "这两档的对应板况记录不足，暂不能确认变化。";
}

export function IntradayBoardChanges({
  snapshot,
  onStock,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
}) {
  const changes = snapshot.board_changes;
  const availableKinds = boardChangeKinds.filter(
    ({ key }) => changes?.groups?.[key]?.count != null,
  );
  const [selected, setSelected] = useState<string>(
    () =>
      boardChangeKinds.find(({ key }) => changes?.groups?.[key]?.items?.length)
        ?.key || "new_sealed",
  );
  const [theme, setTheme] = useState<{
    kind: FeedbackThemeKind;
    name: string;
  } | null>(null);
  if (!changes?.baseline_slot || !availableKinds.length) return null;
  const activeKind =
    availableKinds.find(({ key }) => key === selected) || availableKinds[0];
  const group = changes.groups?.[activeKind.key];
  const choice = activeKind;
  const groupItems = (group?.items || []) as Row[];
  const industryLeaders = feedbackThemeGroups(
    groupItems,
    "industry",
  ).ordered.slice(0, 3);
  const conceptLeaders = feedbackThemeGroups(
    groupItems,
    "concept",
  ).featured.slice(0, 3);
  const selectedRows = theme
    ? filterFeedbackTheme(groupItems, theme.kind, theme.name)
    : groupItems;
  const previous = changes.source_bindings?.previous;
  const current = changes.source_bindings?.current;
  const period =
    previous && current
      ? `${clock(previous.collected_end_at).slice(0, 5)} → ${clock(current.collected_end_at).slice(0, 5)}`
      : "等待相邻时点";
  return (
    <Panel
      title={
        <>
          <GitCompareArrows size={17} aria-hidden="true" /> 本段板况变化{" "}
          <span className="intraday-change-period">{period}</span>
        </>
      }
      subtitle="点击类别查看对应股票 · 对比上一档已留存板况"
      className="intraday-board-changes"
    >
      <div
        className="intraday-change-tabs"
        role="group"
        aria-label="选择板况变化"
      >
        {availableKinds.map(({ key, label, kind }) => {
          const item = changes.groups?.[key];
          return (
            <button
              type="button"
              key={key}
              data-kind={kind}
              data-state={item?.state || "unknown"}
              aria-pressed={activeKind.key === key}
              onClick={() => {
                setSelected(key);
                setTheme(null);
              }}
            >
              <span>{label}</span>
              <strong>
                {evidenceCount(item.count, item.state)}
                <small>只</small>
              </strong>
              <small>
                {item?.state === "observed" ? "已核对" : "已确认下界"}
              </small>
            </button>
          );
        })}
      </div>
      <div className="intraday-change-content">
        <p className="intraday-change-note" role="status">
          {boardChangeReason(group)}
        </p>
        {groupItems.length > 0 &&
          (industryLeaders.length > 0 || conceptLeaders.length > 0) && (
            <div
              className="intraday-change-theme-summary"
              aria-label={`${choice.label}行业题材分布`}
            >
              <div className="intraday-change-theme-heading">
                <strong>{choice.label}集中方向</strong>
                <small>
                  按下方 {integer(groupItems.length)} 只已列股票统计，概念可重叠
                </small>
                {theme && (
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => setTheme(null)}
                  >
                    清除筛选
                  </button>
                )}
              </div>
              {(
                [
                  ["industry", "行业", industryLeaders],
                  ["concept", "题材", conceptLeaders],
                ] as const
              ).map(
                ([kind, label, leaders]) =>
                  leaders.length > 0 && (
                    <div className="intraday-change-theme-row" key={kind}>
                      <span>{label}</span>
                      {leaders.map(([name, items]) => (
                        <button
                          type="button"
                          key={name}
                          className="intraday-feedback-leader"
                          aria-pressed={
                            theme?.kind === kind && theme.name === name
                          }
                          onClick={() =>
                            setTheme(
                              theme?.kind === kind && theme.name === name
                                ? null
                                : { kind, name },
                            )
                          }
                        >
                          <Filter size={11} aria-hidden="true" />
                          <span>{name}</span>
                          <b>{integer(items.length)} 只</b>
                        </button>
                      ))}
                    </div>
                  ),
              )}
            </div>
          )}
        {group?.items?.length > 0 ? (
          <Ranking
            key={`${snapshot.id}:${activeKind.key}:${theme?.kind || ""}:${theme?.name || ""}`}
            rows={selectedRows}
            stockMode={choice.mode}
            title={choice.label}
            subtitle={`${choice.label} · ${period}${theme ? ` · ${theme.name}` : ""}`}
            historyScope={
              snapshot.date && snapshot.slot
                ? { date: snapshot.date, slot: snapshot.slot }
                : undefined
            }
            onStock={onStock}
          />
        ) : (
          <div className="intraday-change-empty">
            {group?.state === "observed"
              ? "本段没有该类变化"
              : "暂无可确认名单"}
          </div>
        )}
        {group?.omitted_count > 0 && (
          <p className="intraday-change-note">
            当前列出前 {integer(group.items.length)} 只，另有{" "}
            {integer(group.omitted_count)} 只已确认记录未展开。
          </p>
        )}
      </div>
      <p className="intraday-change-footnote">
        新入封板池不等于首次涨停；开板、回封需两档状态确认。各类可重叠，不相加为总数。
      </p>
    </Panel>
  );
}

export function IntradaySnapshotFeedback({
  snapshot,
  onStock,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
}) {
  return (
    <>
      {snapshot.market_pulse && (
        <MarketPulse
          pulse={snapshot.market_pulse}
          interval={snapshot.summary?.interval_amount}
        />
      )}
      {snapshot.board_changes && (
        <IntradayBoardChanges
          key={`changes:${snapshot.id}`}
          snapshot={snapshot}
          onStock={onStock}
        />
      )}
      {(snapshot.ladder || snapshot.relay_feedback) && (
        <div className="intraday-feedback-grid" key={snapshot.id}>
          {snapshot.ladder && (
            <LadderCard ladder={snapshot.ladder} onStock={onStock} />
          )}
          {snapshot.ladder?.broken_board && (
            <BrokenBoardDetails
              pool={snapshot.ladder.broken_board}
              onStock={onStock}
            />
          )}
          {snapshot.relay_feedback?.prior_session && (
            <PriorSessionCard
              prior={snapshot.relay_feedback.prior_session}
              priorBroken={snapshot.relay_feedback.prior_broken_session}
              onStock={onStock}
            />
          )}
          {!snapshot.board_changes && snapshot.relay_feedback?.same_day && (
            <SameDayCard
              comparison={snapshot.relay_feedback.same_day}
              onStock={onStock}
            />
          )}
        </div>
      )}
    </>
  );
}

function HistoricalPriceDates({ coverage }: { coverage?: Row }) {
  if (!coverage?.start_suspension && !coverage?.end_suspension) return null;
  const description = [
    coverage.start_suspension &&
      `基准日停牌，取 ${coverage.start_price_date} 收盘`,
    coverage.end_suspension && `截止日停牌，取 ${coverage.end_price_date} 收盘`,
  ]
    .filter(Boolean)
    .join("；");
  return (
    <small className="intraday-historical-price-date" title={description}>
      停牌前收盘
    </small>
  );
}

function Ranking({
  rows,
  sector = false,
  title,
  subtitle,
  showRankChange = false,
  stockMode,
  emptyTitle,
  onStock,
  historyScope,
  memberScope,
}: {
  rows: Row[];
  sector?: boolean;
  title?: string;
  subtitle?: string;
  showRankChange?: boolean;
  stockMode?: StockMode;
  emptyTitle?: string;
  onStock?: (id: string) => void;
  historyScope?: StockHistoryScope;
  memberScope?: MemberScope;
}) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<MarketSort>({
    key: "change_pct",
    order: "desc",
  });
  const [boardFilter, setBoardFilter] = useState<NoonBoardFilterValue>("all");
  const memberLimit = 20;
  const memberQuery = memberScope
    ? new URLSearchParams({
        date: memberScope.date,
        slot: memberScope.slot,
        snapshot_id: memberScope.snapshotId,
        topic_code: memberScope.topicCode,
        board: boardFilter,
        page: String(page + 1),
        limit: String(memberLimit),
        sort: sort.key,
        direction: sort.order,
      })
    : null;
  const members = useApi<Row>(
    memberQuery ? `/market/intraday/members?${memberQuery}` : null,
  );
  const loaded = members.data;
  const memberPage =
    memberScope &&
    loaded?.date === memberScope.date &&
    loaded.slot === memberScope.slot &&
    loaded.snapshot_id === memberScope.snapshotId &&
    loaded.topic_code === memberScope.topicCode &&
    loaded.board === boardFilter &&
    loaded.sort === sort.key &&
    loaded.direction === sort.order &&
    loaded.page === page + 1
      ? loaded
      : null;
  const useFrozenSample = memberPage?.state === "unknown";
  const retainedByCode = new Map(rows.map((row) => [row.instrument_id, row]));
  const displayRows: Row[] = memberScope
    ? useFrozenSample
      ? rows
      : ((memberPage?.items || []) as Row[]).map((row) => {
          const retained = retainedByCode.get(row.instrument_id);
          return retained?.themes?.length && !row.themes?.length
            ? { ...row, themes: retained.themes }
            : row;
        })
    : rows;
  const filteredRows =
    !sector && historyScope && (!memberScope || useFrozenSample)
      ? filterRows(displayRows, boardFilter)
      : displayRows;
  const pageSize = expanded ? 30 : 10;
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filteredRows.length / pageSize) - 1),
  );
  const serverSorted = memberScope && !useFrozenSample;
  const usesHistory = historySortFields.includes(sort.key);
  // Snapshot-only lists retain at most 30 rows. Load the whole retained list
  // only when the selected sort actually needs those historical values.
  const sortHistory = useApi<Row>(
    historyScope && !serverSorted && usesHistory && filteredRows.length
      ? stockHistoryQuery(historyScope, filteredRows)
      : null,
  );
  const sortHistoryRows =
    usesHistory &&
    sortHistory.data?.date === historyScope?.date &&
    sortHistory.data?.slot === historyScope?.slot
      ? sortHistory.data?.items
      : undefined;
  const orderedRows = serverSorted
    ? filteredRows
    : sortMarketRows(
        filteredRows.map((row, index) => {
          const values = sortHistoryRows?.[row.instrument_id];
          return {
            ...row,
            rank: row.rank ?? index + 1,
            ...(values && {
              [sort.key]:
                values[sort.key] ??
                (sort.key === "limit_up_1y"
                  ? values.limit_up_1y_observed || null
                  : null),
            }),
          };
        }),
        sort,
      );
  const visibleRows = memberScope
    ? orderedRows
    : orderedRows.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const history = useApi<Row>(
    historyScope && !sector && !usesHistory && visibleRows.length
      ? stockHistoryQuery(historyScope, visibleRows)
      : null,
  );
  const historyData = usesHistory
    ? serverSorted
      ? memberPage?.history
      : sortHistory.data
    : history.data;
  const historyRows =
    historyData?.date === historyScope?.date &&
    historyData?.slot === historyScope?.slot
      ? historyData?.items
      : undefined;
  const historyRequest = usesHistory
    ? serverSorted
      ? null
      : sortHistory
    : history;
  const broken = stockMode === "broken_stocks";
  const compact = stockMode != null;
  const hasOpenTimes =
    broken && displayRows.some((row) => row.open_times != null);
  const hasOpenPrice = displayRows.some((row) => row.open_change_pct != null);
  const hasAmount = displayRows.some((row) => row.amount != null);
  const hasComparison = displayRows.some(
    (row) => row.change_pp != null || row.amount_delta != null,
  );
  const sortHeader = (field: string, caption: string) => (
    <th
      scope="col"
      aria-sort={
        sort.key === field
          ? sort.order === "asc"
            ? "ascending"
            : "descending"
          : undefined
      }
    >
      <MarketSortHeader
        field={field}
        caption={caption}
        sort={sort}
        onSort={() => {
          setSort({
            key: field,
            order: sort.key === field && sort.order === "desc" ? "asc" : "desc",
          });
          setPage(0);
        }}
      />
    </th>
  );
  return (
    <Panel
      title={compact ? undefined : title || (sector ? "强势板块" : "强势个股")}
      subtitle={
        subtitle ||
        (sector
          ? "按涨跌幅排序 · 仅覆盖本轮观察板块"
          : "按涨跌幅排序 · 基于本轮已核验的个股报价")
      }
      className={`intraday-ranking${compact ? " intraday-stock-ranking" : ""}`}
    >
      {!sector && historyScope && (memberScope || rows.length > 0) && (
        <div
          className="intraday-stock-toolbar intraday-filters"
          role="group"
          aria-label="筛选上市板块"
        >
          {NOON_BOARD_OPTIONS.map(({ value, label }) => (
            <button
              type="button"
              className="btn small"
              key={value}
              aria-pressed={boardFilter === value}
              onClick={() => {
                setBoardFilter(value);
                setPage(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {memberPage && (
        <p className="intraday-stock-note">
          {useFrozenSample
            ? displayRows.length
              ? `该时点仅留存 ${integer(displayRows.length)} 只个股样本。`
              : "该时点暂无个股明细。"
            : [
                memberPage.membership_as_of &&
                  `名单留存日 ${memberPage.membership_as_of}`,
                memberPage.total != null &&
                  `留存名单 ${integer(memberPage.total)} 只`,
                memberPage.quote_coverage &&
                  `匹配本时点报价 ${integer(memberPage.quote_coverage.valid)} 只`,
                boardFilter !== "all" &&
                  memberPage.matched != null &&
                  `当前筛选 ${integer(memberPage.matched)} 只`,
              ]
                .filter(Boolean)
                .join(" · ")}
          {memberPage.unavailable_reason && ` ${memberPage.unavailable_reason}`}
        </p>
      )}
      {memberScope && members.error ? (
        <ErrorBox message={members.error} retry={members.reload} />
      ) : memberScope && !memberPage ? (
        <Loading />
      ) : filteredRows.length ? (
        <>
          {compact && !memberScope && (
            <p className="intraday-stock-note">
              {subtitle} · 留存样本 {integer(filteredRows.length)} 只
            </p>
          )}
          <div className="intraday-table-scroll">
            <table>
              <thead>
                <tr>
                  {sortHeader("rank", "排名")}
                  {sortHeader("name", sector ? "板块" : "股票")}
                  {sortHeader(
                    "change_pct",
                    sector
                      ? "指数涨跌幅"
                      : historyScope?.slot === "auction"
                        ? "竞价涨跌幅"
                        : "今日涨跌幅",
                  )}
                  {!sector && historyScope && (
                    <>
                      {sortHeader("change_30d_pct", "近30交易日涨幅")}
                      {sortHeader("return_ytd_pct", "年内涨幅")}
                      {sortHeader("last_limit_up_date", "最近一次涨停日期")}
                      {sortHeader("limit_up_1y", "近一年涨停次数")}
                    </>
                  )}
                  {hasOpenPrice && sortHeader("open_change_pct", "较开盘")}
                  {(hasAmount || serverSorted) &&
                    sortHeader("amount", "累计成交额")}
                  {hasOpenTimes && sortHeader("open_times", "开板次数")}
                  {hasComparison && sortHeader("change_pp", "较上次涨幅")}
                  {hasComparison &&
                    sortHeader("amount_delta", "区间新增成交额")}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, index) => {
                  const stockHistory = historyRows?.[row.instrument_id];
                  const previousHeight =
                    row.previous_board_count ??
                    stockHistory?.previous_board_count;
                  const sealed =
                    row.limit_up_state != null
                      ? row.limit_up_state === "sealed"
                      : stockMode === "sealed_stocks";
                  const brokenBoard =
                    row.limit_up_state != null
                      ? row.limit_up_state === "broken"
                      : broken;
                  return (
                    <tr key={row.instrument_id}>
                      <td>
                        {row.rank ??
                          (memberScope
                            ? page * memberLimit
                            : currentPage * pageSize) +
                            index +
                            1}
                        {showRankChange &&
                          row.previous_rank != null &&
                          row.rank != null &&
                          row.previous_rank !== row.rank && (
                            <small
                              title={`上次排名第 ${row.previous_rank}`}
                              className={tone(row.previous_rank - row.rank)}
                            >
                              {row.previous_rank > row.rank ? "↑" : "↓"}
                              {Math.abs(row.previous_rank - row.rank)}
                            </small>
                          )}
                      </td>
                      <th scope="row">
                        {!sector && onStock ? (
                          <button
                            className="stock-link"
                            onClick={() => onStock(row.instrument_id)}
                          >
                            {row.name || row.instrument_id}
                          </button>
                        ) : (
                          row.name || row.instrument_id
                        )}
                        <div className="intraday-stock-meta">
                          {sector ? (
                            row.instrument_id
                          ) : (
                            <CopyStockCode instrumentId={row.instrument_id} />
                          )}
                          {!sector && (
                            <span
                              className="intraday-stock-state"
                              data-kind="listing"
                            >
                              {row.board_label || boardLabel(boardOf(row))}
                            </span>
                          )}
                          {row.kind === "industry"
                            ? " · 行业"
                            : row.kind === "concept"
                              ? " · 概念"
                              : ""}
                          {!sector && (
                            <IntradayStockStateBadge
                              row={{
                                ...row,
                                limit_up_state: sealed
                                  ? "sealed"
                                  : brokenBoard
                                    ? "broken"
                                    : row.limit_up_state,
                              }}
                            />
                          )}
                          {!sealed &&
                            !brokenBoard &&
                            row.limit_up_reference_price != null && (
                              <span
                                className="intraday-stock-state"
                                data-kind="reference"
                                title={`最新价等于常规涨停参考价 ${num(row.limit_up_reference_price)} 元，封板及板数待核验`}
                              >
                                涨停价参考
                              </span>
                            )}
                          {!sector && row.trading_status === "suspended" && (
                            <span
                              className="intraday-stock-state"
                              data-kind="suspended"
                              title={row.missing_reason}
                            >
                              停牌
                            </span>
                          )}
                          {!sector &&
                            row.trading_status !== "suspended" &&
                            row.quote_available === false && (
                              <span
                                className="intraday-stock-state"
                                data-kind="listing"
                                title={row.missing_reason}
                              >
                                报价待核验
                              </span>
                            )}
                          {!sector && previousHeight != null && (
                            <span className="intraday-stock-state">
                              前日 {integer(previousHeight)} 板
                            </span>
                          )}
                        </div>
                        <ThemeChips
                          row={row}
                          showMissing={compact && Array.isArray(row.themes)}
                        />
                        {row.signals?.length > 0 && (
                          <small>{row.signals.join(" · ")}</small>
                        )}
                        {row.limit_up_state === "unknown" && (
                          <small>封板状态待核验</small>
                        )}
                      </th>
                      <td className={tone(row.change_pct)}>
                        {percent(row.change_pct)}
                      </td>
                      {!sector && historyScope && (
                        <>
                          <td className={tone(stockHistory?.change_30d_pct)}>
                            {percent(stockHistory?.change_30d_pct)}
                            <HistoricalPriceDates
                              coverage={stockHistory?.change_30d_coverage}
                            />
                          </td>
                          <td className={tone(stockHistory?.return_ytd_pct)}>
                            {percent(stockHistory?.return_ytd_pct)}
                            <HistoricalPriceDates
                              coverage={stockHistory?.return_ytd_coverage}
                            />
                          </td>
                          <td>
                            {stockHistory?.last_limit_up_date || "—"}
                            {stockHistory?.last_limit_up_date &&
                              stockHistory.last_limit_up_complete === false && (
                                <small>最近确认</small>
                              )}
                          </td>
                          <td>
                            {stockHistory?.limit_up_1y != null
                              ? integer(stockHistory.limit_up_1y)
                              : stockHistory?.limit_up_1y_observed > 0
                                ? `≥${integer(stockHistory.limit_up_1y_observed)}`
                                : "—"}
                          </td>
                        </>
                      )}
                      {hasOpenPrice && (
                        <td className={tone(row.open_change_pct)}>
                          {percent(row.open_change_pct)}
                        </td>
                      )}
                      {(hasAmount || serverSorted) && (
                        <td>{amount(row.amount)}</td>
                      )}
                      {hasOpenTimes && (
                        <td>
                          {row.open_times == null
                            ? "—"
                            : integer(row.open_times)}
                        </td>
                      )}
                      {hasComparison && (
                        <td className={tone(row.change_pp)}>
                          {delta(row.change_pp, "百分点")}
                        </td>
                      )}
                      {hasComparison && <td>{amount(row.amount_delta)}</td>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {historyScope && !sector && (
            <p className="intraday-stock-note intraday-history-note">
              {historyRequest?.error
                ? "历史行情暂不可读"
                : historyRequest?.loading
                  ? "历史行情读取中"
                  : `历史指标截至 ${historyData?.as_of || "已发布可核验交易日"} · — 表示资料缺失，≥ 表示可核验下界`}
            </p>
          )}
          {!memberScope && filteredRows.length > 10 && (
            <div className="intraday-expand">
              <button
                className="btn small"
                onClick={() => {
                  setExpanded(!expanded);
                  setPage(0);
                }}
              >
                {expanded ? "收起" : `查看已留存的 ${filteredRows.length} 项`}
              </button>
              {expanded && filteredRows.length > pageSize && (
                <>
                  <button
                    className="btn small"
                    disabled={currentPage === 0}
                    onClick={() => setPage(currentPage - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="btn small"
                    disabled={
                      (currentPage + 1) * pageSize >= filteredRows.length
                    }
                    onClick={() => setPage(currentPage + 1)}
                  >
                    下一页
                  </button>
                </>
              )}
            </div>
          )}
          {memberPage && memberPage.pages > 1 && (
            <div className="intraday-expand">
              <button
                className="btn small"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </button>
              <span>
                第 {page + 1} / {memberPage.pages} 页
              </span>
              <button
                className="btn small"
                disabled={page + 1 >= memberPage.pages}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      ) : (
        <Empty
          title={
            (rows.length || (memberScope && boardFilter !== "all")
              ? "该上市板块暂无留存个股"
              : emptyTitle) ||
            (sector ? "本轮没有可核验的板块排名" : "本轮没有可核验的个股排名")
          }
          description={memberScope ? "" : "仅展示该时点有记录的数据。"}
        />
      )}
    </Panel>
  );
}

type BoardMode =
  | "strong_sectors"
  | "limit_up_sectors"
  | "index_only_sectors"
  | "wide_limit_up_sectors";
type BoardKind = "all" | "industry" | "concept";
type DirectionMode = BoardMode | "overview" | "discovery";
type StockMode = "members" | "candidates" | "sealed_stocks" | "broken_stocks";

const boardModes: [BoardMode, string][] = [
  ["strong_sectors", "板块强度"],
  ["limit_up_sectors", "封板家数"],
  ["wide_limit_up_sectors", "宽口径概念"],
  ["index_only_sectors", "仅指数线索"],
];

const anchorSlots = {
  auction: { key: "auction", label: "早盘竞价", start: "09:25" },
  noon: { key: "noon", label: "午评", start: "11:30" },
  close: { key: "close", label: "收盘复盘", start: "15:00" },
};

const currentCaptureSlots = [
  { key: "1000", label: "开盘首轮", start: "10:00" },
  { key: "1030", label: "早盘一小时", start: "10:30" },
  { key: "1100", label: "上午观察", start: "11:00" },
  { key: "1330", label: "午后首轮", start: "13:30" },
  { key: "1400", label: "午后观察", start: "14:00" },
  { key: "1430", label: "尾盘观察", start: "14:30" },
];

export function timelineSlots(view: Row) {
  const source = new Map<string, Row>(
    ((view.slots || []) as Row[]).map((slot) => [slot.key, slot]),
  );
  const noon =
    typeof view.noon_scheduled_at === "string" &&
    /^\d{2}:\d{2}$/.test(view.noon_scheduled_at)
      ? { ...anchorSlots.noon, start: view.noon_scheduled_at }
      : anchorSlots.noon;
  const slots: Row[] = [
    anchorSlots.auction,
    noon,
    anchorSlots.close,
    ...currentCaptureSlots.map((slot) => source.get(slot.key) || slot),
  ].sort((a, b) => a.start.localeCompare(b.start));
  for (const row of (view.snapshots || []) as Row[]) {
    if (!slots.some((slot) => slot.key === row.slot)) {
      slots.push({
        key: row.slot,
        label: row.label || row.slot_label || row.slot,
        start:
          source.get(row.slot)?.start ||
          clock(row.collected_end_at).slice(0, 5),
        legacy: true,
      });
    }
  }
  return slots;
}

const DISCOVERY_SIGNALS: Record<string, string> = {
  new: "由弱转强",
  accelerating: "涨幅扩大",
  fading: "走弱回落",
  leading: "当前领涨",
};
const discoveryFilters = [
  ["all", "全部变化"],
  ...Object.entries(DISCOVERY_SIGNALS),
];

function discoveryDirections(snapshot: Row, date: string, slot: string): Row[] {
  const discovery = snapshot.discovery;
  return discovery?.date === date && discovery.slot === slot
    ? discovery.directions || []
    : [];
}

function stageBoards(snapshot?: Row): Row[] {
  if (!snapshot) return [];
  if (!snapshot.strength_coverage) return snapshot.sectors || [];
  return Array.from(
    new Map<string, Row>(
      boardModes.flatMap(([key]) =>
        ((snapshot[key] || []) as Row[]).map((row): [string, Row] => [
          row.instrument_id,
          row,
        ]),
      ),
    ).values(),
  );
}

/** Rotation follows every retained quote, independently of the short ranking. */
export function rotationStageBoards(snapshot?: Row): Row[] {
  return Array.isArray(snapshot?.rotation_sectors)
    ? snapshot.rotation_sectors
    : stageBoards(snapshot);
}

const byMove = { key: "change_pct", order: "desc" } as const;
const bySealed = { key: "limit_up_count", order: "desc" } as const;

export function rotationCellChange({
  current,
  previous,
  snapshot,
  previousSnapshot,
  slot,
  previousSlot,
}: {
  current?: Row;
  previous?: Row;
  snapshot?: Row;
  previousSnapshot?: Row;
  slot: string;
  previousSlot: string;
}): { changePP: number | null; sealedDelta: number | null; reason?: string } {
  const unavailable = (reason: string) => ({
    changePP: null,
    sealedDelta: null,
    reason,
  });
  if (!previousSnapshot) return unavailable("上一档缺档");
  if (slot === "auction" || previousSlot === "auction")
    return unavailable("竞价与指数口径不同");
  if (!previous) return unavailable("上一档未留存该方向");
  if (!current || !snapshot) return unavailable("当前档未留存");
  const date = snapshot.date ?? snapshot.trade_date;
  if (!date || date !== (previousSnapshot.date ?? previousSnapshot.trade_date))
    return unavailable("交易日不同或待核验");
  if (
    !current.instrument_id ||
    current.instrument_id !== previous.instrument_id ||
    !["industry", "concept"].includes(current.kind) ||
    current.kind !== previous.kind ||
    !current.quote_source ||
    current.quote_source !== previous.quote_source ||
    current.quote_basis !== "provider_index" ||
    previous.quote_basis !== "provider_index" ||
    !current.price_basis ||
    current.price_basis !== previous.price_basis ||
    !current.currency ||
    current.currency !== previous.currency ||
    !Number.isFinite(current.prev_close) ||
    current.prev_close <= 0 ||
    current.prev_close !== previous.prev_close ||
    !Number.isFinite(current.latest_price) ||
    current.latest_price <= 0 ||
    !Number.isFinite(previous.latest_price) ||
    previous.latest_price <= 0
  )
    return unavailable("指数口径待核验");
  if (
    !Number.isFinite(current.change_pct) ||
    !Number.isFinite(previous.change_pct)
  )
    return unavailable("涨幅待核验");
  if (
    Math.abs(
      (current.latest_price / current.prev_close - 1) * 100 -
        current.change_pct,
    ) > 0.001 ||
    Math.abs(
      (previous.latest_price / previous.prev_close - 1) * 100 -
        previous.change_pct,
    ) > 0.001
  )
    return unavailable("指数价格与涨幅不一致");
  const calculated = current.change_pct - previous.change_pct;
  const stored =
    snapshot.summary?.comparison_slot === previousSlot &&
    Number.isFinite(current.change_pp) &&
    Math.abs(current.change_pp - calculated) <= 0.0001;
  const changePP = Number((stored ? current.change_pp : calculated).toFixed(4));
  const membership = current.member_reference_sha256;
  const countComparable =
    typeof membership === "string" &&
    /^[a-f0-9]{64}$/.test(membership) &&
    membership === previous.member_reference_sha256 &&
    current.member_list_complete === true &&
    previous.member_list_complete === true &&
    current.member_count > 0 &&
    current.member_count === previous.member_count &&
    current.limit_up_count_state === "observed" &&
    previous.limit_up_count_state === "observed" &&
    Number.isInteger(current.limit_up_count) &&
    current.limit_up_count >= 0 &&
    current.limit_up_count <= current.member_count &&
    Number.isInteger(previous.limit_up_count) &&
    previous.limit_up_count >= 0 &&
    previous.limit_up_count <= previous.member_count;
  return {
    changePP,
    sealedDelta: countComparable
      ? current.limit_up_count - previous.limit_up_count
      : null,
  };
}

export function Rotation({
  stages,
  samples,
  activeStage,
  onSelect,
}: {
  stages: Row[];
  samples: Map<string, Row>;
  activeStage: string;
  onSelect: (slot: string, board: string) => void;
}) {
  const [metric, setMetric] = useState<"sealed" | "change">("sealed");
  const [sort, setSort] = useState<MarketSort | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [showChanges, setShowChanges] = useState<boolean | null>(null);
  const limit = stages.findIndex((slot) => slot.key === activeStage);
  const columns = stages.slice(0, limit + 1).filter((slot) => !slot.legacy);
  const bySlot = new Map(
    columns.map((slot) => [
      slot.key,
      new Map(
        rotationStageBoards(samples.get(slot.key)).map((row) => [
          row.instrument_id,
          row,
        ]),
      ),
    ]),
  );
  const available = [...columns]
    .reverse()
    .find((slot) => bySlot.get(slot.key)?.size);
  if (columns.length < 2 || !available) return null;
  const changes = columns.map(
    (slot, index) =>
      new Map(
        index === 0
          ? []
          : [...(bySlot.get(slot.key) || [])].map(([id, current]) => [
              id,
              rotationCellChange({
                current,
                previous: bySlot.get(columns[index - 1].key)?.get(id),
                snapshot: samples.get(slot.key),
                previousSnapshot: samples.get(columns[index - 1].key),
                slot: slot.key,
                previousSlot: columns[index - 1].key,
              }),
            ]),
      ),
  );
  const activeSort = sort || { key: available.key, order: "desc" };
  const byName = activeSort.key === "name";
  const rankedRows = [
    ...new Map<string, Row>(
      [...bySlot.values()].flatMap((rows) => [...rows.entries()]),
    ).values(),
  ].map((row) => {
    const item = bySlot.get(activeSort.key)?.get(row.instrument_id);
    return {
      ...row,
      name: row.name || row.instrument_id,
      change_pct: item?.change_pct,
      limit_up_count: item?.limit_up_count,
    };
  });
  const sortBySealed =
    metric === "sealed" &&
    activeSort.key !== "auction" &&
    rankedRows.some((row) => row.limit_up_count != null);
  const byChange = sortMarketRows(rankedRows, {
    ...byMove,
    order: sortBySealed ? "desc" : activeSort.order,
  });
  const ordered = byName
    ? sortMarketRows(rankedRows, activeSort)
    : sortBySealed
      ? sortMarketRows(byChange, { ...bySealed, order: activeSort.order })
      : byChange;
  const featured = expanded ? ordered : ordered.slice(0, 12);
  const hasComparable = changes.some((rows) =>
    featured.some((board) => rows.get(board.instrument_id)?.changePP != null),
  );
  const changesVisible = showChanges ?? hasComparable;
  const changeSort = (key: string) =>
    setSort({
      key,
      order:
        activeSort.key === key && activeSort.order === "desc" ? "asc" : "desc",
    });
  const sortState = (key: string) =>
    activeSort.key === key
      ? activeSort.order === "desc"
        ? ("descending" as const)
        : ("ascending" as const)
      : undefined;
  return (
    <Panel
      className="intraday-rotation intraday-ranking"
      title="题材轮动"
      subtitle="点击时点表头排序，点击单元格查看该时点的题材个股。"
    >
      <div className="intraday-rotation-toolbar">
        <div
          className="intraday-filters"
          role="group"
          aria-label="题材轮动排序指标"
        >
          <button
            type="button"
            className="btn small"
            aria-pressed={metric === "sealed"}
            onClick={() => setMetric("sealed")}
          >
            涨停家数优先
          </button>
          <button
            type="button"
            className="btn small"
            aria-pressed={metric === "change"}
            onClick={() => setMetric("change")}
          >
            按涨幅
          </button>
        </div>
        <small>
          {byName
            ? "按题材名称排序"
            : activeSort.key === "auction"
              ? "竞价按候选均幅排序"
              : sortBySealed
                ? "涨停家数相同按涨幅排，缺档排在末尾"
                : "按所选时点涨幅排序，缺档排在末尾"}
        </small>
        <label className="intraday-rotation-change-toggle">
          <input
            type="checkbox"
            checked={changesVisible}
            onChange={(event) => setShowChanges(event.target.checked)}
          />
          较上一档变化
        </label>
        {changesVisible && (
          <small className="intraday-rotation-change-note">
            涨幅差按百分点显示；封板差需同一成员范围，缺档不跨档比较。
          </small>
        )}
      </div>
      <div
        className="intraday-table-scroll"
        tabIndex={0}
        aria-label="横向滚动查看各时点"
      >
        <table>
          <thead>
            <tr>
              <th scope="col" aria-sort={sortState("name")}>
                <MarketSortHeader
                  field="name"
                  caption="行业 / 题材"
                  sort={activeSort}
                  onSort={changeSort}
                />
              </th>
              {columns.map((slot) => (
                <th scope="col" key={slot.key} aria-sort={sortState(slot.key)}>
                  {bySlot.get(slot.key)?.size ? (
                    <MarketSortHeader
                      field={slot.key}
                      caption={slot.start}
                      sort={activeSort}
                      onSort={changeSort}
                    />
                  ) : (
                    <time>{slot.start}</time>
                  )}
                  <small>{slot.label}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {featured.map((board) => (
              <tr key={board.instrument_id}>
                <th scope="row">
                  {board.name || board.instrument_id}
                  <small>{board.kind === "industry" ? "行业" : "概念"}</small>
                </th>
                {columns.map((slot, index) => {
                  const item = bySlot.get(slot.key)?.get(board.instrument_id);
                  const snapshot = samples.get(slot.key);
                  const canOpen =
                    item &&
                    (snapshot?.strength_coverage ||
                      Array.isArray(snapshot?.rotation_sectors) ||
                      Array.isArray(item.stocks));
                  const comparison = changes[index].get(board.instrument_id);
                  const changeLine = changesVisible && comparison && (
                    <small
                      className={`intraday-rotation-change ${tone(comparison.changePP)}`}
                      title={
                        comparison.reason ||
                        `较 ${columns[index - 1].start} 的同来源板块指数涨幅变化`
                      }
                    >
                      {comparison.changePP == null
                        ? comparison.reason
                        : `较${columns[index - 1].start} ${delta(comparison.changePP, "百分点")}`}
                      {comparison.sealedDelta != null && (
                        <span className={tone(comparison.sealedDelta)}>
                          {" "}
                          · 封板{comparison.sealedDelta > 0 ? "+" : ""}
                          {integer(comparison.sealedDelta)}
                        </span>
                      )}
                    </small>
                  );
                  return (
                    <td key={slot.key} className={tone(item?.change_pct)}>
                      {canOpen ? (
                        <button
                          type="button"
                          className="intraday-rotation-cell"
                          onClick={() =>
                            onSelect(slot.key, board.instrument_id)
                          }
                          aria-label={`${slot.start} ${board.name || board.instrument_id} 查看个股`}
                        >
                          <strong>{percent(item.change_pct)}</strong>
                          <small>
                            {slot.key === "auction"
                              ? "竞价候选均值"
                              : item.limit_up_count != null
                                ? `封板 ${evidenceCount(item.limit_up_count, item.limit_up_count_state)} 只`
                                : "查看个股"}
                          </small>
                          {changeLine}
                        </button>
                      ) : item ? (
                        <>
                          {percent(item.change_pct)}
                          {changeLine}
                        </>
                      ) : (
                        <small>{snapshot ? "—" : "缺档"}</small>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {ordered.length > 12 && (
        <div className="intraday-expand">
          <button
            type="button"
            className="btn small"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded
              ? "收起到前 12 个方向"
              : `展开全部 ${integer(ordered.length)} 个方向`}
          </button>
        </div>
      )}
    </Panel>
  );
}

function Retrospective({ view }: { view: Row }) {
  const events = (view.events || []) as Row[];
  if (!events.length) return null;
  const max = Math.max(1, ...events.map((event) => event.count));
  return (
    <Panel
      className="intraday-retrospective"
      title={view.title || "盘后回溯 · 首次封板"}
      subtitle={view.scope}
    >
      <ul className="intraday-event-bars">
        {events.map((event) => (
          <li key={event.label}>
            <span>{event.label}</span>
            <span className="intraday-event-track" aria-hidden="true">
              <i style={{ width: `${(100 * event.count) / max}%` }} />
            </span>
            <strong>{integer(event.count)} 只</strong>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function AnchorDetails({
  snapshot,
  onStock,
  requestedBoard,
  historyScope,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
  requestedBoard?: string;
  historyScope: StockHistoryScope;
}) {
  const boards = stageBoards(snapshot);
  const [selected, setSelected] = useState(
    requestedBoard || boards[0]?.instrument_id,
  );
  const board =
    boards.find((row) => row.instrument_id === selected) ||
    rotationStageBoards(snapshot).find((row) => row.instrument_id === selected);
  const stocks = ((board?.stocks || []) as Row[]).map((row) => ({
    ...row,
    continue_day_cnt: row.continue_day_cnt ?? row.board_count,
    limit_up_state: ["limit_up", "sealed"].includes(row.state)
      ? "sealed"
      : ["limit_break", "broken"].includes(row.state)
        ? "broken"
        : row.limit_up_state,
  }));
  return (
    <>
      {snapshot.scope && (
        <p className="intraday-stage-scope">{snapshot.scope}</p>
      )}
      <IntradaySnapshotFeedback
        snapshot={{
          ...snapshot,
          market_pulse: snapshot.market || snapshot.market_pulse,
          ladder: snapshot.ladder?.state ? snapshot.ladder : undefined,
        }}
        onStock={onStock}
      />
      {snapshot.slot === "auction" && (
        <AuctionHeightReference date={historyScope.date} onStock={onStock} />
      )}
      {!!boards.length && (
        <div className="intraday-direction-workspace">
          <Panel title="方向观察" className="intraday-direction-list">
            <div className="intraday-anchor-boards">
              {boards.map((row) => (
                <button
                  key={row.instrument_id}
                  className="intraday-anchor-board"
                  type="button"
                  aria-pressed={row.instrument_id === selected}
                  onClick={() => setSelected(row.instrument_id)}
                >
                  <span>
                    {row.name || row.instrument_id}
                    <small>{row.kind === "industry" ? "行业" : "概念"}</small>
                  </span>
                  <strong className={tone(row.change_pct)}>
                    {percent(row.change_pct)}
                  </strong>
                </button>
              ))}
            </div>
          </Panel>
          {board && (
            <Ranking
              key={board.instrument_id}
              rows={stocks}
              title={`${board.name || board.instrument_id} · 个股`}
              subtitle={
                snapshot.slot === "auction"
                  ? "只统计该题材中纳入竞价观察的候选"
                  : "按所选时点的成员名单和报价展示"
              }
              onStock={onStock}
              historyScope={historyScope}
              memberScope={{
                ...historyScope,
                snapshotId: snapshot.id,
                topicCode: board.instrument_id,
              }}
              emptyTitle="该时点暂无可核验个股"
            />
          )}
        </div>
      )}
      {snapshot.slot === "close" && snapshot.retrospective && (
        <Retrospective view={snapshot.retrospective} />
      )}
    </>
  );
}

const directionColumns = [
  ["rank", "排名"],
  ["name", "行业 / 题材"],
  ["change_pct", "板块涨跌幅"],
  ["up_ratio", "上涨占比"],
  ["limit_up_count", "涨停家数"],
  ["broken_count", "炸板"],
];

export function StrengthView({
  snapshot,
  onStock,
  historyScope,
  requestedBoard,
}: {
  snapshot: Row;
  onStock?: (id: string) => void;
  historyScope: StockHistoryScope;
  requestedBoard?: string;
}) {
  const discoveries = discoveryDirections(
    snapshot,
    historyScope.date,
    historyScope.slot,
  );
  const discoveredById = new Map(
    discoveries.map((row) => [row.instrument_id, row]),
  );
  const fullBoardsById = new Map(
    [...stageBoards(snapshot), ...rotationStageBoards(snapshot)].map((row) => [
      row.instrument_id,
      row,
    ]),
  );
  const overview = Array.from(
    new Map<string, Row>(
      [
        ...discoveries,
        ...(snapshot.strength_coverage ? [] : fullBoardsById.values()),
        ...(snapshot.limit_up_sectors || []),
        ...(snapshot.strong_sectors || []),
      ].map((row) => [
        row.instrument_id,
        fullBoardsById.get(row.instrument_id) || row,
      ]),
    ).values(),
  );
  const modes: [DirectionMode, string][] = [["overview", "综合"]];
  if (snapshot.discovery) modes.push(["discovery", "盘中发现"]);
  modes.push(...boardModes.filter(([key]) => snapshot[key]?.length));
  const requestedMode = overview.some(
    (row) => row.instrument_id === requestedBoard,
  )
    ? "overview"
    : boardModes.find(([key]) =>
        (snapshot[key] || []).some(
          (row: Row) => row.instrument_id === requestedBoard,
        ),
      )?.[0];
  const [mode, setMode] = useState<DirectionMode>(requestedMode || "overview");
  const [kind, setKind] = useState<BoardKind>("all");
  const [discoveryFilter, setDiscoveryFilter] = useState("all");
  const [selectedBoard, setSelectedBoard] = useState<string | null | undefined>(
    requestedBoard,
  );
  const [stockMode, setStockMode] = useState<StockMode>("members");
  const [sort, setSort] = useState<MarketSort>({
    key: "limit_up_count",
    order: "desc",
  });
  const source: Row[] =
    mode === "overview"
      ? overview
      : mode === "discovery"
        ? discoveries.map((row) => fullBoardsById.get(row.instrument_id) || row)
        : snapshot[mode] || [];
  const filtered = source.filter(
    (row) =>
      (kind === "all" || row.kind === kind) &&
      (mode !== "discovery" ||
        discoveryFilter === "all" ||
        discoveredById.get(row.instrument_id)?.signal === discoveryFilter),
  );
  const ranked = sortMarketRows(sortMarketRows(filtered, byMove), bySealed).map(
    (row, i) => ({ ...row, rank: i + 1, name: row.name || row.instrument_id }),
  );
  const boards = sortMarketRows(ranked, sort);
  const board =
    selectedBoard === undefined
      ? boards[0]
      : boards.find((row) => row.instrument_id === selectedBoard) ||
        fullBoardsById.get(selectedBoard || "");
  const signal = board && discoveredById.get(board.instrument_id);
  const members = board && snapshot.board_stocks?.[board.instrument_id];
  const selectDirection = (id: string | null | undefined) => {
    setSelectedBoard(id);
    setStockMode(id === null ? "candidates" : "members");
  };
  const changeSort = (key: string) => {
    if (selectedBoard === undefined && board)
      setSelectedBoard(board.instrument_id);
    setSort((current) => ({
      key,
      order: current.key === key && current.order === "desc" ? "asc" : "desc",
    }));
  };
  const displayedMode =
    stockMode === "members" && !board ? "candidates" : stockMode;
  const poolComplete = snapshot.strength_coverage?.pool_complete === true;
  const breakout = snapshot.ladder?.broken_board;
  const showBroken = breakout || source.some((row) => row.broken_count != null);
  const rowsFor = (value: StockMode): Row[] => {
    if (value === "members") {
      const items: Row[] = members?.items || board?.stocks || [];
      if (!items.length) return signal?.stocks || [];
      const themed = new Map<string, Row>(
        ((signal?.stocks || []) as Row[]).map((row) => [
          row.instrument_id,
          row,
        ]),
      );
      return items.map((row) => {
        const themes = themed.get(row.instrument_id)?.themes;
        return !row.themes?.length && themes?.length ? { ...row, themes } : row;
      });
    }
    if (value === "broken_stocks") {
      return board
        ? board.broken || []
        : snapshot.broken_stocks?.length
          ? snapshot.broken_stocks
          : breakout?.items || [];
    }
    return (
      (board || snapshot)[value] ||
      (!board && value === "candidates" ? snapshot.stocks : undefined) ||
      []
    );
  };
  const stockModes: [StockMode, string][] = [
    ...(board ? [["members", "成分个股"] as [StockMode, string]] : []),
    ["candidates", "走强"],
    ["sealed_stocks", "封板"],
    ...(showBroken ? [["broken_stocks", "炸板"] as [StockMode, string]] : []),
  ];
  const kinds: [BoardKind, string][] = [
    ["all", "全部"],
    ["industry", "行业"],
    ["concept", "概念"],
  ];
  return (
    <div className="intraday-direction-workspace">
      <Panel
        title="盘中方向"
        subtitle="默认按涨停家数排序，同家数按板块涨幅排列。点板块查看个股。"
        className="intraday-ranking intraday-direction-list"
      >
        <div className="intraday-board-toolbar">
          <div className="intraday-filters" role="group" aria-label="榜单口径">
            {modes.map(([value, label]) => (
              <button
                className="btn small"
                key={value}
                aria-pressed={value === mode}
                onClick={() => {
                  setMode(value);
                  if (value === "wide_limit_up_sectors") setKind("all");
                  selectDirection(undefined);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="intraday-filters" role="group" aria-label="板块类型">
            {kinds.map(([value, label]) => (
              <button
                className="btn small"
                key={value}
                aria-pressed={value === kind}
                disabled={
                  mode === "wide_limit_up_sectors" && value === "industry"
                }
                onClick={() => {
                  setKind(value);
                  selectDirection(undefined);
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {mode === "discovery" && (
          <>
            <div
              className="intraday-discovery-filters intraday-filters"
              role="group"
              aria-label="盘中发现变化"
            >
              {discoveryFilters.map(([key, label]) => (
                <button
                  type="button"
                  key={key}
                  className="btn small"
                  aria-pressed={discoveryFilter === key}
                  onClick={() => {
                    setDiscoveryFilter(key);
                    selectDirection(undefined);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="intraday-intro">
              {snapshot.discovery.reason ||
                "与同日上一可比时点对照。走弱回落指原本上涨的方向涨幅明显收窄或翻绿。"}
            </p>
          </>
        )}
        {mode === "wide_limit_up_sectors" && (
          <p className="intraday-intro">
            成员超过 200 只的概念单列，避免宽口径标签淹没更集中的方向。
          </p>
        )}
        {mode === "index_only_sectors" && (
          <p className="intraday-intro">
            这些板块的成员覆盖未达到强度榜门槛，只展示可核验的指数报价和样本。
          </p>
        )}
        {boards.length ? (
          <div className="intraday-table-scroll intraday-board-scroll">
            <table>
              <thead>
                <tr>
                  {directionColumns
                    .filter(([key]) => key !== "broken_count" || showBroken)
                    .map(([key, label]) => (
                      <th
                        key={key}
                        scope="col"
                        aria-sort={
                          sort.key === key
                            ? sort.order === "desc"
                              ? "descending"
                              : "ascending"
                            : undefined
                        }
                      >
                        <MarketSortHeader
                          field={key}
                          caption={label}
                          sort={sort}
                          onSort={changeSort}
                        />
                      </th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {boards.map((row) => (
                  <tr
                    key={row.instrument_id}
                    data-selected={row.instrument_id === board?.instrument_id}
                    onClick={() => selectDirection(row.instrument_id)}
                  >
                    <td>{row.rank}</td>
                    <th scope="row">
                      <button
                        className="stock-link"
                        aria-pressed={
                          row.instrument_id === board?.instrument_id
                        }
                      >
                        {row.name || row.instrument_id}
                        <ArrowUpRight size={13} aria-hidden="true" />
                      </button>
                      <small>
                        {row.kind === "industry"
                          ? "行业"
                          : row.kind === "concept"
                            ? "概念"
                            : "—"}
                        {discoveredById.has(row.instrument_id) && (
                          <span
                            className="intraday-direction-signal"
                            data-signal={
                              discoveredById.get(row.instrument_id)?.signal
                            }
                          >
                            {
                              DISCOVERY_SIGNALS[
                                discoveredById.get(row.instrument_id)?.signal
                              ]
                            }
                          </span>
                        )}
                      </small>
                    </th>
                    <td className={tone(row.change_pct)}>
                      {percent(row.change_pct)}
                    </td>
                    <td>
                      {row.up_ratio == null ? "—" : `${num(row.up_ratio)}%`}
                      {row.advancers != null && (
                        <small>上涨 {integer(row.advancers)} 只</small>
                      )}
                    </td>
                    <td>
                      {row.limit_up_count == null
                        ? "待核验"
                        : `${["partial", "lower_bound"].includes(row.limit_up_count_state) ? "≥" : ""}${integer(row.limit_up_count)} 只`}
                      {row.limit_up_ratio != null && (
                        <small>占成员 {num(row.limit_up_ratio)}%</small>
                      )}
                    </td>
                    {showBroken && (
                      <td>
                        {evidenceCount(
                          row.broken_count,
                          row.broken_count_state,
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={
              mode === "discovery" && discoveryFilter !== "all"
                ? `本时点暂无${DISCOVERY_SIGNALS[discoveryFilter]}方向`
                : "当前口径没有可核验板块"
            }
            description="可以切换筛选条件查看其他方向。"
          />
        )}
        <details className="intraday-scope">
          <summary>榜单口径</summary>
          <p>
            强度榜要求指数上涨、过半可比成员上涨、成员涨幅中位数为正，且至少 5
            只可比成员、覆盖达到 60%。行业和概念可能共享成员。
          </p>
        </details>
      </Panel>
      <section
        className="panel intraday-direction-detail"
        aria-label="方向内个股"
      >
        <div className="intraday-detail-heading">
          <div>
            <span className="intraday-eyebrow">
              {board ? "板块观察" : "市场观察"}
            </span>
            <h2>{board?.name || "盘中个股线索"}</h2>
          </div>
          {board && (
            <button className="btn small" onClick={() => selectDirection(null)}>
              全部方向
            </button>
          )}
        </div>
        {board && (
          <div className="intraday-detail-facts">
            <span>
              指数{" "}
              <strong className={tone(board.change_pct)}>
                {percent(board.change_pct)}
              </strong>
            </span>
            {Number.isFinite(board.open_change_pct) && (
              <span>
                较开盘{" "}
                <strong className={tone(board.open_change_pct)}>
                  {percent(board.open_change_pct)}
                </strong>
              </span>
            )}
            {Number.isFinite(board.median_change_pct) && (
              <span>
                成员涨幅中位数{" "}
                <strong className={tone(board.median_change_pct)}>
                  {percent(board.median_change_pct)}
                </strong>
              </span>
            )}
            {board.rankable_members != null && board.member_count != null && (
              <span>
                覆盖{" "}
                <strong>
                  {integer(board.rankable_members)} /{" "}
                  {integer(board.member_count)}
                </strong>
              </span>
            )}
            {board.first_board_count != null && (
              <span>
                首板{" "}
                <strong>
                  {evidenceCount(
                    board.first_board_count,
                    board.board_height_count_state,
                  )}{" "}
                  只
                </strong>
              </span>
            )}
            {board.multi_board_count != null && (
              <span>
                连板{" "}
                <strong>
                  {evidenceCount(
                    board.multi_board_count,
                    board.board_height_count_state,
                  )}{" "}
                  只
                </strong>
              </span>
            )}
            {board.member_date && <span>名单 {board.member_date}</span>}
            {signal && (
              <span
                className="intraday-direction-signal"
                data-signal={signal.signal}
              >
                {DISCOVERY_SIGNALS[signal.signal]}
              </span>
            )}
            {(board.change_pp ?? signal?.change_pp) != null && (
              <span>
                较上轮{" "}
                <strong className={tone(board.change_pp ?? signal?.change_pp)}>
                  {delta(board.change_pp ?? signal?.change_pp, "百分点")}
                </strong>
              </span>
            )}
            {(board.amount_delta ?? signal?.amount_delta) != null && (
              <span>
                区间新增成交{" "}
                <strong>
                  {amount(board.amount_delta ?? signal?.amount_delta)}
                </strong>
              </span>
            )}
          </div>
        )}
        <div
          className="intraday-detail-tabs intraday-filters"
          role="group"
          aria-label="个股观察类型"
        >
          {stockModes.map(([value, label]) => (
            <button
              className="btn small"
              key={value}
              aria-pressed={value === displayedMode}
              onClick={() => setStockMode(value)}
            >
              {label}
              {value === "broken_stocks" &&
                (board ? board.broken_count : breakout?.count) != null && (
                  <span className="intraday-break-count">
                    {board
                      ? evidenceCount(
                          board.broken_count,
                          board.broken_count_state,
                        )
                      : evidenceCount(breakout?.count, breakout?.state)}
                  </span>
                )}
            </button>
          ))}
        </div>
        <Ranking
          key={`${board?.instrument_id || "market"}:${displayedMode}`}
          rows={rowsFor(displayedMode)}
          onStock={onStock}
          stockMode={displayedMode}
          historyScope={historyScope}
          memberScope={
            displayedMode === "members" && board
              ? {
                  ...historyScope,
                  snapshotId: snapshot.id,
                  topicCode: board.instrument_id,
                }
              : undefined
          }
          subtitle={
            displayedMode === "members"
              ? "按所选时点的成员名单和报价展示"
              : displayedMode === "candidates"
                ? "价格走强、未列入本轮已核验封板池"
                : displayedMode === "sealed_stocks"
                  ? "本轮封板池核验样本"
                  : "本轮炸板池核验样本"
          }
          emptyTitle={
            displayedMode === "members"
              ? "当前没有可核验的成员报价"
              : displayedMode === "candidates"
                ? poolComplete
                  ? "本轮暂无符合条件的走强股"
                  : "封板池待核验"
                : displayedMode === "sealed_stocks"
                  ? "本轮暂无封板样本"
                  : breakout?.state === "unknown"
                    ? "本轮炸板池待核验"
                    : "本轮暂无可核验炸板样本"
          }
        />
      </section>
    </div>
  );
}

const captureDomainLabels: Record<string, string> = {
  stocks: "股票报价",
  boards: "板块报价",
  indices: "指数报价",
  limit_up: "涨停池",
  limit_break: "炸板池",
  limit_pools: "封板／炸板状态",
};

function partialSnapshotNote(snapshot?: Row) {
  if (snapshot?.quality !== "partial") return "";
  const gaps = ((snapshot.summary?.gaps || []) as Row[]).flatMap((gap) => {
    if (!gap.reason) return [];
    const domain =
      typeof gap.domain === "string"
        ? gap.domain.replace(/^boards_\d+$/, "boards")
        : "";
    const label = captureDomainLabels[domain];
    return [
      label && !String(gap.reason).includes(label)
        ? `${label}：${gap.reason}`
        : gap.reason,
    ];
  });
  return (
    [...new Set(gaps)].join("。") ||
    snapshot.summary?.reason ||
    "已有该时点数据，部分报价或来源证据待核验。"
  );
}

export default function IntradayObservation({
  date,
  today,
  dateFixed,
  onResetDate,
  onStock,
}: {
  date: string;
  today: string;
  dateFixed: boolean;
  onResetDate: () => void;
  onStock?: OpenStock;
}) {
  const active = usePageActive();
  const result = useApi<Row>(
    date ? `/market/intraday?date=${encodeURIComponent(date)}` : null,
  );
  const view = result.data?.date === date ? result.data : null;
  const snapshots: Row[] = view?.snapshots || [];
  const anchors: Row[] = view?.anchors || [];
  const slots = view ? timelineSlots(view) : [];
  const samples = new Map<string, Row>(
    [...snapshots, ...anchors].map((row) => [row.slot, row]),
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [rotationBoard, setRotationBoard] = useState<string>();
  const pendingDrill = useRef(false);
  const details = useRef<HTMLDivElement>(null);
  const latest = slots
    .filter((slot) => samples.has(slot.key))
    .sort((a, b) => a.start.localeCompare(b.start))
    .at(-1);
  const selectedSlot =
    selected && slots.some((slot) => slot.key === selected)
      ? selected
      : latest?.key || view?.current_slot?.key || slots[0]?.key;
  const snapshot = samples.get(selectedSlot);
  const anchor = anchors.find((row) => row.slot === selectedSlot);
  const brief = ((view?.briefs || []) as Row[]).find(
    (row) => row.slot === selectedSlot,
  );
  const historyScope = { date, slot: selectedSlot };
  const openStock =
    onStock && snapshot
      ? (id: string) =>
          onStock(id, { slot: selectedSlot, snapshotId: snapshot.id })
      : undefined;
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [error, setError] = useState("");
  const lastRequest = useRef<{ slot: string; key: string } | null>(null);
  const reload = result.reload;
  const job = view?.latest_job;
  const running = activeStatus(job?.status) || activeStatus(view?.state);
  useEffect(() => {
    if (!pendingDrill.current) return;
    pendingDrill.current = false;
    details.current?.focus({ preventScroll: true });
    details.current?.scrollIntoView({ block: "start" });
  }, [selectedSlot, rotationBoard]);
  useEffect(() => {
    if (!active || date !== view?.today) return;
    const timer = window.setInterval(
      reload,
      running || submitted ? 2500 : 30000,
    );
    return () => window.clearInterval(timer);
  }, [active, date, view?.today, running, submitted, reload]);
  useEffect(() => {
    if (!submitted) return;
    if (job?.job_id === submitted || !view?.can_capture) {
      setSubmitted(null);
      return;
    }
    const timer = window.setTimeout(() => setSubmitted(null), 15000);
    return () => window.clearTimeout(timer);
  }, [submitted, job?.job_id, view?.can_capture]);

  async function capture() {
    if (pending || submitted || !view?.can_capture) return;
    const slot = view.current_slot?.key || "";
    if (!lastRequest.current || lastRequest.current.slot !== slot)
      lastRequest.current = { slot, key: requestKey() };
    setPending(true);
    setError("");
    try {
      const task = await post<Row>("/market/intraday/jobs", {
        date,
        request_key: lastRequest.current.key,
      });
      setSelected(null);
      setRotationBoard(undefined);
      setSubmitted(task.job_id || null);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "提交失败");
    } finally {
      setPending(false);
      reload();
    }
  }

  const summary = snapshot?.summary;
  const partialNote = partialSnapshotNote(snapshot);
  const historical = !!today && !!date && date < today;
  const aiStage = aiMarketStage(selectedSlot);
  return (
    <div className="intraday-workbench">
      {active && <PageScrollEdges label="盘中观察" />}
      <section className="panel intraday-header" aria-label="盘中观察">
        <div className="intraday-hero">
          <div className="intraday-hero-meta" role="status">
            {date && (
              <time className="intraday-active-date" dateTime={date}>
                {date}
              </time>
            )}
            {historical && (
              <span className="intraday-history-tag">历史快照</span>
            )}
            {snapshot && (
              <span className="intraday-snapshot-time">
                <Clock3 size={13} />
                {snapshot.label ||
                  snapshot.slot_label ||
                  slots.find((slot) => slot.key === snapshot.slot)?.label}{" "}
                · {clock(snapshot.collected_end_at)}
              </span>
            )}
          </div>
          <div className="intraday-actions">
            {date && aiStage && (
              <AIEntry kind="market" asOf={date} marketStage={aiStage} />
            )}
            <IntradayAutomation />
            {(historical || dateFixed) && (
              <button
                className="btn small"
                onClick={onResetDate}
                disabled={!today}
              >
                回到今天
              </button>
            )}
            <button
              className="btn"
              onClick={reload}
              aria-label="刷新盘中观察状态"
            >
              <RefreshCw size={15} />
              刷新状态
            </button>
            <button
              className="btn primary"
              disabled={!view?.can_capture || running || pending || !!submitted}
              onClick={capture}
              title={view?.reason || undefined}
            >
              <Activity size={15} />
              {running
                ? "采集中"
                : pending || submitted
                  ? "提交中"
                  : "采集当前时段"}
            </button>
          </div>
        </div>
        {view?.preview_notice && <Warning>{view.preview_notice}</Warning>}
        {error && <ErrorBox message={error} />}
        {result.error && <ErrorBox message={result.error} retry={reload} />}
        {!snapshot && view?.reason && (
          <p className="intraday-intro">{view.reason}</p>
        )}
        {job?.error_summary && !running && (
          <Warning>{job.error_summary}</Warning>
        )}
        {slots.length > 0 && (
          <div
            className="intraday-timeline"
            role="group"
            aria-label="盘中观察时段"
          >
            {slots.map((slot) => {
              const item = samples.get(slot.key);
              const current = view?.current_slot?.key === slot.key;
              const waitingClose = !item && slot.key === "close" && !historical;
              const status = item
                ? item.quality === "partial"
                  ? "部分留存"
                  : "已留存"
                : current
                  ? running
                    ? "采集中"
                    : "采集窗口"
                  : waitingClose
                    ? "等待发布"
                    : historical
                      ? "缺快照"
                      : "未留存";
              return (
                <button
                  key={slot.key}
                  className="intraday-slot"
                  data-current={current}
                  data-available={!!item}
                  title={partialSnapshotNote(item) || undefined}
                  aria-pressed={selectedSlot === slot.key}
                  onClick={() => {
                    setSelected(slot.key);
                    setRotationBoard(undefined);
                  }}
                >
                  <time>{slot.start}</time>
                  <span>{item?.label || slot.label}</span>
                  <small>
                    {status}
                    {slot.legacy ? " · 旧窗口" : ""}
                  </small>
                </button>
              );
            })}
          </div>
        )}
        {partialNote && (
          <div className="intraday-coverage-note" role="status">
            <strong>部分留存</strong>
            <span>{partialNote}</span>
          </div>
        )}
      </section>
      {brief && <BriefCard brief={brief} selectedSlot={selectedSlot} />}
      {anchor?.slot === "auction" && (
        <AuctionDiscovery
          date={date}
          active={active}
          chartDate={date}
          onStock={openStock}
        />
      )}
      <div className="intraday-stage-details" ref={details} tabIndex={-1}>
        {anchor ? (
          <AnchorDetails
            key={`${anchor.id}:${rotationBoard || ""}`}
            snapshot={anchor}
            requestedBoard={rotationBoard}
            onStock={openStock}
            historyScope={historyScope}
          />
        ) : snapshot ? (
          <>
            <IntradaySnapshotFeedback snapshot={snapshot} onStock={openStock} />
            {snapshot.strength_coverage ||
            discoveryDirections(snapshot, historyScope.date, historyScope.slot)
              .length > 0 ? (
              <StrengthView
                key={`${snapshot.id}:${rotationBoard || ""}`}
                snapshot={snapshot}
                onStock={openStock}
                requestedBoard={rotationBoard}
                historyScope={historyScope}
              />
            ) : (
              <div className="intraday-rankings" key={snapshot.id}>
                <Ranking
                  rows={snapshot.sectors || []}
                  sector
                  title="板块指数涨幅"
                  subtitle="旧快照未留存板块成员与封板证据"
                  showRankChange
                />
                <Ranking
                  rows={snapshot.stocks || []}
                  onStock={openStock}
                  historyScope={historyScope}
                  title="个股涨幅"
                  subtitle="旧快照未留存封板证据"
                  showRankChange
                />
              </div>
            )}
            <details className="intraday-scope intraday-snapshot-scope">
              <summary>
                快照采集范围 · {clock(snapshot.collected_start_at)}–
                {clock(snapshot.collected_end_at)}（北京时间）
                {snapshot.quality === "partial" && " · 有待核验项"}
              </summary>
              <div className="intraday-facts">
                {summary?.stock_declared != null && (
                  <span>
                    来源声明{" "}
                    <strong>{integer(summary.stock_declared)} 只股票</strong>
                  </span>
                )}
                {summary?.coverage?.stocks?.rankable != null && (
                  <span>
                    可排名{" "}
                    <strong>
                      {integer(summary.coverage.stocks.rankable)} 只股票
                    </strong>
                  </span>
                )}
                {summary?.board_planned != null && (
                  <span>
                    板块报价{" "}
                    <strong>
                      {integer(summary.board_returned)} /{" "}
                      {integer(summary.board_planned)}
                    </strong>
                  </span>
                )}
                {summary?.comparison_slot && (
                  <span>与同日上一份可比快照比较</span>
                )}
              </div>
              {summary?.reason && (
                <p className="intraday-intro">{summary.reason}</p>
              )}
              {summary?.recent_listing_count > 0 && (
                <>
                  <p className="intraday-intro">
                    新股单列（上市前 5 个交易日，共{" "}
                    {integer(summary.recent_listing_count)} 只可排名）
                  </p>
                  <StockSamples
                    rows={summary.recent_listings || []}
                    onStock={openStock}
                  />
                </>
              )}
              <p className="intraday-intro">
                排名基于已取得的报价。区间成交额是两次累计值之差，不代表净流入。快照之间没有连续分时记录。
              </p>
            </details>
          </>
        ) : result.loading && !view ? (
          <Loading />
        ) : (
          <Panel>
            <Empty
              title={
                running && view?.current_slot?.key === selectedSlot
                  ? "正在采集盘中报价"
                  : `${slots.find((slot) => slot.key === selectedSlot)?.start || "所选时点"} 暂无快照`
              }
              description={
                selectedSlot === "close"
                  ? "收盘行情正式发布后更新全天复盘。"
                  : `${date} 的这个时点没有留存报价，因此无法显示该时点的个股与板块。`
              }
              action={
                samples.size > 0 && (
                  <div className="intraday-expand">
                    {slots
                      .filter((slot) => samples.has(slot.key))
                      .map((slot) => (
                        <button
                          type="button"
                          className="btn small"
                          key={slot.key}
                          onClick={() => {
                            setSelected(slot.key);
                            setRotationBoard(undefined);
                          }}
                        >
                          查看 {slot.start} {slot.label}
                        </button>
                      ))}
                  </div>
                )
              }
            />
          </Panel>
        )}
      </div>
      <Rotation
        key={`${date}:${selectedSlot}`}
        stages={slots}
        samples={samples}
        activeStage={selectedSlot}
        onSelect={(slot, board) => {
          if (selectedSlot === slot && rotationBoard === board) {
            details.current?.focus({ preventScroll: true });
            details.current?.scrollIntoView({ block: "start" });
            return;
          }
          pendingDrill.current = true;
          setSelected(slot);
          setRotationBoard(board);
        }}
      />
    </div>
  );
}
