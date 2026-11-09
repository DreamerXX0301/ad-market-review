import NoonStockChange from "./NoonStockChange";
import MarketSuspensionBadge from "./MarketSuspensionBadge";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";
import {
  CalendarDays,
  CheckCircle2,
  Database,
  RefreshCw,
  Sun,
  ChevronUp,
  ChevronDown,
} from "lucide-react";
import { api, post, requestKey, useApi, usePageActive } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Panel as BasePanel,
  Warning,
} from "../../components/UI";
import { amount, integer, num, percent, tone } from "../../lib/format";
import {
  noonCaptureState,
  noonCanResume,
  noonClock,
  noonIndexAmountDelta,
  noonLines,
  noonNumber,
  noonPayload,
  noonPreviousText,
  noonPreviousFacts,
  noonRows,
  noonShouldPoll,
  noonSourceRows,
  noonSourceCaption,
  noonStateText,
} from "./noonPresentation";
import "./NoonReview.css";
import "./NoonControls.css";
import NoonWorkbenchAnalysis from "./NoonWorkbenchAnalysis";
import NoonReadGuide from "./NoonReadGuide";
import NoonTemperature from "./NoonTemperature";
import NoonLadderTopics from "./NoonLadderTopics";
import CopyStockCode from "../../components/CopyStockCode";
import AIEntry from "../ai/AIEntry";
import FloatingScrollControls from "../../components/FloatingScrollControls";
import NoonBoardFilter, { NoonBoardTag } from "./NoonBoardFilter";
import { filterRows } from "./noonBoardPresentation";
import type { NoonBoardFilterValue } from "./noonBoardPresentation";
import {
  beginNoonRun,
  acceptNoonRun,
  releaseNoonRun,
  reconcileNoonRun,
  noonRequestRejected,
  adoptExistingNoonRun,
} from "../../components/noonRunLock";
import { noonTextParts } from "./noonRichText";
import {
  noonEnrichQuotes,
  noonEventLabels,
  noonKnownHeight,
  noonQuoteContext,
  noonSortRows,
} from "./noonQuotePresentation";
import {
  noonManualScrollKey,
  noonReadingActivationLine,
  noonReadingAtLine,
  noonReadingScrollOwner,
} from "./noonReadingScroll";
import {
  NOON_READING_SECTIONS,
  noonReadingSection,
} from "./noonReadGuidePresentation";

const NoonCloseReview = lazy(() => import("./NoonCloseReview"));

function Panel({ children, ...props }: ComponentProps<typeof BasePanel>) {
  return (
    <BasePanel {...props}>
      <div className="noon-panel-body">{children}</div>
    </BasePanel>
  );
}

const INDICES = [
  ["000001.SH", "上证指数"],
  ["399001.SZ", "深证成指"],
  ["399006.SZ", "创业板指"],
  ["000688.SH", "科创50"],
];
const PREVIOUS_STATES: Record<string, string> = {
  retained: "已留存复盘",
  background_only: "盘后事实背景",
  missing: "未留存前一交易日复盘",
  calendar_unknown: "前一交易日待核验",
};
const SOURCE_STATES: Record<string, string> = {
  ready: "已取得",
  received: "已取得",
  complete: "覆盖通过",
  partial: "部分可用",
  failed: "请求失败",
  unknown: "待核验",
  unavailable: "不可用",
  empty_unverified: "空结果待核验",
  observed: "已取得",
  outside_window: "窗口外响应已剔除",
  credential_echo_rejected: "响应核验未通过",
  invalid_source_time: "来源时间未通过核验",
  invalid_response: "来源响应未通过核验",
};

function MarketText({ text }: { text: string }) {
  return (
    <>
      {noonTextParts(text).map((part, index) =>
        part.tone === "neutral" ? (
          part.text
        ) : (
          <span className={part.tone} key={index}>
            {part.text}
          </span>
        ),
      )}
    </>
  );
}
function Lines({ values, empty }: { values: unknown; empty?: string }) {
  const lines = noonLines(values);
  return lines.length ? (
    <ul className="noon-lines">
      {lines.map((line, index) => (
        <li key={index}>
          <MarketText text={line} />
        </li>
      ))}
    </ul>
  ) : (
    <p className="noon-unknown">{empty || "暂无可核验说明"}</p>
  );
}

function QuoteIdentity({
  row,
  onStock,
  chartDate,
}: {
  row: Row;
  onStock?: (id: string) => void;
  chartDate?: string;
}) {
  return (
    <>
      {onStock && /^\d{6}\.(SH|SZ|BJ)$/.test(row.instrument_id || "") ? (
        <button
          type="button"
          className="noon-stock-link"
          disabled={!chartDate}
          title={
            chartDate
              ? `正式行情截至 ${chartDate}，同日收盘发布后优先显示完整日 K`
              : "该午评之前没有已发布收盘行情"
          }
          onClick={() => onStock(row.instrument_id)}
        >
          {row.name || row.instrument_id}
        </button>
      ) : (
        <span>
          {row.name || row.instrument_id || row.topic_code || "身份待核验"}
        </span>
      )}
      <NoonBoardTag row={row} />
      <MarketSuspensionBadge evidence={row.suspension} />
      <small>
        <CopyStockCode instrumentId={row.instrument_id} code={row.topic_code} />
      </small>
    </>
  );
}
function QuoteTags({ row }: { row: Row }) {
  const industries = noonRows(row.industries),
    concepts = noonRows(row.topics);
  const all = [...industries, ...concepts].filter(
    (tag) => typeof tag.name === "string" && tag.name.trim(),
  );
  const shown = [...industries.slice(0, 1), ...concepts.slice(0, 2)].filter(
    (tag) => typeof tag.name === "string" && tag.name.trim(),
  );
  if (!shown.length) shown.push(...all.slice(0, 3));
  if (!all.length) return <span className="noon-unknown">—</span>;
  const tag = (item: Row, index: number) => (
    <span
      key={`${item.topic_code}-${index}`}
      className={`noon-membership-tag ${item.kind === "industry" ? "industry" : "concept"}`}
      title={`${item.kind === "industry" ? "行业" : "概念"}：${item.name}；成员名单日 ${item.members_observed_date || "未知"}；归属不是涨停原因`}
    >
      {item.name}
    </span>
  );
  return (
    <div className="noon-membership-tags" aria-label="原采集代际行业与概念归属">
      {shown.map(tag)}
      {all.length > shown.length && (
        <details className="noon-membership-more">
          <summary title="展开全部行业与概念">
            +{all.length - shown.length}
          </summary>
          <div>
            {all.map(tag)}
            <small>成员归属按原采集代际名单展示，不代表上涨原因。</small>
          </div>
        </details>
      )}
    </div>
  );
}
function QuoteEventTags({ row }: { row: Row }) {
  const labels = noonEventLabels(row);
  return labels.length ? (
    <div className="noon-event-tags" aria-label="已核验午间及昨日事件状态">
      {labels.map((item) => (
        <span className={`noon-event-chip ${item.tone}`} key={item.label}>
          {item.label}
        </span>
      ))}
    </div>
  ) : null;
}
function quoteTheme(row: Row): string | null {
  return (
    [row.limit_up_reason, row.theme_text, row.mainline].find(
      (value) => typeof value === "string" && value.trim(),
    ) || null
  );
}
function QuoteTable({
  rows,
  label,
  limit = 10,
  onStock,
  chartDate,
  context = {},
  poolKind,
  defaultSort,
  boardFilter = "all",
}: {
  rows: Row[];
  label: string;
  limit?: number;
  onStock?: (id: string) => void;
  chartDate?: string;
  context?: Record<string, Row>;
  poolKind?: string;
  defaultSort?: string;
  boardFilter?: NoonBoardFilterValue;
}) {
  const enriched = filterRows(noonEnrichQuotes(rows, context), boardFilter);
  const [sort, setSort] = useState<{
    key: string;
    direction: "asc" | "desc";
  } | null>(null);
  type Column = {
    key: string;
    label: string;
    value: (row: Row) => string | number | null;
    cell: (row: Row) => ReactNode;
    numeric?: boolean;
    always?: boolean;
  };
  const number = (row: Row, field: string) => noonNumber(row[field]);
  const column = (
    key: string,
    title: string,
    display: (value: number | null) => string,
    value = (row: Row) => number(row, key),
  ): Column => ({
    key,
    label: title,
    numeric: true,
    value,
    cell: (row: Row) => display(value(row)),
  });
  const columns: Column[] = [
    {
      key: "name",
      label: "股票",
      always: true,
      value: (row: Row) => row.name || row.instrument_id || null,
      cell: (row: Row) => (
        <QuoteIdentity row={row} onStock={onStock} chartDate={chartDate} />
      ),
    },
    {
      key: "classification",
      label: "行业 / 概念",
      value: (row: Row) =>
        [...noonRows(row.industries), ...noonRows(row.topics)]
          .map((tag) => tag.name)
          .filter(Boolean)
          .join("、") || null,
      cell: (row: Row) => <QuoteTags row={row} />,
    },
    column(
      "previous_board_count",
      "昨日高度",
      (value) => (value == null ? "—" : `${value}板`),
      (row: Row) => noonKnownHeight(row.previous_board_count),
    ),
    ...(poolKind === "limit_up"
      ? [
          column(
            "continue_day_cnt",
            "本次来源板数",
            (value) => (value == null ? "—" : `${value}板`),
            (row: Row) => noonKnownHeight(row.continue_day_cnt),
          ),
        ]
      : []),
    ...(!poolKind
      ? [column("latest_price", "午间最新价", (value) => num(value))]
      : []),
    {
      ...column("change_pct", "午间涨跌幅", (value) => percent(value)),
      cell: (row: Row) => (
        <NoonStockChange
          value={number(row, "change_pct")}
          state={row.current_event_state}
        />
      ),
    },
    ...(!poolKind
      ? [column("amount", "累计成交额", (value) => amount(value))]
      : []),
    ...(poolKind === "limit_break"
      ? [column("open_times", "开板次数", (value) => integer(value))]
      : []),
    ...(poolKind === "limit_down"
      ? [
          {
            key: "last_limit_time",
            label: "最近跌停时间",
            value: (row: Row) =>
              typeof row.last_limit_time === "string" &&
              row.last_limit_time.trim()
                ? row.last_limit_time
                : null,
            cell: (row: Row) => row.last_limit_time || "—",
          },
        ]
      : []),
    ...(poolKind === "limit_up"
      ? [column("seal_money", "单次封单额", (value) => amount(value))]
      : []),
    ...(poolKind && poolKind !== "limit_up"
      ? [
          {
            key: "first_limit_time",
            label: "首次来源时间",
            value: (row: Row) =>
              typeof row.first_limit_time === "string" &&
              row.first_limit_time.trim()
                ? row.first_limit_time
                : null,
            cell: (row: Row) => row.first_limit_time || "—",
          },
        ]
      : []),
    {
      key: "event_labels",
      label: "事件状态",
      value: (row: Row) =>
        noonEventLabels(row)
          .map((item) => item.label)
          .join("、") || null,
      cell: (row: Row) => <QuoteEventTags row={row} />,
    },
    {
      key: "theme",
      label: "题材 / 来源原因",
      value: quoteTheme,
      cell: (row: Row) => {
        const text = quoteTheme(row);
        if (!text) return "—";
        const origin = row.limit_up_reason
          ? "本次午间来源"
          : row.theme_source === "previous_session_pool"
            ? "昨日来源"
            : row.theme_source === "previous_manual" || row.mainline
              ? "昨日人工关注"
              : "本次午间来源";
        return (
          <details className="noon-reason-details">
            <summary>来源原因</summary>
            <span
              title={`${origin} ${row.theme_date || ""}；保留来源原文，不代表已核验归因。`}
            >
              {text}
            </span>
          </details>
        );
      },
    },
  ].filter(
    (item) =>
      item.always || enriched.some((row: Row) => item.value(row) != null),
  );
  const preferredKey =
    defaultSort ||
    (enriched.some(
      (row: Row) => noonKnownHeight(row.previous_board_count) != null,
    )
      ? "previous_board_count"
      : poolKind === "limit_up"
        ? "continue_day_cnt"
        : "change_pct");
  const initialKey = columns.some((item) => item.key === preferredKey)
    ? preferredKey
    : columns.some((item) => item.key === "change_pct")
      ? "change_pct"
      : "name";
  const active =
    sort && columns.some((item) => item.key === sort.key)
      ? sort
      : { key: initialKey, direction: "desc" as const };
  const visible = noonSortRows(
    enriched,
    active.key,
    active.direction,
    (row, key) => columns.find((item) => item.key === key)?.value(row),
  ).slice(0, limit);
  if (!visible.length)
    return (
      <p className="noon-unknown">
        {boardFilter !== "all"
          ? "该板别暂无展示样本"
          : "该范围暂无可核验报价。"}
      </p>
    );
  return (
    <div className="noon-table-wrap">
      <table className="noon-table noon-quote-table" aria-label={label}>
        <thead>
          <tr>
            {columns.map((item) => (
              <th
                key={item.key}
                className={
                  item.numeric ? "noon-number-column" : "noon-text-column"
                }
                aria-sort={
                  active.key === item.key
                    ? active.direction === "desc"
                      ? "descending"
                      : "ascending"
                    : "none"
                }
              >
                <button
                  type="button"
                  className="noon-sort-button"
                  onClick={() =>
                    setSort({
                      key: item.key,
                      direction:
                        active.key === item.key && active.direction === "desc"
                          ? "asc"
                          : "desc",
                    })
                  }
                  aria-label={`${label}按${item.label}${active.key === item.key && active.direction === "desc" ? "升序" : "降序"}`}
                >
                  {item.label}
                  <span aria-hidden="true">
                    {active.key === item.key
                      ? active.direction === "desc"
                        ? "↓"
                        : "↑"
                      : "↕"}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row, index) => (
            <tr key={row.instrument_id || row.topic_code || index}>
              {columns.map((item) => (
                <td
                  key={item.key}
                  className={
                    item.numeric ? "noon-number-column" : "noon-text-column"
                  }
                >
                  {item.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function PoolClassifications({
  value,
  onStock,
  chartDate,
  boardFilter,
}: {
  value?: Row;
  onStock?: (id: string) => void;
  chartDate?: string;
  boardFilter?: NoonBoardFilterValue;
}) {
  if (!value) return null;
  return (
    <div className="noon-pool-classifications" aria-label="三池行业概念数量">
      {(
        [
          ["limit_up", "涨停", "up"],
          ["limit_down", "跌停", "down"],
          ["limit_break", "炸板", ""],
        ] as const
      ).map(([key, label, color]) => {
        const item = value[key];
        if (!item || item.sample_count === 0) return null;
        const industries = noonRows(item.industries),
          concepts = noonRows(item.topics);
        return (
          <details key={key}>
            <summary>
              <span className={color}>{label}样本归属</span>
              <span>
                {industries.length || concepts.length
                  ? `${industries.length} 行业 / ${concepts.length} 概念`
                  : "归属待核验"}
              </span>
            </summary>
            <NoonLadderTopics
              value={item}
              boardFilter={boardFilter}
              showSampleChange={key !== "limit_up"}
              limit={5}
              sampleLabel={label}
              onStock={onStock}
              chartDate={chartDate}
            />
          </details>
        );
      })}
    </div>
  );
}
function PoolFacts({
  events,
  onStock,
  chartDate,
  context,
  boardFilter,
  classifications,
}: {
  events: Row;
  onStock?: (id: string) => void;
  chartDate?: string;
  context: Record<string, Row>;
  boardFilter?: NoonBoardFilterValue;
  classifications?: Row;
}) {
  return (
    <div className="noon-pool-details">
      {[
        ["limit_up", "涨停池样本"],
        ["limit_down", "跌停池样本"],
        ["limit_break", "炸板池样本"],
      ].map(([key, title]) => {
        const event = events[key] || {},
          rows = noonRows(event.items);
        const stats = classifications?.[key] || {};
        const meanChange = noonNumber(stats.mean_change_pct);
        return (
          <details className="noon-details" key={key}>
            <summary>
              <span>
                {title} · 已核验 {integer(noonNumber(event.count))} / 来源声明{" "}
                {integer(noonNumber(event.declared_total))}
              </span>
              {key !== "limit_up" && (
                <span
                  className="noon-pool-mean-change"
                  title="全池有效午间涨跌幅的算术平均，缺报价不计入；不随上市板别筛选重算"
                >
                  平均涨跌幅{" "}
                  <b className={tone(meanChange)}>{percent(meanChange)}</b>
                  <small>
                    （有效{integer(noonNumber(stats.valid_count))}）
                  </small>
                </span>
              )}
            </summary>
            {!rows.length ? (
              <p>
                {event.state === "observed" && event.count === 0
                  ? "来源已确认本次为 0 只。"
                  : event.reason || "本次没有可核验样本，不能据此认定零事件。"}
              </p>
            ) : (
              <QuoteTable
                rows={rows}
                label={title}
                limit={30}
                onStock={onStock}
                chartDate={chartDate}
                context={context}
                poolKind={key}
                boardFilter={boardFilter}
                defaultSort={
                  key === "limit_up" ? "continue_day_cnt" : "change_pct"
                }
              />
            )}
            <p className="noon-footnote">
              {event.scope || "来源池范围"} ·
              表中按所选列排序显示前30条；单次封单额不代表稳定性，几天几板文本不等于连续板数。
            </p>
          </details>
        );
      })}
    </div>
  );
}

function PreviousReview({
  previous,
  compact = false,
  onStock,
  chartDate,
  context = {},
  boardFilter,
}: {
  previous: Row;
  context?: Record<string, Row>;
  boardFilter?: NoonBoardFilterValue;
  compact?: boolean;
  onStock?: (id: string) => void;
  chartDate?: string;
}) {
  const observations = noonRows(previous.observations);
  const mainlines = noonRows(previous.mainlines);
  const visibleMainlines = filterRows(mainlines, boardFilter || "all");
  const facts = noonPreviousFacts(previous);
  const retained = previous.state === "retained";
  const title = PREVIOUS_STATES[previous.state] || "复盘来源待核验";
  if (compact)
    return (
      <>
        <div className="noon-previous-state">
          <Badge tone={retained ? "good" : "warn"}>{title}</Badge>
          <span>{previous.date || "日期待核验"}</span>
        </div>
        <Lines
          values={noonPreviousText(previous).slice(0, 3)}
          empty={
            retained
              ? "已保存的复盘原文可在对照页查看。"
              : "未留存的判断不补写；可用的前日行情背景另列。"
          }
        />
        <p className="noon-footnote">复盘原文与版本随本次午评固定保存。</p>
        {facts && (
          <p className="noon-footnote">
            <MarketText
              text={`前日收盘可比 ${integer(noonNumber(facts.comparable))} 只 · 上涨 ${integer(noonNumber(facts.up))} / 下跌 ${integer(noonNumber(facts.down))} / 平盘 ${integer(noonNumber(facts.flat))}。仅事实背景。`}
            />
          </p>
        )}
      </>
    );
  return (
    <div className="noon-tab-stack">
      <Panel
        title="上个交易日复盘与午间事实"
        subtitle="昨天按前一市场交易日确定；原文不是今天重算的判断"
      >
        <div className="noon-previous-state">
          <Badge tone={retained ? "good" : "warn"}>{title}</Badge>
          <span>{previous.date || "日期待核验"}</span>
        </div>
        <div className="noon-compare">
          <section>
            <h3>{retained ? "原复盘内容" : "已有盘后背景"}</h3>
            <Lines
              values={noonPreviousText(previous)}
              empty={
                retained
                  ? "当前留存没有可摘要的文字，请查看下方原始内容。"
                  : "没有可核验的前日复盘或背景，保持缺失。"
              }
            />
            <div className="noon-proof-meta">
              {facts && (
                <div
                  className="noon-background-facts"
                  aria-label="前一交易日已发布收盘事实"
                >
                  <div className="noon-coverage-cells">
                    <div>
                      <span>已留存报价样本</span>
                      <strong>{integer(noonNumber(facts.observed))}</strong>
                    </div>
                    <div>
                      <span>可比样本</span>
                      <strong>{integer(noonNumber(facts.comparable))}</strong>
                    </div>
                    <div>
                      <span>上涨 / 下跌</span>
                      <strong>
                        <span className="up">
                          {integer(noonNumber(facts.up))}
                        </span>{" "}
                        /{" "}
                        <span className="down">
                          {integer(noonNumber(facts.down))}
                        </span>
                      </strong>
                    </div>
                    <div>
                      <span>平盘</span>
                      <strong>{integer(noonNumber(facts.flat))}</strong>
                    </div>
                  </div>
                  <p className="noon-footnote">
                    {facts.notice ||
                      "采集时读取的前日收盘事实背景，不代表昨天已保存的判断。"}
                  </p>
                </div>
              )}
              <span>实际记录 {noonClock(previous.recorded_at_ms)}</span>
              <span>复盘版本 {previous.snapshot_id || "未留存"}</span>
            </div>
          </section>
          <section>
            <h3>本次午间对应事实</h3>
            <Lines
              values={observations
                .filter(
                  (row) =>
                    typeof row.message === "string" ||
                    typeof row.reason === "string",
                )
                .slice(0, 6)}
              empty="下表按原关注股票列示当前报价；没有对应报价时保持未知。"
            />
            <p className="noon-footnote">
              自由文本观察只做事实对照，不自动判定兑现或证伪。
            </p>
          </section>
        </div>
        {retained && previous.payload && (
          <details className="noon-details">
            <summary>查看已冻结的复盘原文</summary>
            <FrozenText value={previous.payload} />
          </details>
        )}
      </Panel>
      <Panel
        title="原关注股票与当前表现"
        subtitle="仅使用原复盘与主线明确关联的股票，不按当前题材回填历史"
      >
        <QuoteTable
          rows={observations}
          label="昨日关注股票午间表现"
          context={context}
          boardFilter={boardFilter}
          limit={30}
          onStock={onStock}
          chartDate={chartDate}
        />
      </Panel>
      <Panel title="原主线与观察记录" subtitle="保留当时版本和记录时点">
        {!visibleMainlines.length ? (
          <p className="noon-unknown">
            {boardFilter && boardFilter !== "all"
              ? "该板别暂无展示样本"
              : "上个交易日没有可用的已留存主线记录。"}
          </p>
        ) : (
          <div className="noon-mainlines">
            {visibleMainlines.slice(0, 40).map((row, index) => (
              <article key={row.revision_id || index}>
                <strong>
                  {row.instrument_name ||
                    row.name ||
                    row.instrument_id ||
                    "原关注记录"}
                </strong>
                <NoonBoardTag row={row} />
                <span>
                  {row.display_mainline || row.mainline_name || "归属待核验"}
                </span>
                <p className="noon-footnote">
                  系统记录 {noonClock(row.recorded_at_ms)}
                </p>
                {noonRows(row.attributions).map((item, entry) => (
                  <div className="noon-attribution" key={entry}>
                    <div className="noon-attribution-source">
                      {item.source_name || "来源名称未提供"}
                      {item.source_published_at && (
                        <span>
                          {" "}
                          · 来源发布 {noonClock(item.source_published_at)}
                        </span>
                      )}
                    </div>
                    {item.source_reason && (
                      <p>
                        <b>来源转述</b>
                        {item.source_reason}
                      </p>
                    )}
                    {item.catalyst_note && (
                      <p>
                        <b>催化补充</b>
                        {item.catalyst_note}
                      </p>
                    )}
                    {item.user_note && (
                      <p>
                        <b>我的判断</b>
                        {item.user_note}
                      </p>
                    )}
                    {item.evidence_ref && (
                      <p className="noon-evidence-ref">
                        证据 {item.evidence_ref}
                      </p>
                    )}
                  </div>
                ))}
              </article>
            ))}
          </div>
        )}
        {noonLines(previous.gaps).length > 0 && (
          <Lines values={previous.gaps} />
        )}
      </Panel>
    </div>
  );
}

/** A bounded readable representation of the retained original, never an arbitrary JSON dump. */
function FrozenText({ value }: { value: unknown }) {
  const lines: string[] = [];
  const visit = (item: unknown, depth: number) => {
    if (lines.length >= 80 || depth > 6) return;
    if (typeof item === "string" && item.trim()) {
      if (!/^[0-9a-f_-]{24,}$/i.test(item) && !/^\d{4}-\d{2}-\d{2}/.test(item))
        lines.push(item.slice(0, 2000));
    } else if (Array.isArray(item))
      item.slice(0, 30).forEach((entry) => visit(entry, depth + 1));
    else if (item && typeof item === "object")
      Object.entries(item)
        .filter(
          ([key]) =>
            !["provenance", "source_records", "input_snapshot_ids"].includes(
              key,
            ),
        )
        .forEach(([, entry]) => visit(entry, depth + 1));
  };
  visit(value, 0);
  return (
    <Lines
      values={[...new Set(lines)]}
      empty="留存内容没有可读文本；来源与版本身份仍保留。"
    />
  );
}

function NoonNotes({ snapshotId }: { snapshotId: string }) {
  const saved = useApi<Row>(
    `/market/noon/${encodeURIComponent(snapshotId)}/notes`,
  );
  const [body, setBody] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const revision = useRef<number | null>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    if (saved.data?.snapshot_id === snapshotId && !dirty) {
      setBody(typeof saved.data.body === "string" ? saved.data.body : "");
      revision.current = noonNumber(saved.data.revision);
    }
  }, [saved.data, snapshotId, dirty]);
  async function save() {
    if (inFlight.current || revision.current == null || conflict) return;
    inFlight.current = true;
    setSaving(true);
    setMessage("");
    try {
      const value = await post<Row>(
        `/market/noon/${encodeURIComponent(snapshotId)}/notes`,
        { body, expected_revision: revision.current },
      );
      revision.current = noonNumber(value.revision);
      saved.setData(value);
      setDirty(false);
      setMessage("我的观察已保存为独立修订。");
      saved.reload();
    } catch (error: any) {
      setMessage(error.message || "观察保存未完成，草稿仍保留在本页。");
      if (error.status === 409) {
        setConflict(true);
        saved.reload();
      }
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }
  function useLatest() {
    if (!saved.data || saved.data.snapshot_id !== snapshotId) return;
    setBody(typeof saved.data.body === "string" ? saved.data.body : "");
    revision.current = noonNumber(saved.data.revision);
    setConflict(false);
    setDirty(false);
    setMessage("已采用当前保存版本，可继续修改。");
  }
  return (
    <Panel
      title="我的午后观察"
      subtitle="手动保存为独立修订，不修改采集事实与规则摘要"
    >
      {saved.error && <ErrorBox message={saved.error} retry={saved.reload} />}
      <label className="noon-note-label" htmlFor={`noon-note-${snapshotId}`}>
        补充自己的判断与待核对事项
      </label>
      <textarea
        id={`noon-note-${snapshotId}`}
        className="noon-note-input"
        rows={5}
        maxLength={4000}
        value={body}
        disabled={saving || (!saved.data && saved.loading)}
        onChange={(event) => {
          setBody(event.target.value);
          setDirty(true);
          setMessage("");
        }}
        placeholder="例如：下午继续核对原关注方向的上涨范围。"
      />
      <div className="noon-note-actions">
        <span>
          {body.length} / 4000{dirty ? " · 尚未保存" : ""}
        </span>
        <button
          className="btn small primary"
          disabled={
            saving ||
            !dirty ||
            revision.current == null ||
            conflict ||
            !!saved.error
          }
          onClick={save}
        >
          {saving ? "正在保存…" : "保存我的观察"}
        </button>
      </div>
      {message && (
        <p className="noon-footnote" role="status">
          {message}
        </p>
      )}
      {conflict && (
        <div className="noon-note-conflict">
          <p>
            其他窗口可能已经修订。请核对当前保存内容，再采用该版本继续编辑。
          </p>
          <details>
            <summary>查看当前保存内容</summary>
            <p>{saved.data?.body || "当前保存内容为空"}</p>
          </details>
          <button
            className="btn small"
            disabled={saved.loading || !saved.data}
            onClick={useLatest}
          >
            采用当前保存版本
          </button>
        </div>
      )}
      <p className="noon-footnote">
        {saved.data?.recorded_at_ms
          ? `最近保存 ${noonClock(saved.data.recorded_at_ms)} · 修订 ${saved.data.revision}`
          : "尚未保存个人观察"}{" "}
        · 随午评到期自动清理。
      </p>
    </Panel>
  );
}

export default function NoonReview({
  date,
  onDate,
  onDatesChanged,
  onStock: onNoonStock,
  chartDate,
  onSector,
  onCloseStock,
  onCloseSector,
}: {
  date: string;
  onDate: (value: string) => void;
  onDatesChanged?: () => void;
  onStock?: (id: string, snapshotId: string) => void;
  chartDate?: string;
  onSector?: (row: Row, view?: "chart" | "members") => void;
  onCloseStock?: (id: string, date: string) => void;
  onCloseSector?: (row: Row, view: "chart" | "members", date: string) => void;
}) {
  const active = usePageActive();
  const [tab, setTab] = useState("overview");
  // A close-tab date change reads only its small API until the original noon is opened.
  const noonReadDate = useRef(date);
  if (tab !== "close") noonReadDate.current = date;
  const result = useApi<Row>(
    `/market/noon${noonReadDate.current ? `?date=${encodeURIComponent(noonReadDate.current)}` : ""}`,
  );
  const view = result.data?.date === date || !date ? result.data : null;
  const retentionDays = view?.retention?.calendar_days;
  const selected = date || view?.date || "";
  const payload = noonPayload(view, selected);
  const [pendingDay, setPendingDay] = useState(""),
    [submitError, setSubmitError] = useState("");
  const [resuming, setResuming] = useState(false);
  const [boardFilter, setBoardFilter] = useState<NoonBoardFilterValue>("all");
  const [readingSection, setReadingSection] = useState("noon-overview-start");
  const [scrollTarget, setScrollTarget] = useState<string | null>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const scrollLockUntil = useRef(0);
  const scrollingTo = useRef<string | null>(null);
  const submitting = useRef(false),
    currentDay = useRef(selected),
    keys = useRef<Record<string, string>>({});
  currentDay.current = selected;
  const control = noonCaptureState(view, selected, pendingDay === selected);
  useEffect(() => {
    setSubmitError("");
  }, [selected]);
  useEffect(() => {
    if (view?.date === pendingDay && view?.locked_for_day === true)
      setPendingDay("");
  }, [view, pendingDay]);
  useEffect(() => {
    if (!active || !noonShouldPoll(view, selected)) return;
    const interval = window.setInterval(result.reload, 2200);
    return () => window.clearInterval(interval);
  }, [active, view?.state, selected, result.reload]);
  async function reconcileRun(day: string, key: string, error: any) {
    if (noonRequestRejected(error)) {
      // A 409 can mean another tab already owns today's run. Transfer the
      // existing gate atomically when it is readable; rejection itself must
      // still release this request if the follow-up read is unavailable.
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const checked = await api<Row>(
          `/market/noon?date=${encodeURIComponent(day)}`,
          { signal: controller.signal },
        );
        if (
          checked.date === day &&
          checked.task &&
          adoptExistingNoonRun(key, checked.task)
        )
          return;
      } catch {
        /* A confirmed rejection does not become a pending POST. */
      } finally {
        window.clearTimeout(timeout);
      }
      releaseNoonRun(key);
      setPendingDay("");
      return;
    }
    const checked = await api<Row>(
      `/market/noon?date=${encodeURIComponent(day)}`,
    );
    if (checked.date !== day) return;
    reconcileNoonRun(key, checked);
  }
  async function capture() {
    if (!view || control.disabled || submitting.current || !selected) return;
    const day = selected;
    submitting.current = true;
    setPendingDay(day);
    setSubmitError("");
    keys.current[day] ||= requestKey();
    const key = keys.current[day];
    try {
      beginNoonRun(day, key);
      const task = await post("/market/noon/jobs", {
        date: day,
        request_key: key,
      });
      acceptNoonRun(key, task);
      onDatesChanged?.();
    } catch (error: any) {
      if (currentDay.current === day)
        setSubmitError(
          error.message || "午评任务提交未完成，请重新读取本地状态。",
        );
      // Confirm the durable day ledger before offering any further action.
      try {
        await reconcileRun(day, key, error);
      } catch {
        /* The refresh below exposes the local read error; submission remains locked. */
      }
    } finally {
      submitting.current = false;
      result.reload();
    }
  }
  async function resumeOriginal() {
    if (!noonCanResume(view, selected) || submitting.current || resuming)
      return;
    submitting.current = true;
    setResuming(true);
    setSubmitError("");
    const day = selected;
    const key = (keys.current[day] ||= requestKey());
    try {
      beginNoonRun(day, key, view!.task);
      const task = await post(
        `/tasks/${encodeURIComponent(view!.task.task_id || view!.task.job_id)}/resume`,
      );
      acceptNoonRun(key, task);
    } catch (error: any) {
      setSubmitError(error.message || "原任务未能继续，请重新核对窗口。");
      try {
        await reconcileRun(day, key, error);
      } catch {
        /* Keep the gate until the server can confirm the task state. */
      }
    } finally {
      submitting.current = false;
      setResuming(false);
      result.reload();
    }
  }
  const snapshot = view?.snapshot || {},
    summary = payload?.summary || {},
    breadth = summary.breadth || {},
    events = { ...payload?.events };
  const restoredPoolKeys = new Set<string>();
  for (const key of ["limit_up", "limit_down", "limit_break"]) {
    const verifiedPool =
      snapshot.workbench?.verified_empty_event_pools?.[key] ??
      (key === "limit_down"
        ? snapshot.workbench?.limit_down_source_pool
        : null);
    if (verifiedPool?.state === "observed" && verifiedPool.count === 0) {
      events[key] = verifiedPool;
      restoredPoolKeys.add(key);
    }
  }
  const onStock =
    onNoonStock && typeof snapshot.snapshot_id === "string"
      ? (id: string) => onNoonStock(id, snapshot.snapshot_id)
      : undefined;
  const previous = payload?.previous_review || { state: "missing" };
  const quoteContext = noonQuoteContext(snapshot);
  const analysis = payload?.analysis || {};
  const allSources = noonSourceRows(payload?.sources);
  const sources = allSources.filter(
    (row) => row.name !== "manifest" && row.kind !== "manifest",
  );
  const up = noonNumber(breadth.up),
    down = noonNumber(breadth.down),
    flat = noonNumber(breadth.flat);
  const breadthTotal =
    up != null && down != null && flat != null ? up + down + flat : null;
  const gapRows = payload?.gaps;
  const gaps = noonLines(
    Array.isArray(gapRows)
      ? gapRows.filter(
          (gap) =>
            !restoredPoolKeys.has(gap?.domain) ||
            ![
              "来源为空，不能确认真实零事件",
              "来源范围、身份、价格或分页未完整核验，不能把缺失视为零事件",
            ].includes(gap?.reason),
        )
      : gapRows,
  );
  const stocks = noonRows(payload?.stocks);
  const recent = Array.isArray(view?.available_dates)
    ? view.available_dates
    : [];
  function navigateReading(id: string) {
    const section = noonReadingSection(id);
    if (!section) return;
    setTab(section.tab);
    setReadingSection(id);
    scrollingTo.current = id;
    scrollLockUntil.current = Date.now() + 1400;
    setScrollTarget(id);
  }
  function openSourceDetails() {
    const detail =
      pageRef.current?.querySelector<HTMLDetailsElement>("#noon-data-gaps");
    if (detail) {
      detail.open = true;
      detail.scrollIntoView({ block: "start", behavior: "auto" });
    }
  }
  const readingIndex = Math.max(
    0,
    NOON_READING_SECTIONS.findIndex((section) => section.id === readingSection),
  );
  function stepReading(direction: -1 | 1) {
    const next = NOON_READING_SECTIONS[readingIndex + direction];
    if (next) navigateReading(next.id);
  }
  useEffect(() => {
    if (!active || !payload || tab === "close") return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest('[role="dialog"]')) return;
      if (
        !event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        target?.closest("input,textarea,select,[contenteditable=true]")
      )
        return;
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        stepReading(event.key === "ArrowUp" ? -1 : 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, selected, readingIndex, snapshot.snapshot_id, tab]);
  useEffect(() => {
    if (!scrollTarget || !active || !payload || tab === "close") return;
    const frame = requestAnimationFrame(() => {
      // A wheel/touch/key action can supersede this click before its first frame.
      if (scrollingTo.current !== scrollTarget) {
        setScrollTarget(null);
        return;
      }
      const target = pageRef.current?.querySelector<HTMLElement>(
        `#${scrollTarget}`,
      );
      if (target) {
        target.scrollIntoView({
          behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")
            .matches
            ? "auto"
            : "smooth",
          block: "start",
        });
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }
      setScrollTarget(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [scrollTarget, tab, active, selected, snapshot.snapshot_id]);
  useEffect(() => {
    if (!active || !payload || tab === "close") return;
    const page = pageRef.current;
    if (!page) return;
    const appMain = page.closest<HTMLElement>(".app-main");
    const header = appMain?.querySelector<HTMLElement>(".topbar");
    const guide = page.querySelector<HTMLElement>(".noon-reading-sidebar");
    const sections = NOON_READING_SECTIONS.filter(
      (section) => section.tab === tab,
    );
    const anchors = sections
      .map((section) => ({
        section,
        node: page.querySelector<HTMLElement>(`#${section.id}`),
      }))
      .filter(
        (
          item,
        ): item is { section: (typeof sections)[number]; node: HTMLElement } =>
          !!item.node,
      );
    let frame = 0;
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(sync);
    };
    const sync = () => {
      frame = 0;
      const owner = noonReadingScrollOwner(page);
      const ownerRect = owner?.getBoundingClientRect();
      const viewportTop = ownerRect
        ? Math.max(0, ownerRect.top + (owner?.clientTop || 0))
        : 0;
      const viewportBottom = ownerRect
        ? Math.min(window.innerHeight, ownerRect.bottom)
        : window.innerHeight;
      const headerRect = header?.getBoundingClientRect();
      const headerHeight = headerRect?.height || 86;
      const compact = window.matchMedia?.("(max-width: 1000px)").matches;
      const guideHeight = compact
        ? guide?.getBoundingClientRect().height || 0
        : 0;
      const threshold = noonReadingActivationLine(
        viewportTop,
        headerRect?.bottom || viewportTop,
        guideHeight,
      );
      page.style.setProperty("--noon-guide-top", `${headerHeight + 10}px`);
      page.style.setProperty(
        "--noon-scroll-offset",
        `${headerHeight + guideHeight + 22}px`,
      );
      const positions = anchors
        .filter(({ node }) => node.getClientRects().length > 0)
        .map(({ section, node }) => ({
          id: section.id,
          top: node.getBoundingClientRect().top,
        }));
      const documentOwner = page.ownerDocument.scrollingElement;
      const atEnd = owner
        ? owner.scrollHeight - owner.scrollTop - owner.clientHeight <= 2
        : !!documentOwner &&
          documentOwner.scrollHeight - window.scrollY - window.innerHeight <= 2;
      if (scrollingTo.current && Date.now() < scrollLockUntil.current) {
        const target = positions.find(
          (position) => position.id === scrollingTo.current,
        );
        const reached =
          target &&
          ((target.top <= threshold && target.top >= threshold - 24) ||
            (atEnd &&
              target.top > threshold &&
              target.top < viewportBottom - 24));
        if (!reached) {
          schedule();
          return;
        }
      }
      scrollingTo.current = null;
      scrollLockUntil.current = 0;
      const current = noonReadingAtLine(
        positions,
        threshold,
        atEnd ? viewportBottom : undefined,
      );
      if (current)
        setReadingSection((previous) =>
          previous === current ? previous : current,
        );
    };
    const manual = (event: Event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('[role="dialog"]')) return;
      if (
        target &&
        !page.contains(target) &&
        target !== appMain &&
        target !== page.ownerDocument.body &&
        target !== page.ownerDocument.documentElement
      )
        return;
      if (
        event.type === "keydown" &&
        !noonManualScrollKey(
          event as KeyboardEvent,
          !!target?.closest("input,textarea,select,[contenteditable=true]"),
        )
      )
        return;
      const wasNavigating = scrollingTo.current != null;
      scrollingTo.current = null;
      scrollLockUntil.current = 0;
      setScrollTarget(null);
      if (wasNavigating) {
        const owner = noonReadingScrollOwner(page);
        if (owner)
          owner.scrollTo({
            top: owner.scrollTop,
            left: owner.scrollLeft,
            behavior: "auto",
          });
        else
          window.scrollTo({
            top: window.scrollY,
            left: window.scrollX,
            behavior: "auto",
          });
      }
      schedule();
    };
    // Scroll does not bubble from inner owners, but window capture observes both
    // document scrolling and any future constrained .app-main scroll container.
    window.addEventListener("scroll", schedule, {
      passive: true,
      capture: true,
    });
    window.addEventListener("resize", schedule);
    window.addEventListener("wheel", manual, { passive: true, capture: true });
    window.addEventListener("touchmove", manual, {
      passive: true,
      capture: true,
    });
    window.addEventListener("pointerdown", manual, true);
    window.addEventListener("keydown", manual, true);
    page.addEventListener("toggle", schedule, true);
    const resize =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(schedule)
        : null;
    for (const node of new Set([
      page,
      header,
      guide,
      ...anchors.map((item) => item.node),
    ]))
      if (node) resize?.observe(node);
    sync();
    return () => {
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("wheel", manual, true);
      window.removeEventListener("touchmove", manual, true);
      window.removeEventListener("pointerdown", manual, true);
      window.removeEventListener("keydown", manual, true);
      page.removeEventListener("toggle", schedule, true);
      resize?.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [active, selected, tab, snapshot.snapshot_id, boardFilter]);
  return (
    <div className="noon-workbench page-stack" ref={pageRef}>
      <div className="noon-page-head">
        <div>
          <div className="noon-eyebrow">
            <Sun size={15} />
            手动午间观察
          </div>
          <h2>午间快照</h2>
          {view?.is_demo && (
            <Badge tone="warn">
              样式演示 · 昨日复盘与对照基准为模拟 ·{" "}
              {noonRows(payload?.previous_review?.observations).length}
              只演示关注
            </Badge>
          )}
          <p>上午的市场事实，与上个交易日关注逐项对照。</p>
        </div>
        <div className="noon-head-actions" hidden={tab === "close"}>
          {selected && (
            <AIEntry kind="market" asOf={selected} marketStage="noon" />
          )}
          {view?.today && (
            <button
              className="btn"
              disabled={selected === view.today}
              onClick={() => onDate(view.today)}
            >
              <CalendarDays size={14} />
              今天
            </button>
          )}
          <button
            className="btn"
            disabled={result.loading}
            onClick={result.reload}
            aria-label="重新读取午评保存状态"
          >
            <RefreshCw size={14} />
            读取状态
          </button>
          <button
            className="btn primary"
            disabled={control.disabled || result.loading}
            onClick={capture}
          >
            <Sun size={15} />
            {control.label}
          </button>
        </div>
      </div>
      <section className="noon-reading-status" aria-label="午评日期与状态">
        <div className="noon-scope-strip">
          <span>{selected || "正在读取观察日"}</span>
          <Badge>每天只提交一次</Badge>
          <span>
            {retentionDays
              ? `保留 ${retentionDays} 个自然日 · 到期随盘后增量自动清理`
              : "到期随盘后增量自动清理"}
          </span>
        </div>
        <p className="noon-control-reason">
          {tab === "close"
            ? "收盘回看复用已经发布的同日事实，不重新采集午评。"
            : control.reason}
        </p>
      </section>
      {tab !== "close" && result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {tab !== "close" && submitError && (
        <ErrorBox message={submitError} retry={result.reload} />
      )}
      {tab !== "close" && result.loading && !view && <Loading />}
      <div className="noon-tabs" role="tablist" aria-label="午评内容">
        {[
          ["overview", "午间总览"],
          ["previous", "昨日复盘对照"],
          ["close", "收盘回看"],
        ].map(([key, label]) => (
          <button
            type="button"
            key={key}
            role="tab"
            aria-selected={tab === key}
            aria-controls={
              key === "close" ? "noon-close-content" : "noon-original-content"
            }
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {view?.task && ["queued", "running", "paused"].includes(view.state) && (
        <section className="noon-task" aria-live="polite">
          <div>
            <Badge tone="warn">{noonStateText[view.state]}</Badge>
            <span>
              {view.task.stage || view.task.message || "等待唯一工作进程"}
            </span>
          </div>
          <div className="noon-task-track">
            <i
              style={{
                width: `${Math.max(0, Math.min(100, noonNumber(view.task.progress) || 0))}%`,
              }}
            />
          </div>
          <p>仅刷新已提交任务的本地进度，不自动创建新任务或实时轮询来源。</p>
          {view.state === "paused" && (
            <div className="noon-paused-action">
              <button
                className="btn small"
                disabled={
                  !noonCanResume(view, selected) || resuming || result.loading
                }
                onClick={resumeOriginal}
              >
                {resuming ? "正在继续…" : "继续原任务"}
              </button>
              <span>
                {noonCanResume(view, selected)
                  ? "仅继续同一个任务，不新建采集。"
                  : "原午休窗口已不可用，不能继续或补造午间数据。"}
              </span>
            </div>
          )}
        </section>
      )}
      {view &&
        !payload &&
        tab !== "close" &&
        !["queued", "running", "paused"].includes(view.state) && (
          <Panel>
            <Empty
              title={noonStateText[view.state] || "尚无可展示快照"}
              description={
                view.state === "purged"
                  ? "该日午评正文与专属原件已按当时的保留期限清理。当天名额与清理回执继续保留。"
                  : view.state === "failed" || view.state === "cancelled"
                    ? view.task?.message ||
                      "本日已有采集任务，不能再次新建。没有有效快照的内容保持缺失。"
                    : "午评只在本交易日11:31至13:00前手动采集；未采集的日期不会被盘后或次日数据补造。"
              }
            />
          </Panel>
        )}
      {payload && (
        <div id="noon-original-content" hidden={tab === "close"}>
          <div className="noon-capture-proof">
            <Badge tone={snapshot.quality === "complete" ? "good" : "warn"}>
              {snapshot.quality === "complete" ? "采集完成" : "部分数据可用"}
            </Badge>
            <span>
              实际采集 {noonClock(snapshot.collected_start_at)} —{" "}
              {noonClock(snapshot.collected_end_at)}
            </span>
            <span>
              单条报价时刻
              {payload.quote_as_of == null ? "未提供" : "见来源明细"}
            </span>
          </div>
          <NoonBoardFilter
            value={boardFilter}
            onChange={setBoardFilter}
            disabled={
              pendingDay === selected ||
              ["queued", "running"].includes(view?.state || "")
            }
          />
          <div className="noon-reading-layout">
            <aside className="noon-reading-sidebar">
              <NoonReadGuide
                activeSection={readingSection}
                onNavigate={navigateReading}
              />
            </aside>
            <div className="noon-reading-content">
              <div
                className="noon-indices"
                id="noon-overview-start"
                hidden={tab !== "overview"}
              >
                {INDICES.map(([id, name]) => {
                  const quote =
                    noonRows(payload.indices).find(
                      (row) => row.instrument_id === id,
                    ) || {};
                  const comparison =
                    snapshot.workbench?.index_comparisons?.[id];
                  const amountDelta = noonIndexAmountDelta(comparison);
                  const changePct = noonNumber(comparison?.change_pct);
                  const absoluteYi =
                    amountDelta == null ? null : Math.abs(amountDelta) / 1e8;
                  const deltaText =
                    absoluteYi == null || amountDelta == null
                      ? null
                      : amountDelta === 0
                        ? "持平"
                        : `${amountDelta > 0 ? "放量" : "缩量"}${absoluteYi < 0.005 ? "不足 0.01" : " " + num(absoluteYi)} 亿元`;
                  return (
                    <article key={id} className="noon-index">
                      <span>{name}</span>
                      <strong className={tone(noonNumber(quote.change_pct))}>
                        {num(noonNumber(quote.latest_price))}
                      </strong>
                      <div>
                        <b className={tone(noonNumber(quote.change_pct))}>
                          {percent(noonNumber(quote.change_pct))}
                        </b>
                        <small>累计 {amount(noonNumber(quote.amount))}</small>
                      </div>
                      <div
                        className="noon-index-comparison"
                        title={
                          comparison?.reason ||
                          "本份午评未留存可核验的昨日同期基准"
                        }
                      >
                        <span>
                          {comparison?.state === "unavailable"
                            ? "昨日同期成交额"
                            : comparison?.label || "昨日同期成交额"}
                        </span>
                        {deltaText ? (
                          <b className={tone(amountDelta)}>{deltaText}</b>
                        ) : (
                          <span>暂无可比数据</span>
                        )}
                      </div>
                      {deltaText && (
                        <small className="noon-index-baseline">
                          基准 {amount(comparison?.previous_amount)}
                          {changePct != null && <> · {percent(changePct)}</>}
                        </small>
                      )}
                    </article>
                  );
                })}
              </div>
              <div
                id="noon-panel-overview"
                role="region"
                aria-label="午间总览"
                hidden={tab !== "overview"}
              >
                <div className="noon-main-grid">
                  <div className="noon-column">
                    <Panel
                      title="市场广度"
                      subtitle={
                        breadth.scope || "已核验午间报价样本；缺报不认定停牌"
                      }
                    >
                      <div className="noon-breadth-values">
                        <div>
                          <strong className="up">{integer(up)}</strong>
                          <span>上涨</span>
                        </div>
                        <div>
                          <strong className="down">{integer(down)}</strong>
                          <span>下跌</span>
                        </div>
                        <div>
                          <strong>{integer(flat)}</strong>
                          <span>平盘</span>
                        </div>
                      </div>
                      {breadthTotal != null && breadthTotal > 0 && (
                        <div
                          className="noon-breadth-bar"
                          role="img"
                          aria-label={`上涨${up}只、下跌${down}只、平盘${flat}只`}
                        >
                          <i style={{ flex: up || 0 }} />
                          <i style={{ flex: flat || 0 }} />
                          <i style={{ flex: down || 0 }} />
                        </div>
                      )}
                      <div className="noon-amount">
                        <span>样本累计成交额</span>
                        <strong>
                          {amount(noonNumber(summary.amount?.value))}
                        </strong>
                      </div>
                      <div className="noon-footnote noon-split">
                        <span>
                          有价 {integer(noonNumber(breadth.valid))} / 名录{" "}
                          {integer(noonNumber(breadth.total))}
                        </span>
                        <span>缺报 {integer(noonNumber(breadth.missing))}</span>
                      </div>
                      <p className="noon-footnote">
                        不以指数成交额相加，不推算全天成交额或未取得的昨日午间同比。
                      </p>
                      <div className="noon-footnote noon-split">
                        <span>
                          涨幅≥5%{" "}
                          {integer(
                            noonNumber(breadth.distribution?.above_5_pct),
                          )}{" "}
                          只
                        </span>
                        <span>
                          跌幅≥5%{" "}
                          {integer(
                            noonNumber(breadth.distribution?.below_minus_5_pct),
                          )}{" "}
                          只
                        </span>
                      </div>
                    </Panel>
                    <Panel
                      title="短线情绪"
                      subtitle="午间池状态，不作为收盘晋级或断板确认"
                    >
                      <div className="noon-event-values">
                        {[
                          ["limit_up", "涨停池", "up"],
                          ["limit_down", "跌停池", "down"],
                          ["limit_break", "炸板池", ""],
                        ].map(([key, caption, color]) => (
                          <div key={key}>
                            <strong className={color}>
                              {integer(noonNumber(events[key]?.count))}
                            </strong>
                            <span>{caption}</span>
                            <small>
                              {events[key]?.reason ||
                                (events[key]?.state === "observed" ||
                                events[key]?.state === "ready" ||
                                events[key]?.state === "complete"
                                  ? "来源池范围"
                                  : "覆盖见来源明细")}
                            </small>
                          </div>
                        ))}
                      </div>
                      <div className="noon-footnote">
                        统计接口封板{" "}
                        {integer(
                          noonNumber(
                            events.statistics?.limit_up_stock_count ??
                              events.statistics?.data?.limit_up_stock_count,
                          ),
                        )}{" "}
                        家，与涨停池分别列示；不强行对齐家数。
                      </div>
                      <div className="noon-board-facts">
                        <span>
                          已核验最高连续板数{" "}
                          <b>
                            {integer(
                              noonNumber(
                                (
                                  events.board_summary ||
                                  events.limit_up?.board_summary
                                )?.max_continuous_board,
                              ),
                            )}
                          </b>
                        </span>
                        <span>
                          首板{" "}
                          <b>
                            {integer(
                              noonNumber(
                                (
                                  events.board_summary ||
                                  events.limit_up?.board_summary
                                )?.first_board_count,
                              ),
                            )}
                          </b>
                        </span>
                        <span>
                          连板{" "}
                          <b>
                            {integer(
                              noonNumber(
                                (
                                  events.board_summary ||
                                  events.limit_up?.board_summary
                                )?.continuous_board_count,
                              ),
                            )}
                          </b>
                        </span>
                        <span>
                          板数未知{" "}
                          <b>
                            {integer(
                              noonNumber(
                                (
                                  events.board_summary ||
                                  events.limit_up?.board_summary
                                )?.unknown_count,
                              ),
                            )}
                          </b>
                        </span>
                      </div>
                      <p className="noon-footnote">
                        {(
                          events.board_summary || events.limit_up?.board_summary
                        )?.scope ||
                          "来源板数仅在已核验池样本内统计，不从几天几板文本猜连续高度。"}
                      </p>
                      <PoolClassifications
                        value={snapshot.workbench?.event_classification_counts}
                        boardFilter={boardFilter}
                        onStock={onStock}
                        chartDate={chartDate}
                      />
                      <PoolFacts
                        events={events}
                        boardFilter={boardFilter}
                        classifications={
                          snapshot.workbench?.event_classification_counts
                        }
                        context={quoteContext}
                        onStock={onStock}
                        chartDate={chartDate}
                      />
                    </Panel>
                  </div>
                  <div className="noon-column">
                    <NoonTemperature
                      temperature={snapshot.workbench?.temperature}
                    />
                    <Panel title="午评摘要" actions={<Badge>事实初稿</Badge>}>
                      <div className="noon-summary-copy">
                        <Lines values={analysis.summary} />
                      </div>
                      <Lines values={analysis.observations} />
                      <p className="noon-footnote">
                        规则说明来自本次保存事实，缺少依据不补写判断。
                      </p>
                    </Panel>
                    <Panel
                      title="接着昨天看"
                      actions={
                        <button
                          className="btn small"
                          onClick={() => navigateReading("noon-review-history")}
                        >
                          查看对照
                        </button>
                      }
                    >
                      <PreviousReview previous={previous} compact />
                    </Panel>
                  </div>
                </div>
                <NoonWorkbenchAnalysis
                  key={snapshot.snapshot_id}
                  workbench={snapshot.workbench}
                  snapshotId={snapshot.snapshot_id}
                  stockSuspensions={snapshot.stock_suspensions}
                  boardFilter={boardFilter}
                  onStock={onStock}
                  chartDate={chartDate}
                  onSector={
                    onSector
                      ? (row, view) =>
                          onSector(
                            {
                              ...row,
                              noon_members_snapshot_id: snapshot.snapshot_id,
                            },
                            view,
                          )
                      : undefined
                  }
                />
                <div className="noon-main-grid">
                  <div className="noon-column">
                    <Panel
                      title="成交额前列股票"
                      subtitle="午间已核验股票样本；最新价不写入日线收盘"
                    >
                      <QuoteTable
                        rows={stocks}
                        label="午间成交额前列股票"
                        context={quoteContext}
                        boardFilter={boardFilter}
                        defaultSort="amount"
                        onStock={onStock}
                        chartDate={chartDate}
                      />
                      <p className="noon-footnote">
                        {chartDate
                          ? chartDate === date
                            ? "点击股票查看当日收盘日 K，表格仍显示午间报价。"
                            : `点击股票查看午间临时日 K，正式行情截至 ${chartDate}。`
                          : "该午评之前尚无已发布收盘行情，暂不能打开个股图表。"}
                      </p>
                    </Panel>
                  </div>
                  <div className="noon-column">
                    <Panel title="风险与缺口">
                      <Lines values={analysis.risks} />
                      <Lines values={gaps.slice(0, 6)} />
                      <button className="btn small" onClick={openSourceDetails}>
                        查看来源与时间
                      </button>
                    </Panel>
                    {snapshot.snapshot_id && (
                      <NoonNotes
                        key={snapshot.snapshot_id}
                        snapshotId={snapshot.snapshot_id}
                      />
                    )}
                    <Panel
                      title="小时热榜"
                      subtitle="供应商关注度样本，不等于上涨因果"
                    >
                      {filterRows(
                        noonRows(payload.hot_stocks).map((row) => ({
                          ...row,
                          instrument_id: row.instrument_id || row.thscode,
                        })),
                        boardFilter,
                      ).length ? (
                        <ol className="noon-hot-list">
                          {noonEnrichQuotes(
                            filterRows(
                              noonRows(payload.hot_stocks).map((row) => ({
                                ...row,
                                instrument_id: row.instrument_id || row.thscode,
                              })),
                              boardFilter,
                            ).slice(0, 10),
                            quoteContext,
                          ).map((row, index) => (
                            <li key={row.instrument_id || row.thscode || index}>
                              <button
                                type="button"
                                className="noon-stock-link"
                                disabled={
                                  !onStock ||
                                  !chartDate ||
                                  !/^\d{6}\.(SH|SZ|BJ)$/.test(
                                    row.instrument_id || row.thscode || "",
                                  )
                                }
                                title={
                                  chartDate
                                    ? `查看截至${chartDate}的已发布K线`
                                    : "该午评之前暂无已发布K线"
                                }
                                onClick={() =>
                                  onStock?.(row.instrument_id || row.thscode)
                                }
                              >
                                {row.name ||
                                  row.instrument_id ||
                                  row.thscode ||
                                  "身份待核验"}
                              </button>
                              <NoonBoardTag row={row} />
                              <QuoteEventTags row={row} />
                              {(noonRows(row.industries).length > 0 ||
                                noonRows(row.topics).length > 0) && (
                                <QuoteTags row={row} />
                              )}
                              <small>
                                {row.tag_name ||
                                  row.reason ||
                                  row.limit_up_reason ||
                                  (row.rank != null
                                    ? `来源排名 ${row.rank}`
                                    : "")}
                              </small>
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <p className="noon-unknown">
                          {boardFilter !== "all"
                            ? "该板别暂无展示样本"
                            : "本次未取得小时热榜。"}
                        </p>
                      )}
                    </Panel>
                  </div>
                </div>
              </div>
              <div
                id="noon-panel-previous"
                role="region"
                aria-label="昨日原复盘"
                hidden={tab !== "previous"}
              >
                <section id="noon-review-history" tabIndex={-1}>
                  <PreviousReview
                    previous={previous}
                    context={quoteContext}
                    boardFilter={boardFilter}
                    onStock={onStock}
                    chartDate={chartDate}
                  />
                </section>
              </div>
              <details id="noon-data-gaps" className="noon-source-details">
                <summary>来源说明</summary>
                <div className="noon-tab-stack">
                  <Warning>
                    实际采集区间不等于每条行情时刻。不同域分别采集，不能声称同一秒全市场快照；当前未接入分钟线、连续资金轮动与封单稳定性。
                  </Warning>
                  <Panel
                    title="本次覆盖范围"
                    subtitle="代码枚举、有效报价和精确时点分别核验，不能互相替代"
                  >
                    <div className="noon-coverage-cells">
                      <div>
                        <span>股票返回代码</span>
                        <strong>
                          {integer(noonNumber(summary.coverage?.stock_codes))} /{" "}
                          {integer(
                            noonNumber(summary.coverage?.declared_stock_codes),
                          )}
                        </strong>
                      </div>
                      <div>
                        <span>股票有效最新价</span>
                        <strong>
                          {integer(noonNumber(summary.coverage?.priced_stocks))}
                        </strong>
                      </div>
                      <div>
                        <span>板块返回代码</span>
                        <strong>
                          {integer(
                            noonNumber(summary.coverage?.sector_observed),
                          )}{" "}
                          /{" "}
                          {integer(
                            noonNumber(summary.coverage?.sector_requested),
                          )}
                        </strong>
                      </div>
                      <div>
                        <span>逐条行情时刻</span>
                        <strong>未提供</strong>
                      </div>
                    </div>
                    <p className="noon-footnote">
                      {summary.coverage?.enumeration_complete === true
                        ? "股票代码表分页枚举已完成。"
                        : "股票代码表完整枚举尚未通过。"}
                      {summary.coverage?.sector_complete === true
                        ? "板块目录代码均有返回。"
                        : "板块目录覆盖尚未完整。"}
                      有代码不等于有有效当日报价，也不证明同一秒快照。
                    </p>
                  </Panel>
                  <Panel
                    title="来源、时间与覆盖"
                    subtitle="每个数据域独立核验；请求时间与来源批次时间分别保留"
                  >
                    {!sources.length ? (
                      <p className="noon-unknown">
                        来源明细未提供，请保留本次记录检查。
                      </p>
                    ) : (
                      <div className="noon-table-wrap">
                        <table className="noon-table noon-sources-table">
                          <thead>
                            <tr>
                              <th>数据域 / 来源</th>
                              <th>实际采集</th>
                              <th>批次来源时间</th>
                              <th>状态与缺口</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sources.map((row, index) => (
                              <tr key={row.name || row.domain || index}>
                                <td>
                                  <span>{noonSourceCaption(row)}</span>
                                  <small>{row.source || "扶摇"}</small>
                                </td>
                                <td>
                                  {noonClock(
                                    row.started_at ||
                                      row.requested_at ||
                                      row.collected_start_at,
                                  )}
                                  <small>
                                    至{" "}
                                    {noonClock(
                                      row.finished_at ||
                                        row.received_at ||
                                        row.collected_end_at,
                                    )}
                                  </small>
                                </td>
                                <td>
                                  {noonClock(
                                    row.source_timestamp?.value_ms ??
                                      row.source_timestamp ??
                                      row.timestamp,
                                  )}
                                  <small>逐条行情时间未提供</small>
                                </td>
                                <td>
                                  {row.reason ||
                                    row.message ||
                                    SOURCE_STATES[row.state] ||
                                    "见原件证据"}
                                  {row.rows != null && (
                                    <small>
                                      返回 {integer(noonNumber(row.rows))} 条
                                    </small>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    <details className="noon-details">
                      <summary>快照与来源证据身份</summary>
                      <p>快照 {snapshot.snapshot_id || "—"}</p>
                      <p>正文 SHA256 {snapshot.content_sha256 || "未提供"}</p>
                      {allSources.map((row, index) => (
                        <p key={index}>
                          {noonSourceCaption(row)} ·{" "}
                          {row.sha256 || row.content_sha256 || "来源SHA未提供"}
                        </p>
                      ))}
                    </details>
                  </Panel>
                  <Panel title="本次缺失说明">
                    <Lines
                      values={gaps}
                      empty="本次没有附加缺口说明；仍需遵守逐条时刻未提供等固定边界。"
                    />
                  </Panel>
                  <Panel title="保存与自动清理">
                    <p>
                      每天只允许提交一次。
                      {retentionDays
                        ? `按已核验盘后交易日保留 ${retentionDays} 个自然日，含该交易日。`
                        : "按已核验盘后交易日滚动留存。"}
                      过期午评由盘后增量安全收尾自动清理。
                    </p>
                    <p className="noon-footnote">
                      未运行增量或发布失败时暂留；永久小台账区分“未采集”和“已清理”，不会因删除正文而恢复当天名额。
                    </p>
                  </Panel>
                </div>
              </details>
              <div className="noon-snapshot-footer">
                <CheckCircle2 size={14} />
                <span>
                  午评独立保存 · 历史事实不改写 · 不改变盘后行情、策略与回测口径
                </span>
                <Database size={14} />
              </div>
            </div>
          </div>
        </div>
      )}
      {tab === "close" && (
        <div id="noon-close-content">
          <Suspense fallback={<Loading />}>
            <NoonCloseReview
              date={selected}
              onStock={onCloseStock}
              onSector={onCloseSector}
              noonStart={snapshot.collected_start_at}
              noonEnd={snapshot.collected_end_at}
            />
          </Suspense>
        </div>
      )}
      {!payload && tab !== "close" && recent.length > 0 && (
        <p className="noon-footnote">
          已有记录日期可从顶部盘面观察日期选择，清理后的日期仍保留状态回执。
        </p>
      )}
      {payload && active && tab !== "close" && (
        <FloatingScrollControls
          className="noon-quick-navigation"
          label="午评上下节快捷导航"
          position={{
            text: `${readingIndex + 1}/${NOON_READING_SECTIONS.length}`,
            label: `当前第${readingIndex + 1}节，共${NOON_READING_SECTIONS.length}节`,
          }}
          previous={{
            label: "午评上一节",
            caption: "上节",
            shortcut: "Alt+ArrowUp",
            disabled: readingIndex === 0,
            title:
              readingIndex > 0
                ? `上一节：${NOON_READING_SECTIONS[readingIndex - 1].title}（Alt+↑）`
                : "已经是第一节",
            icon: <ChevronUp size={18} />,
            onClick: () => stepReading(-1),
          }}
          next={{
            label: "午评下一节",
            caption: "下节",
            shortcut: "Alt+ArrowDown",
            disabled: readingIndex === NOON_READING_SECTIONS.length - 1,
            title:
              readingIndex < NOON_READING_SECTIONS.length - 1
                ? `下一节：${NOON_READING_SECTIONS[readingIndex + 1].title}（Alt+↓）`
                : "已经是最后一节",
            icon: <ChevronDown size={18} />,
            onClick: () => stepReading(1),
          }}
        />
      )}
    </div>
  );
}
