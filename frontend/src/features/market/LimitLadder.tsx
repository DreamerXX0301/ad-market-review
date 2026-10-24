import { useEffect, useState } from "react";
import {
  ArrowDownUp,
  ArrowUpRight,
  ChevronDown,
  Layers3,
  RefreshCw,
  Users,
  X,
} from "lucide-react";
import type { Row } from "../../lib/api";
import { integer, percent, tone } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Warning,
  Warnings,
} from "../../components/UI";
import MarketDataCoverage from "./MarketDataCoverage";
import TopicMembersDialog from "./TopicMembersDialog";
import DailyMainlines from "./DailyMainlines";
import { CurrencyCell } from "./MarketTable";
import CopyStockCode from "../../components/CopyStockCode";
import { sortMarketRows } from "./marketPresentation";
import type { MarketSort } from "./marketPresentation";
import {
  filterLadderRows,
  groupLadderRows,
  ladderClassificationCount,
  ladderPatternTags,
  ladderTopics,
  themeCountText,
} from "./ladderPresentation";
import type { LadderTopic } from "./ladderPresentation";
import {
  buildLadderDisplayGroups,
  ladderCardAttribution,
  ladderStockKey,
  ladderThemeIndex,
} from "./ladderBoardPresentation";
import "./LimitLadder.css";

const rowsAt = (value: unknown): Row[] => (Array.isArray(value) ? value : []);
const positiveCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value > 0;
const heightClass = (height: number | null) =>
  height == null
    ? "unknown"
    : height >= 4
      ? "high"
      : height === 3
        ? "middle"
        : height === 2
          ? "second"
          : "first";
const sortChoices = [
  ["first_limit_time", "首次封板"],
  ["last_limit_time", "最后封板"],
  ["break_count", "开板次数"],
  ["board_count", "连板数"],
  ["previous_board_count", "昨日连板"],
  ["change_pct", "当日涨幅"],
  ["turnover_rate", "换手率"],
  ["amount", "成交额"],
  ["seal_money", "封单金额"],
  ["name", "股票名称"],
  ["code", "股票代码"],
];

function LadderStockCard({
  row,
  themeIndex,
  onStock,
  onTopic,
  selectedTopic,
  failed = false,
  showHeight = false,
  displayTopic,
  observationDate,
  snapshotId,
}: {
  row: Row;
  themeIndex: Map<string, Row>;
  onStock: (id: string) => void;
  onTopic: (topic: LadderTopic) => void;
  selectedTopic: LadderTopic | null;
  failed?: boolean;
  showHeight?: boolean;
  displayTopic?: LadderTopic;
  observationDate: string;
  snapshotId: unknown;
}) {
  const patterns = ladderPatternTags(row, observationDate, snapshotId);
  const attribution = ladderCardAttribution(row, displayTopic);
  const primaryConcept = attribution.conceptTopics[0];
  const otherTopics = [
    ...attribution.conceptTopics.slice(1),
    ...attribution.otherTopics,
  ];
  const sourceIndustryIsTopic = attribution.industryTopics.some(
    (topic) => topic.name === attribution.sourceIndustry,
  );
  const topicChip = (topic: LadderTopic) => {
    const theme = themeIndex.get(topic.key);
    return (
      <button
        key={topic.key}
        className={`ladder-stock-theme ${selectedTopic?.key === topic.key ? "selected" : ""}`}
        onClick={() => onTopic(topic)}
        title={
          theme?.reason ||
          "已留存当日分类关联，不代表涨停驱动；点击筛选本页样本"
        }
      >
        <span>{topic.name}</span>
        {theme && <small>{themeCountText(theme)}</small>}
      </button>
    );
  };
  const board = failed ? row.previous_board_count : row.board_count;
  const breakCount =
    typeof row.break_count === "number" &&
    Number.isInteger(row.break_count) &&
    row.break_count >= 0
      ? `${row.break_count}次`
      : "—";
  return (
    <article className="ladder-stock-card">
      <div className="ladder-stock-primary">
        <div className="ladder-stock-identity-wrap">
          <button
            className="ladder-stock-identity"
            onClick={() => onStock(row.instrument_id)}
            disabled={!row.instrument_id}
          >
            <strong>
              {row.name || row.code || row.instrument_id || "名称待核验"}
            </strong>
          </button>
          {patterns.map((pattern) => (
            <span
              key={pattern.kind}
              className={`ladder-pattern-tag ${pattern.kind}`}
              title={pattern.detail}
            >
              {pattern.label}
            </span>
          ))}
          <CopyStockCode code={row.code} instrumentId={row.instrument_id} />
        </div>
        <div className="ladder-stock-status">
          {showHeight && positiveCount(board) && (
            <span className="ladder-stock-height">
              {failed ? "昨" : ""}
              {board}板
            </span>
          )}
          {row.twenty_cm_state === true && (
            <span className="ladder-twenty-tag">20cm</span>
          )}
          {row.core_role_verified === true &&
            typeof row.core_role === "string" &&
            row.core_role && (
              <span className="ladder-core-role">{row.core_role}</span>
            )}
          <span className={`ladder-stock-change ${tone(row.change_pct)}`}>
            {percent(row.change_pct)}
          </span>
        </div>
      </div>
      <div className="ladder-stock-topics">
        <div className="ladder-attribution-row">
          <span className="ladder-attribution-label">行业</span>
          <div className="ladder-attribution-values">
            {attribution.sourceIndustry && !sourceIndustryIsTopic && (
              <span className="ladder-source-industry">
                {attribution.sourceIndustry}
              </span>
            )}
            {attribution.industryTopics.map(topicChip)}
            {!attribution.sourceIndustry &&
              !attribution.industryTopics.length && (
                <span className="ladder-no-topic">待核验</span>
              )}
          </div>
        </div>
        <div className="ladder-attribution-row">
          <span className="ladder-attribution-label">概念关联</span>
          <div className="ladder-attribution-values">
            {primaryConcept ? (
              topicChip(primaryConcept)
            ) : (
              <span className="ladder-no-topic">未留存明确概念</span>
            )}
            {otherTopics.length > 0 && (
              <details className="ladder-more-topics">
                <summary>
                  其余关联 {otherTopics.length}
                  <ChevronDown size={10} />
                </summary>
                <div>{otherTopics.map(topicChip)}</div>
              </details>
            )}
          </div>
        </div>
        <div
          className="ladder-attribution-row"
          title="来源记录的原因说明，不代表已验证的涨停驱动"
        >
          <span className="ladder-attribution-label">来源原因</span>
          <span className="ladder-source-reason">
            {attribution.sourceReason || "待核验"}
          </span>
        </div>
      </div>
      <dl className="ladder-stock-timing">
        {failed ? (
          <>
            <div>
              <dt>昨日高度</dt>
              <dd>
                {positiveCount(row.previous_board_count)
                  ? `${row.previous_board_count}板`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>昨日末封</dt>
              <dd>{row.previous_limit_time || "—"}</dd>
            </div>
            <div>
              <dt>来源日</dt>
              <dd>{row.previous_board_date || "—"}</dd>
            </div>
          </>
        ) : (
          <>
            <div>
              <dt>首封</dt>
              <dd>{row.first_limit_time || "—"}</dd>
            </div>
            <div>
              <dt>末封</dt>
              <dd>{row.last_limit_time || "—"}</dd>
            </div>
            <div>
              <dt>开板</dt>
              <dd
                className={
                  positiveCount(row.break_count) ? "ladder-break-active" : ""
                }
              >
                {breakCount}
              </dd>
            </div>
          </>
        )}
      </dl>
      <div className="ladder-stock-secondary">
        <span title={row.turnover_rate_reason || "来源当日换手率；缺失不补0"}>
          换手 <b>{percent(row.turnover_rate)}</b>
        </span>
        <span>
          成交 <CurrencyCell value={row.amount} field="amount" />
        </span>
        {!failed && (
          <span>
            封单 <CurrencyCell value={row.seal_money} field="seal_money" />
          </span>
        )}
      </div>
    </article>
  );
}

function ClassificationSection({
  id,
  title,
  description,
  accent,
  rows,
  allRows,
  coverage,
  onStock,
  onTopic,
  selectedTopic,
  searching,
  failed = false,
  themeIndex,
  sort,
  observationDate,
  snapshotId,
}: {
  id: string;
  title: string;
  description: string;
  accent: string;
  rows: Row[];
  allRows: Row[];
  coverage?: Row;
  onStock: (id: string) => void;
  onTopic: (topic: LadderTopic) => void;
  selectedTopic: LadderTopic | null;
  searching: boolean;
  failed?: boolean;
  themeIndex: Map<string, Row>;
  sort: MarketSort;
  observationDate: string;
  snapshotId: unknown;
}) {
  const count = ladderClassificationCount(allRows, coverage);
  return (
    <section className={`ladder-classification ${accent}`} id={id}>
      <header className="ladder-section-heading">
        <div>
          <h2>
            <span className="ladder-section-dot" />
            {title}
          </h2>
          <p>{description}</p>
        </div>
        <span className="ladder-section-count">
          {searching ? `${rows.length} / ${integer(count)}` : integer(count)}
          <small>{count == null ? " 数量待核验" : " 只已确认"}</small>
        </span>
      </header>
      {rows.length ? (
        <div className="ladder-classification-grid">
          {sortMarketRows(rows, sort).map((row, index) => (
            <LadderStockCard
              key={row.instrument_id || index}
              row={row}
              themeIndex={themeIndex}
              onStock={onStock}
              onTopic={onTopic}
              selectedTopic={selectedTopic}
              failed={failed}
              showHeight
              observationDate={observationDate}
              snapshotId={snapshotId}
            />
          ))}
        </div>
      ) : (
        <Empty
          title={
            searching && allRows.length
              ? "当前筛选暂无匹配股票"
              : "暂无可核验样本"
          }
          description={
            coverage?.reason || "分类证据不足时保留未知，不代表市场没有发生。"
          }
        />
      )}
      {coverage && (
        <details className="ladder-classification-status">
          <summary>
            分类范围与缺口
            <ChevronDown size={12} />
            {positiveCount(coverage.unknown_count) && (
              <span>{integer(coverage.unknown_count)}只待核验</span>
            )}
          </summary>
          <p>{coverage.reason || "仅对已留存、符合本定义的事件样本分类"}</p>
        </details>
      )}
    </section>
  );
}

export default function LimitLadder({
  date,
  data,
  loading,
  error,
  onReload,
  onStock,
}: {
  date: string;
  data: Row | null;
  loading: boolean;
  error: string;
  onReload: () => void;
  onStock: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedTopic, setSelectedTopic] = useState<LadderTopic | null>(null);
  const [memberTopic, setMemberTopic] = useState<Row | null>(null);
  const [view, setView] = useState<"height" | "theme" | "mainlines">("height");
  const [rawTopicsOpen, setRawTopicsOpen] = useState(false);
  const [sort, setSort] = useState<MarketSort>({
    key: "first_limit_time",
    order: "asc",
  });
  useEffect(() => {
    setQuery("");
    setSelectedTopic(null);
    setMemberTopic(null);
  }, [date]);
  const main = rowsAt(data?.items),
    rebound = rowsAt(data?.rebound_items),
    failed = rowsAt(data?.failed_items),
    twenty = rowsAt(data?.twenty_cm_items),
    themes = rowsAt(data?.theme_counts);
  const themeIndex = ladderThemeIndex(themes);
  const filtered = filterLadderRows(main, query, selectedTopic);
  const groups = groupLadderRows(filtered);
  if (!groups.some((group) => group.height === 1) && !query && !selectedTopic)
    groups.splice(
      groups.findIndex((group) => group.height == null) < 0
        ? groups.length
        : groups.findIndex((group) => group.height == null),
      0,
      { height: 1, items: [] },
    );
  const displayPlan = buildLadderDisplayGroups(main, themes);
  const filteredSet = new Set(filtered);
  const themeGroups = displayPlan.groups
    .map((group) => ({
      ...group,
      items: group.items.filter((row) => filteredSet.has(row)),
    }))
    .filter((group) => group.items.length);
  const coverage = data?.classification_coverage || {};
  const highest = main
    .map((row) => row.board_count)
    .filter(positiveCount)
    .sort((a, b) => b - a)[0];
  const searching = !!query.trim() || !!selectedTopic;
  const onTopic = (topic: LadderTopic) =>
    setSelectedTopic((current) => (current?.key === topic.key ? null : topic));
  const baseId = "limit-ladder-" + date;
  const sections = [
    {
      key: "rebound",
      title: "反包板 · 事件候选",
      description: "T涨停 / T-1确认非涨停 / T-2涨停，非K线实体包络判断",
      rows: rebound,
      accent: "violet",
    },
    {
      key: "failed",
      title: "晋级失败",
      description: "昨日来源涨停、今日确认未收盘涨停，不等于跌停",
      rows: failed,
      accent: "cool",
      failed: true,
    },
    {
      key: "twenty_cm",
      title: "20cm · 制度已核验",
      description: "来源收盘涨停与当日20%制度联合核验",
      rows: twenty,
      accent: "amber",
    },
  ];
  const stockCards = (items: Row[], wide = false, showHeight = false) => (
    <div className={`ladder-stock-grid ${wide ? "is-wide" : ""}`}>
      {sortMarketRows(items, sort).map((row, index) => (
        <LadderStockCard
          key={row.instrument_id || index}
          row={row}
          themeIndex={themeIndex}
          onStock={onStock}
          onTopic={onTopic}
          selectedTopic={selectedTopic}
          showHeight={showHeight}
          displayTopic={displayPlan.topicsByInstrument.get(ladderStockKey(row))}
          observationDate={date}
          snapshotId={data?.snapshot_id}
        />
      ))}
    </div>
  );
  return (
    <div className="page-stack limit-ladder">
      <div className="ladder-intro">
        <div className="ladder-compact-caption">
          <Layers3 size={14} />
          <strong>连板高度与归属分布</strong>
          <span>全部样本展开</span>
        </div>
        <div className="button-group">
          <Badge>{date || "日期待核验"}</Badge>
          <button
            className="btn small"
            onClick={onReload}
            aria-label="刷新涨停梯队"
          >
            <RefreshCw size={13} />
            刷新
          </button>
        </div>
      </div>
      <details className="ladder-coverage-details">
        <summary>
          <span>{date || "日期待核验"}</span>
          <strong>
            {loading
              ? "正在读取样本"
              : `已确认 ${main.length ? integer(main.length) : "—"} 只`}
          </strong>
          <span className="ladder-coverage-state">
            {loading
              ? "核验中"
              : data?.data_coverage?.selected_date_available === false
                ? "所选日不可用"
                : data?.data_coverage?.partial === true
                  ? "部分覆盖"
                  : data?.data_coverage?.partial === false
                    ? "按来源口径"
                    : "覆盖待核验"}
          </span>
          <span className="ladder-coverage-more">
            覆盖与来源
            <ChevronDown size={12} />
          </span>
        </summary>
        <MarketDataCoverage coverage={data?.data_coverage} loading={loading} />
      </details>
      {error && <ErrorBox message={error} retry={onReload} />}
      {data?.unavailable_reason && <Warning>{data.unavailable_reason}</Warning>}
      {loading ? (
        <Loading />
      ) : (
        <>
          {view !== "mainlines" && (
            <div className="ladder-overview">
              <div>
                <span>已确认涨停样本</span>
                <strong>
                  {main.length ? integer(main.length) : "—"}
                  <small>只</small>
                </strong>
              </div>
              <div>
                <span>最高连板</span>
                <strong>
                  {highest ? integer(highest) : "—"}
                  <small>板</small>
                </strong>
              </div>
              <div>
                <span>展示分组</span>
                <strong>
                  {displayPlan.groups.length
                    ? integer(displayPlan.groups.length)
                    : "—"}
                  <small>组</small>
                </strong>
              </div>
              <p>
                同一盘面日 · 同一来源口径
                <br />
                展示分组不代表涨停主因，原始关联按需查看。
              </p>
            </div>
          )}
          <div className="ladder-controls">
            <div className="ladder-view-switch" aria-label="切换涨停看板视图">
              {(
                [
                  ["height", "连板梯队"],
                  ["theme", "概念关联"],
                  ["mainlines", "当日主线"],
                ] as const
              ).map(([key, text]) => (
                <button
                  key={key}
                  aria-pressed={view === key}
                  className={view === key ? "active" : ""}
                  onClick={() => {
                    setView(key);
                    if (key === "mainlines") setSelectedTopic(null);
                  }}
                >
                  {text}
                </button>
              ))}
            </div>
            <label className="ladder-search">
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={
                  view === "mainlines"
                    ? "搜索股票、代码或主线…"
                    : "搜索股票、代码或题材…"
                }
                aria-label="搜索涨停梯队股票或题材"
              />
            </label>
            {view !== "mainlines" && (
              <div className="ladder-sort-controls">
                <label>
                  组内排序
                  <select
                    aria-label="涨停看板组内排序字段"
                    value={sort.key}
                    onChange={(event) =>
                      setSort({
                        key: event.target.value,
                        order: [
                          "first_limit_time",
                          "last_limit_time",
                          "name",
                          "code",
                        ].includes(event.target.value)
                          ? "asc"
                          : "desc",
                      })
                    }
                  >
                    {sortChoices.map(([key, caption]) => (
                      <option key={key} value={key}>
                        {caption}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="btn small"
                  onClick={() =>
                    setSort({
                      ...sort,
                      order: sort.order === "asc" ? "desc" : "asc",
                    })
                  }
                  aria-label={`切换为${sort.order === "asc" ? "降序" : "升序"}`}
                >
                  <ArrowDownUp size={12} />
                  {sort.order === "asc" ? "升序" : "降序"}
                </button>
              </div>
            )}
          </div>
          {searching && view !== "mainlines" && (
            <div className="ladder-filter-state">
              <span>
                {selectedTopic ? `题材：${selectedTopic.name}` : ""}
                {query.trim() ? ` 搜索：${query.trim()}` : ""} · 匹配{" "}
                {filtered.length}/{main.length}只
              </span>
              <button
                onClick={() => {
                  setQuery("");
                  setSelectedTopic(null);
                }}
              >
                <X size={12} />
                清除筛选
              </button>
            </div>
          )}
          <div className="ladder-board-layout" id={baseId + "-main"}>
            {view === "height" && (
              <section className="ladder-main">
                <header className="ladder-main-heading">
                  <div>
                    <h2>连板梯队</h2>
                    <p>高板逐只突出 · 二板与首板紧凑排列</p>
                  </div>
                  <span>
                    {filtered.length}/{main.length}只已返回
                  </span>
                </header>
                {groups.length ? (
                  <div className="ladder-height-groups">
                    {groups.map((group) => (
                      <section
                        className={`ladder-board-group ${heightClass(group.height)}`}
                        key={group.height ?? "unknown"}
                      >
                        <header className="ladder-height-rail">
                          <strong>
                            {group.height == null
                              ? "板数未知"
                              : group.height === 1
                                ? "首板"
                                : `${group.height}连板`}
                          </strong>
                          <span>
                            {group.items.length
                              ? `${group.items.length}只`
                              : "样本待核验"}
                          </span>
                        </header>
                        {group.items.length ? (
                          stockCards(
                            group.items,
                            group.height != null && group.height >= 3,
                          )
                        ) : (
                          <div className="ladder-empty-group">
                            首板记录缺失或尚无确认样本，未填成0。
                          </div>
                        )}
                      </section>
                    ))}
                  </div>
                ) : (
                  <Empty
                    title={
                      searching
                        ? "当前筛选没有匹配股票"
                        : "所选日暂无可核验梯队"
                    }
                    description={
                      data?.main_coverage?.reason || "来源未留存时保持未知。"
                    }
                  />
                )}
              </section>
            )}
            {view === "theme" && (
              <section className="ladder-theme-board">
                <header className="ladder-main-heading">
                  <div>
                    <h2>概念关联分组</h2>
                    <p>每只股票仅出现一次 · 最多6组与其他题材</p>
                  </div>
                  <span>本组家数与全部关联家数分开</span>
                </header>
                <p className="ladder-display-notice">
                  按已留存概念成员作浏览分组，不代表涨停主因；不修改题材归属。其余关联见股票卡片或下方数据详情。
                </p>
                {themeGroups.length ? (
                  <div className="ladder-theme-groups">
                    {themeGroups.map((group) => (
                      <section className="ladder-theme-group" key={group.key}>
                        <header className="ladder-theme-rail">
                          {group.topic ? (
                            <button
                              onClick={() => onTopic(group.topic!)}
                              aria-pressed={
                                selectedTopic?.key === group.topic.key
                              }
                            >
                              {group.topic.name}
                            </button>
                          ) : (
                            <h3 className="ladder-other-title">其他题材</h3>
                          )}
                          <strong>本组 {group.items.length} 只</strong>
                          {group.countLabel && (
                            <span title={group.theme?.reason}>
                              关联涨停 {group.countLabel}
                            </span>
                          )}
                          {group.theme &&
                            (group.theme.topic_id ||
                              group.theme.member_lookup) && (
                              <button
                                className="ladder-member-button"
                                onClick={() => setMemberTopic(group.theme)}
                              >
                                <Users size={11} />
                                成分股
                              </button>
                            )}
                        </header>
                        {stockCards(group.items, false, true)}
                      </section>
                    ))}
                  </div>
                ) : (
                  <Empty
                    title="当前范围暂无概念关联分组"
                    description={
                      data?.theme_coverage?.notice ||
                      data?.theme_coverage?.reason ||
                      "只有所选日已留存的有效成员归属才可分组。"
                    }
                  />
                )}
              </section>
            )}
            {view === "mainlines" && (
              <DailyMainlines
                key={date}
                date={date}
                rows={main}
                query={query}
                onStock={onStock}
                snapshotId={data?.snapshot_id}
              />
            )}
          </div>
          <div
            className="ladder-supplemental-sections"
            aria-label="补充事件分类"
          >
            {sections.map((section) => (
              <ClassificationSection
                key={section.key}
                id={baseId + "-" + section.key}
                title={section.title}
                description={section.description}
                accent={section.accent}
                rows={filterLadderRows(section.rows, query, selectedTopic)}
                allRows={section.rows}
                coverage={coverage[section.key]}
                onStock={onStock}
                onTopic={onTopic}
                selectedTopic={selectedTopic}
                searching={searching}
                failed={section.failed}
                themeIndex={themeIndex}
                sort={sort}
                observationDate={date}
                snapshotId={data?.snapshot_id}
              />
            ))}
          </div>
          <details className="ladder-evidence">
            <summary>
              数据与归属详情
              <ChevronDown size={13} />
              {positiveCount(data?.main_coverage?.unknown_count) && (
                <span>
                  {integer(data?.main_coverage?.unknown_count)}
                  只来源候选未通过核验
                </span>
              )}
            </summary>
            <div>
              <p>
                连板高度不等同于龙头，当日主线中的角色仅为用户候选标记，不改变来源评级。封板时间不等于全天封单稳定性；缺失字段以“—”显示，不补0。
              </p>
              <p>
                题材家数来自该盘面日已留存成员，多个分类可重叠。确认下界不当作完整数量；卡片没有家数时仅表示归属，不推算题材总数。
              </p>
              {data &&
                typeof data.total === "number" &&
                data.total > main.length && (
                  <p>
                    当前接口只返回{main.length}/{data.total}
                    只，未返回股票不视为不存在。
                  </p>
                )}
              <p>{data?.main_coverage?.reason}</p>
              <p>
                {data?.theme_coverage?.notice || data?.theme_coverage?.reason}
              </p>
              <details
                className="ladder-theme-shortcuts"
                onToggle={(event) => setRawTopicsOpen(event.currentTarget.open)}
              >
                <summary>
                  全部原始关联与筛选 <span>{themes.length}组已返回</span>
                  <ChevronDown size={12} />
                </summary>
                <div className="ladder-theme-grid">
                  {rawTopicsOpen && (
                    <>
                      {themes.length ? (
                        themes.map((theme, index) => {
                          const topic = ladderTopics({ topics: [theme] })[0];
                          return (
                            <div
                              key={topic?.key || index}
                              className={`ladder-theme-card ${topic?.key === selectedTopic?.key ? "active" : ""}`}
                            >
                              <button
                                className="ladder-theme-select"
                                onClick={() => topic && onTopic(topic)}
                                aria-pressed={topic?.key === selectedTopic?.key}
                                title={
                                  theme.reason ||
                                  "当日可核验题材成员中的涨停家数"
                                }
                              >
                                <span>{theme.name}</span>
                                <strong>{themeCountText(theme)}</strong>
                              </button>
                              <div>
                                <span>
                                  首封 {theme.first_limit_time || "—"} · 末封{" "}
                                  {theme.last_limit_time || "—"}
                                  {theme.first_limit_time_complete === false ||
                                  theme.last_limit_time_complete === false
                                    ? " · 部分时间"
                                    : ""}
                                </span>
                                {(theme.topic_id || theme.member_lookup) && (
                                  <button
                                    onClick={() => setMemberTopic(theme)}
                                    aria-label={`查看${theme.name}的当日成员`}
                                  >
                                    <Users size={12} />
                                    <ArrowUpRight size={11} />
                                  </button>
                                )}
                              </div>
                            </div>
                          );
                        })
                      ) : (
                        <p className="ladder-empty-themes">
                          {data?.theme_coverage?.notice ||
                            data?.theme_coverage?.reason ||
                            "该盘面日未留存可关联题材，不用当前成员回填。"}
                        </p>
                      )}
                    </>
                  )}
                </div>
              </details>

              <Warnings items={data?.warnings} />
            </div>
          </details>
        </>
      )}
      {memberTopic && (
        <TopicMembersDialog
          topic={memberTopic}
          date={date}
          onClose={() => setMemberTopic(null)}
          onStock={onStock}
        />
      )}
    </div>
  );
}
