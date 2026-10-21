import { useEffect, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { list, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  integer,
  num,
  percent,
  tone,
  textValue,
  label,
} from "../../lib/format";
import {
  Badge,
  Cell,
  Empty,
  ErrorBox,
  Loading,
  Panel,
  Stat,
  Table,
  Warning,
  Warnings,
} from "../../components/UI";
import { CandleChart, EChart } from "../../components/Charts";
import "./Market.css";
import "./MarketOverview.css";
import FundTrend from "./FundTrend";
import MarketDataCoverage from "./MarketDataCoverage";
import EmotionStage from "./EmotionStage";
import EmotionDashboard from "./EmotionDashboard";
import LimitLadder from "./LimitLadder";
import TopicMembersDialog from "./TopicMembersDialog";
import SectorChartDialog from "./SectorChartDialog";
import { sectorChartTarget } from "./sectorChartPresentation";
import type { SectorChartSelection } from "./sectorChartPresentation";
import { ExplainedMetric, MetricHelp } from "./MetricHelp";
import {
  CurrencyCell,
  decorateMarketColumns,
  SortableMarketTable,
} from "./MarketTable";
import {
  marketExplanations,
  amountChangeText,
  marketLabels,
  marketMoney,
  sortMarketRows,
  sectorQuoteSummary,
  industryFundsSummary,
} from "./marketPresentation";
import type { MarketSort } from "./marketPresentation";
import {
  RecentLimitCell,
  YearLimitCountCell,
} from "../topics/MemberHistoryCells";
const indexIds = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH"];
const indexNames = ["上证指数", "深证成指", "创业板指", "科创50"];
export function IndexCards({
  indices,
  selected,
  onSelect,
}: {
  indices: Row[];
  selected?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="index-cards market-overview-indices">
      {indexIds.map((id, i) => {
        const row =
          indices.find(
            (x) =>
              x.instrument_id === id ||
              x.code === id ||
              x.code === id.split(".")[0],
          ) || {};
        return (
          <button
            key={id}
            className={"index-card " + (selected === id ? "selected" : "")}
            onClick={() => onSelect(row.instrument_id || id)}
          >
            <div className="index-card-label">
              <span>{row.name || indexNames[i]}</span>
              <ChevronRight size={14} />
            </div>
            <div className="index-price-line">
              <span className={"index-price " + tone(row.change_pct)}>
                {num(row.close ?? row.latest_price)}
              </span>
              <span className={"index-price-change " + tone(row.change_pct)}>
                {percent(row.change_pct)}
              </span>
            </div>
            <div className="index-bottom">
              <span className="index-current-amount muted">
                成交 {marketMoney(row.amount)}
              </span>
              <span
                className={"index-amount-change " + tone(row.amount_change)}
                title={
                  row.amount_change_reason ||
                  (row.amount_previous_date
                    ? `对比前一市场交易日 ${row.amount_previous_date}，本指数日K成交额的同口径差值；指数之间有重叠，不相加作为市场总额`
                    : "前一市场交易日的指数成交额尚未核验")
                }
              >
                {amountChangeText(row.amount_change)}
              </span>
            </div>
            <div className="index-card-line" />
          </button>
        );
      })}
    </div>
  );
}
function Breadth({ data }: { data: Row }) {
  const breadth = data.breadth || {};
  const total =
    Number(breadth.up || 0) +
    Number(breadth.down || 0) +
    Number(breadth.flat || 0);
  return (
    <Panel
      title={
        <>
          市场广度{" "}
          <MetricHelp
            label="市场广度"
            description={marketExplanations.breadth}
          />
        </>
      }
      subtitle={breadth.scope || "已留存可比 A股 · 当日收盘统计"}
    >
      <div className="breadth-values">
        <span className="up">
          <strong>{integer(breadth.up)}</strong>上涨
        </span>
        <span className="down">
          <strong>{integer(breadth.down)}</strong>下跌
        </span>
        <span className="muted">
          <strong>{integer(breadth.flat)}</strong>平盘
        </span>
      </div>
      <div className="breadth-bar">
        <i
          style={{
            width: total ? (Number(breadth.up || 0) / total) * 100 + "%" : "0%",
          }}
        />
        <i
          style={{
            width: total
              ? (Number(breadth.flat || 0) / total) * 100 + "%"
              : "0%",
          }}
        />
        <i
          style={{
            width: total
              ? (Number(breadth.down || 0) / total) * 100 + "%"
              : "0%",
          }}
        />
      </div>
      <div className="detail-list">
        <p>
          停牌 <b>{integer(breadth.suspended)}</b>
        </p>
        <p>
          待核验 / 缺失 <b>{integer(breadth.missing)}</b>
        </p>
        <p>
          统计标的 <b>{integer(breadth.total ?? total)}</b>
        </p>
      </div>
      <div className="market-reference">
        <span>
          普跌参考 <b>500</b>{" "}
          <MetricHelp
            label="市场广度参考线"
            description={marketExplanations.reference_lines}
          />
        </span>
        <span>
          普涨参考 <b>3,000</b>{" "}
          <MetricHelp
            label="市场广度参考线"
            description={marketExplanations.reference_lines}
          />
        </span>
      </div>
    </Panel>
  );
}
export function MarketOverview({
  date,
  bootstrap,
  onStock,
  indicesOnly = false,
}: {
  date: string;
  bootstrap: Row | null;
  onStock: (id: string) => void;
  indicesOnly?: boolean;
}) {
  const [selected, setSelected] = useState("000001.SH"),
    [sealMode, setSealMode] = useState("funds");
  const result = useApi<Row>(date ? `/market/overview?date=${date}` : null);
  const data = result.data;
  const indices =
    data?.indices ||
    (date === bootstrap?.data_date ? bootstrap?.indices || [] : []);
  const selectedName =
    indices.find((x: Row) => x.instrument_id === selected)?.name ||
    indexNames[indexIds.indexOf(selected)] ||
    selected;
  if (!date || (result.loading && !data))
    return (
      <div className="page-stack">
        <IndexCards
          indices={indices}
          selected={selected}
          onSelect={setSelected}
        />
        <Panel title="正在读取所选日行情">
          <Loading />
        </Panel>
      </div>
    );
  return (
    <div className="page-stack">
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      <MarketDataCoverage
        coverage={data?.data_coverage}
        loading={result.loading}
      />
      <IndexCards
        indices={indices}
        selected={selected}
        onSelect={setSelected}
      />
      {!indicesOnly && (
        <div className="summary-strip">
          <Stat
            label="沪深京总成交额"
            value={marketMoney(data?.total_amount)}
            sub={
              <span
                className={tone(data?.amount_change)}
                title={
                  data?.amount_change_reason ||
                  (data?.amount_previous_date
                    ? `对比前一市场交易日 ${data.amount_previous_date}，${data.amount_scope || "沪深京已留存A股股票成交额"}的同口径差值`
                    : "前一市场交易日的同口径金额尚未核验")
                }
              >
                {amountChangeText(data?.amount_change)}
              </span>
            }
          />
          <Stat
            label="涨停 / 跌停"
            value={
              <>
                <span className="up">{integer(data?.limit_up_count)}</span>
                <span className="stat-separator"> / </span>
                <span className="down">{integer(data?.limit_down_count)}</span>
              </>
            }
            sub="专门事件数据 · 缺失不推算"
          />
          <Stat
            label="炸板家数"
            value={integer(data?.broken_count)}
            sub="日内触板后未封住"
          />
          <Stat
            label="行情覆盖"
            value={integer(bootstrap?.data_summary?.stock_count)}
            sub={
              bootstrap?.data_date
                ? "最近发布 " + bootstrap.data_date
                : "等待首次初始化"
            }
          />
        </div>
      )}
      <div className={indicesOnly ? "" : "market-layout"}>
        <Panel
          title={
            <>
              <span className="live-dot" />
              {selectedName}
            </>
          }
          subtitle={date + " · 独立盘面观察日期"}
          actions={<Badge>真实历史行情</Badge>}
        >
          <CandleChart
            instrumentId={selected}
            asOf={date || undefined}
            height={indicesOnly ? 710 : 625}
          />
        </Panel>
        {!indicesOnly && (
          <div className="side-stack">
            <Breadth data={data || {}} />
            <Panel title="数据边界">
              <div className="data-note">
                <span className="note-dot" />
                <div>
                  <strong>盘后研究工作台</strong>
                  <p>页面显示已发布的历史行情，当前没有实时行情订阅。</p>
                </div>
              </div>
              <div className="data-note">
                <span className="note-dot amber" />
                <div>
                  <strong>先核验，再形成结论</strong>
                  <p>真实筹码、封单时序与全天资金轮动需要额外数据。</p>
                </div>
              </div>
              <Warnings items={data?.warnings} />
            </Panel>
          </div>
        )}
      </div>
      {!indicesOnly && (
        <>
          <div className="two-col">
            <Panel title="成交额 TOP 10 · 个股" subtitle="当日成交最活跃的标的">
              <SortableMarketTable
                sortScope="仅对成交额TOP10已入选样本重排，不重新计算全市场TOP10"
                defaultSort={{ key: "amount", order: "desc" }}
                rows={(data?.top_amount || []).slice(0, 10)}
                columns={[
                  {
                    key: "rank",
                    title: "#",
                    render: (_, r, i) => <span className="rank">{i + 1}</span>,
                  },
                  { key: "name" },
                  { key: "change_pct", align: "right" },
                  { key: "amount", align: "right" },
                  { key: "topics" },
                ]}
                onStock={onStock}
                pageSize={10}
              />
            </Panel>
            <Panel title="当日涨幅前列" subtitle="市场事实，不代表买卖建议">
              <SortableMarketTable
                sortScope="仅对当日涨幅前十已入选样本重排"
                defaultSort={{ key: "change_pct", order: "desc" }}
                rows={(data?.top_gainers || []).slice(0, 10)}
                columns={[
                  {
                    key: "rank",
                    title: "#",
                    render: (_, r, i) => <span className="rank">{i + 1}</span>,
                  },
                  { key: "name" },
                  { key: "change_pct", align: "right" },
                  { key: "close", align: "right" },
                  { key: "topics" },
                ]}
                onStock={onStock}
                pageSize={10}
              />
            </Panel>
          </div>
          <div className="two-col">
            <Panel
              title="分歧最大 · 炸板池前五"
              subtitle="当日炸板池样本 · 按开板次数、成交额排序"
            >
              <SortableMarketTable
                sortScope="仅对已留存炸板池前五样本重排"
                defaultSort={{ key: "open_times", order: "desc" }}
                rows={(data?.divergence_top || data?.divergence || []).slice(
                  0,
                  5,
                )}
                columns={[
                  { key: "name" },
                  { key: "topics" },
                  { key: "open_times", title: "开板次数" },
                  { key: "amount", align: "right" },
                ]}
                onStock={onStock}
                emptyTitle="尚无可核验排名"
                emptyDescription="只有完成事件采样与字段验收后才展示前五，不以日K代替开板过程。"
              />
            </Panel>
            <Panel
              title={
                sealMode === "funds"
                  ? "封单资金前五 · 收盘快照"
                  : "封单最稳 · 前五"
              }
              subtitle={
                sealMode === "funds"
                  ? "按已留存单次封单金额排序，金额大不等于稳定"
                  : "基于连续盘口样本，不按日末封单推断"
              }
              actions={
                <div className="segmented">
                  <button
                    className={sealMode === "funds" ? "active" : ""}
                    onClick={() => setSealMode("funds")}
                  >
                    封单金额
                  </button>
                  <button
                    className={sealMode === "stable" ? "active" : ""}
                    onClick={() => setSealMode("stable")}
                  >
                    稳定性
                  </button>
                </div>
              }
            >
              <SortableMarketTable
                key={sealMode}
                sortScope="仅对当前已入选前五样本重排"
                defaultSort={
                  sealMode === "funds"
                    ? { key: "seal_money", order: "desc" }
                    : { key: "seal_cv", order: "asc" }
                }
                rows={(sealMode === "funds"
                  ? data?.seal_funds_top5 || data?.largest_seals || []
                  : data?.stable_seal_top || data?.seal_stability?.items || []
                ).slice(0, 5)}
                columns={
                  sealMode === "funds"
                    ? [
                        { key: "name" },
                        { key: "topics" },
                        {
                          key: "seal_money",
                          title: "封单金额",
                          render: (value) => (
                            <CurrencyCell value={value} field="seal_money" />
                          ),
                        },
                        { key: "board_count" },
                      ]
                    : [
                        { key: "name" },
                        { key: "topics" },
                        { key: "seal_cv", title: "封单变异系数" },
                        { key: "snapshot_count", title: "样本数" },
                      ]
                }
                onStock={onStock}
                emptyTitle={
                  sealMode === "funds"
                    ? "该日尚无已留存封单快照"
                    : "连续封单时序尚未接入"
                }
                emptyDescription={
                  sealMode === "funds"
                    ? "先同步该交易日涨停池，封单金额采用供应商快照定义。"
                    : data?.seal_stability?.unavailable_reason ||
                      "需要带交易时间戳的逐笔委托或连续涨停买一封单序列；日K、首末封时间或单次五档快照不能计算稳定性。"
                }
              />
              {sealMode === "stable" ? (
                <div className="panel-footnote">
                  接口要求：交易日、股票代码、采样时间、涨停买一价量、开板事件。免费涨停池可补封板时间和单次封单，稳定排名仍需连续采样或历史
                  Level-2 授权数据。
                </div>
              ) : (
                <div className="panel-footnote">
                  来源：
                  {textValue(
                    data?.seal_funds_source || "已留存涨停池封单金额",
                  )}{" "}
                  · {date} · 单次快照不包含日内封单波动。
                </div>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
const pageDetails: Record<
  string,
  { title: string; desc: string; columns: string[] }
> = {
  emotion: {
    title: "市场情绪",
    desc: "从市场赚钱效应与短线情绪两部分观察盘面；只展示可核验事实，缺少数据时保留未知。",
    columns: ["name", "value", "change_pct", "description"],
  },
  sectors: {
    title: "行业与题材强弱",
    desc: "分类体系与成员范围明确，行业和概念不混为同一种排名。",
    columns: [
      "name",
      "change_pct",
      "amount",
      "limit_up_count",
      "observed_count",
      "member_count",
      "calculation",
      "quote_date",
      "membership_observed_date",
    ],
  },
  ladder: {
    title: "涨停梯队",
    desc: "连板、首板与反包、晋级失败、20cm分区完整展开，题材与封板时序同页查看。",
    columns: [
      "name",
      "topics",
      "board_count",
      "first_limit_time",
      "last_limit_time",
      "break_count",
      "amount",
      "turnover_rate",
      "reason",
      "seal_money",
      "core_role",
    ],
  },
  gainers: {
    title: "近 30 日涨幅榜",
    desc: "按已完成交易日计算区间涨幅；缺历史身份和监管材料时单列提示。",
    columns: [
      "name",
      "topics",
      "change_pct",
      "change_30d_pct",
      "last_limit_up_date",
      "limit_up_ytd",
      "board_count",
      "volume_ratio",
      "technical_observation",
      "risk_labels",
    ],
  },
  abnormal: {
    title: "异动与监管",
    desc: "将计算预警、供应商标签和官方监管原文分别标识。",
    columns: [
      "name",
      "event_type",
      "start_date",
      "end_date",
      "reason",
      "source",
    ],
  },
  flow: {
    title: "资金与热点轮动",
    desc: "日级成交额与分类资金流分开；全天时序不能从日末数重建。",
    columns: [
      "name",
      "amount",
      "change_pct",
      "net_inflow",
      "observed_count",
      "calculation",
    ],
  },
};
export function MarketPage({
  route,
  date,
  onStock,
}: {
  route: string;
  date: string;
  onStock: (id: string) => void;
}) {
  const info = pageDetails[route];
  const isSector = route === "sectors" || route === "flow";
  const isGainers = route === "gainers";
  const [filter, setFilter] = useState(""),
    [kind, setKind] = useState(route === "flow" ? "industry" : "concept"),
    [level, setLevel] = useState("all"),
    [rank, setRank] = useState(route === "flow" ? "amount" : "change_pct"),
    [gainerPage, setGainerPage] = useState(1),
    [gainerTopN, setGainerTopN] = useState<number | null>(100),
    [gainerQuery, setGainerQuery] = useState(""),
    [columnSort, setColumnSort] = useState<MarketSort | null>(null),
    [memberTopic, setMemberTopic] = useState<Row | null>(null),
    [sectorChart, setSectorChart] = useState<
      (SectorChartSelection & { scopeDate: string; scopeRoute: string }) | null
    >(null);
  useEffect(() => {
    setMemberTopic(null);
    setSectorChart(null);
  }, [date, route]);
  useEffect(() => {
    if (!isGainers) return;
    const timer = window.setTimeout(() => {
      setGainerQuery(filter.trim());
      setGainerPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [filter, isGainers, date]);
  const gainerSort = columnSort || {
    key: "change_30d_pct",
    order: "desc" as const,
  };
  const result = useApi<Row>(
    date
      ? `/market/${route}?date=${date}${!isGainers ? "&limit=1000" : ""}${isGainers ? "&page=" + gainerPage + "&page_size=25&query=" + encodeURIComponent(gainerQuery) + "&sort_by=" + gainerSort.key + "&sort_order=" + gainerSort.order + (gainerTopN == null ? "" : "&top_n=" + gainerTopN) : ""}`
      : null,
  );
  const data = result.data;
  const sectorResult = useApi<Row>(
    route === "flow" && date ? `/market/sectors?date=${date}&limit=1000` : null,
  );
  const conceptFlow = route === "flow" && kind === "concept";
  const tableData = conceptFlow ? sectorResult.data : data;
  const tableLoading = conceptFlow ? sectorResult.loading : result.loading;
  const fundFlowAvailable =
    route === "flow" && data?.summary?.net_flow_available === true;
  const hasFundFlow = fundFlowAvailable && !conceptFlow;
  useEffect(() => {
    if (fundFlowAvailable)
      setRank((current) => (current === "amount" ? "net_inflow" : current));
  }, [fundFlowAvailable]);
  function selectKind(value: string) {
    setKind(value);
    setLevel("all");
    setColumnSort(null);
    setRank(
      route === "flow" && value === "industry"
        ? fundFlowAvailable
          ? "net_inflow"
          : "amount"
        : "change_pct",
    );
  }
  let rows = list(tableData);
  if (!rows.length && tableData) {
    for (const key of [
      "sectors",
      "concepts",
      "ladder",
      "gainers",
      "events",
      "rankings",
    ])
      if (Array.isArray(tableData[key])) {
        rows = tableData[key];
        break;
      }
  }
  const rowKind = (r: Row) =>
    r.kind === "industry" || r.category === "行业" ? "industry" : "concept";
  const levels = [
    ...new Set(
      rows
        .filter((r) => rowKind(r) === "industry")
        .map((r) => r.classification_level ?? r.level)
        .filter((v) => v != null)
        .map(String),
    ),
  ].sort();
  useEffect(() => {
    setLevel(kind === "industry" && levels.length ? levels[0] : "all");
  }, [kind, levels.join(",")]);
  let filtered = rows.filter(
    (r) =>
      (!isSector || rowKind(r) === kind) &&
      (level === "all" ||
        String(r.classification_level ?? r.level) === level) &&
      (isGainers ||
        !filter ||
        JSON.stringify(r).toLowerCase().includes(filter.toLowerCase())),
  );
  if (isSector) {
    if (rank === "decline")
      filtered = filtered.filter(
        (row) => row.change_pct != null && Number(row.change_pct) < 0,
      );
    else if (rank === "change_pct")
      filtered = filtered.filter(
        (row) => row.change_pct != null && Number(row.change_pct) > 0,
      );
    else if (rank === "net_inflow" || rank === "net_outflow")
      filtered = filtered.filter(
        (row) =>
          typeof row.net_inflow === "number" &&
          (rank === "net_inflow" ? row.net_inflow > 0 : row.net_inflow < 0),
      );
  }
  const sectorSort: MarketSort = {
    key:
      rank === "decline"
        ? "change_pct"
        : rank === "net_outflow"
          ? "net_inflow"
          : rank,
    order: ["decline", "net_outflow"].includes(rank) ? "asc" : "desc",
  };
  const activeSort = isGainers
    ? gainerSort
    : columnSort ||
      (isSector
        ? sectorSort
        : route === "ladder"
          ? { key: "board_count", order: "desc" as const }
          : null);
  if (!isGainers) filtered = sortMarketRows(filtered, activeSort);
  function changeColumnSort(key: string) {
    setColumnSort({
      key,
      order:
        activeSort?.key === key && activeSort.order === "desc" ? "asc" : "desc",
    });
    if (isGainers) setGainerPage(1);
  }
  const sortScope = isGainers
    ? "由服务端对该日全部匹配证券排序后分页"
    : tableData?.total != null && tableData.total <= rows.length
      ? "对当前筛选中的全部已返回记录排序后分页"
      : `只对已加载的 ${rows.length}/${tableData?.total ?? "未知"} 条样本排序，不代表完整来源范围`;
  const risingCount = rows.filter(
    (r) =>
      rowKind(r) === kind &&
      (level === "all" ||
        String(r.classification_level ?? r.level) === level) &&
      typeof r.change_pct === "number" &&
      r.change_pct > 0,
  ).length;
  const fallingCount = rows.filter(
    (r) =>
      rowKind(r) === kind &&
      (level === "all" ||
        String(r.classification_level ?? r.level) === level) &&
      typeof r.change_pct === "number" &&
      r.change_pct < 0,
  ).length;
  const summary = tableData?.summary || {};
  const isQuoteBoard = route === "sectors" || conceptFlow;
  const quoteSummary = sectorQuoteSummary(tableData, kind, level);
  const fundsSummary = industryFundsSummary(data);
  const entries = Object.entries(summary)
    .filter(
      () =>
        !tableData?.unavailable_reason && route !== "emotion" && !isQuoteBoard,
    )
    .filter(
      ([k, v]) =>
        typeof v === "number" ||
        (typeof v === "string" &&
          v.length <= 12 &&
          ![
            "formula",
            "model",
            "observation",
            "rank_basis",
            "scope",
            "window",
          ].includes(k)),
    )
    .slice(0, 6);
  if (route === "ladder")
    return (
      <LimitLadder
        date={date}
        data={data}
        loading={result.loading}
        error={result.error}
        onReload={result.reload}
        onStock={onStock}
      />
    );
  if (route === "emotion")
    return (
      <div className="page-stack">
        <div className="section-intro">
          <p>{info.desc}</p>
          <div className="sentiment-page-controls">
            <Badge>{date || "暂无可用日期"}</Badge>
            <button
              className="icon-btn"
              aria-label="刷新市场情绪"
              onClick={result.reload}
            >
              <RefreshCw size={15} />
            </button>
          </div>
        </div>
        {result.error && (
          <ErrorBox message={result.error} retry={result.reload} />
        )}
        {result.loading && !data && (
          <Panel title="正在核对情绪样本">
            <Loading />
          </Panel>
        )}
        {data?.sentiment ? (
          <EmotionDashboard
            sentiment={data.sentiment}
            coverage={data.data_coverage}
            warnings={data.warnings}
            observation={
              <>
                {data.emotion && <EmotionStage emotion={data.emotion} />}
                {data.breadth && <Breadth data={data} />}
              </>
            }
          />
        ) : data ? (
          <>
            <MarketDataCoverage
              coverage={data.data_coverage}
              loading={result.loading}
            />
            {data.emotion && (
              <EmotionStage emotion={data.emotion} warnings={data.warnings} />
            )}
            {data.breadth && <Breadth data={data} />}
          </>
        ) : (
          !result.loading && (
            <Panel title="市场情绪">
              <Empty
                title="暂无可核验的情绪读数"
                description="选择已留存的盘面日期后，按实际行情和来源证据读取。"
              />
            </Panel>
          )
        )}
        {data?.history?.length > 0 && (
          <Panel
            title="沪深市场广度走势"
            subtitle="已留存沪深可比样本，不含北交所 · 上涨 / 下跌家数 · 参考线 500 / 3,000"
          >
            <EChart
              height={310}
              option={{
                animation: false,
                tooltip: { trigger: "axis", confine: true },
                legend: {
                  data: ["上涨家数", "下跌家数"],
                  textStyle: { color: "#8191a8" },
                  top: 14,
                },
                grid: { left: 62, right: 24, top: 52, bottom: 40 },
                xAxis: {
                  type: "category",
                  data: (data?.history || []).map((r: Row) => r.date),
                  axisLabel: { color: "#8191a8", fontSize: 9 },
                  axisLine: { lineStyle: { color: "#29364a" } },
                },
                yAxis: {
                  type: "value",
                  axisLabel: { color: "#8191a8", fontSize: 10 },
                  splitLine: {
                    lineStyle: { color: "#29364a", type: "dashed" },
                  },
                },
                dataZoom: [{ type: "inside", start: 55, end: 100 }],
                series: [
                  {
                    name: "上涨家数",
                    type: "line",
                    showSymbol: false,
                    data: (data?.history || []).map((r: Row) => r.up),
                    lineStyle: { color: "#ed6865", width: 1.5 },
                    markLine: {
                      symbol: "none",
                      silent: true,
                      data: [{ yAxis: 500 }, { yAxis: 3000 }],
                      label: { fontSize: 9, color: "#8191a8" },
                      lineStyle: { color: "#728095", type: "dashed" },
                    },
                  },
                  {
                    name: "下跌家数",
                    type: "line",
                    showSymbol: false,
                    data: (data?.history || []).map((r: Row) => r.down),
                    lineStyle: { color: "#39bc9b", width: 1.2 },
                  },
                ],
              }}
            />
          </Panel>
        )}
      </div>
    );
  return (
    <div className="page-stack">
      <div className="section-intro">
        <p>{info.desc}</p>
        <Badge>{date || "暂无可用日期"}</Badge>
      </div>
      <MarketDataCoverage
        coverage={tableData?.data_coverage}
        loading={tableLoading}
      />
      {route === "flow" && (
        <div className="market-concept-flow-note" aria-label="行业资金数据状态">
          <strong>
            {fundsSummary.available
              ? "行业净流入数据已留存"
              : "行业净流入数据待核验"}
          </strong>
          <span>
            {date} · 已取得 {integer(fundsSummary.observed)} /{" "}
            {integer(fundsSummary.total)} 项行业资金记录 · 已留存{" "}
            {integer(fundsSummary.days)} 个交易日。
            {conceptFlow
              ? "当前查看概念行情观察，下面的概念涨跌与成交额不代表资金净流入。"
              : "行业资金与板块行情采用各自来源口径，概念净流入尚未接入。"}
          </span>
        </div>
      )}
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {conceptFlow && sectorResult.error && (
        <ErrorBox message={sectorResult.error} retry={sectorResult.reload} />
      )}
      {(route !== "emotion" || !data?.emotion) && (
        <Warnings
          items={tableData?.warnings?.filter(
            (warning: unknown) =>
              warning !== tableData?.data_coverage?.notice &&
              warning !== tableData?.membership_coverage?.notice,
          )}
        />
      )}
      {tableData?.unavailable_reason && (
        <Warning>{tableData.unavailable_reason}</Warning>
      )}
      {isQuoteBoard && (
        <>
          <div className="stats-grid">
            <ExplainedMetric
              label={kind === "industry" ? "当前层级行业目录" : "当前概念目录"}
              value={integer(quoteSummary.catalogCount)}
              description={`当前目录用于定位所选日板块行情，不证明过去交易日的完整目录。${quoteSummary.catalogDate ? `目录采集日 ${quoteSummary.catalogDate}。` : "目录采集日待核验。"}`}
            />
            <ExplainedMetric
              label="所选日已报价"
              value={integer(quoteSummary.quotedCount)}
              description="板块自身日行情与历史成分档案分别判断。来源指数可有真实涨跌和成交额，而成分仍未知；没有数据不补0。"
            />
            <ExplainedMetric
              label="该日历史成分可用"
              value={integer(quoteSummary.membersAvailable)}
              description={quoteSummary.membershipNotice}
            />
          </div>
          <details className="market-members-details market-sector-membership-coverage">
            <summary>历史成分档案范围 · 与板块行情独立</summary>
            <p className="muted">{quoteSummary.membershipNotice}</p>
            <MarketDataCoverage
              coverage={tableData?.membership_coverage}
              loading={tableLoading}
            />
          </details>
        </>
      )}
      {entries.length > 0 && (
        <div className="stats-grid">
          {entries.map(([key, value]) => (
            <ExplainedMetric
              key={key}
              label={marketLabels[key] || label[key] || key}
              value={<Cell field={key} value={value} />}
              description={marketExplanations[key]}
            />
          ))}
        </div>
      )}
      {route !== "emotion" && (summary.formula || summary.rank_basis) && (
        <div className="calculation-note">
          {summary.model ? summary.model + " · " : ""}
          {summary.formula || summary.rank_basis}
        </div>
      )}
      <Panel
        title={
          conceptFlow
            ? "概念行情观察"
            : route === "sectors"
              ? `${kind === "industry" ? "行业" : "概念"}板块日行情`
              : info.title
        }
        subtitle={
          tableData?.source
            ? `来源 ${textValue(tableData.source)}`
            : "按所选盘面日期读取数据"
        }
        actions={
          <>
            <input
              className="search-input compact"
              placeholder={
                isGainers
                  ? gainerTopN == null
                    ? "搜索全市场名称、题材、代码…"
                    : `在近30日榜前${gainerTopN}名中搜索…`
                  : "筛选名称、题材、代码…"
              }
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <button
              className="icon-btn"
              onClick={conceptFlow ? sectorResult.reload : result.reload}
              aria-label="刷新"
            >
              <RefreshCw size={15} />
            </button>
          </>
        }
      >
        {isGainers && (
          <div className="market-gainer-range">
            <span>近30日涨幅范围</span>
            <div className="segmented">
              {[100, 200, 300, 500, 1000].map((count) => (
                <button
                  key={count}
                  className={gainerTopN === count ? "active" : ""}
                  onClick={() => {
                    setGainerTopN(count);
                    setGainerPage(1);
                  }}
                >
                  前{count}
                </button>
              ))}
              <button
                className={gainerTopN == null ? "active" : ""}
                onClick={() => {
                  setGainerTopN(null);
                  setGainerPage(1);
                }}
              >
                全市场
              </button>
            </div>
            <small>
              {gainerTopN == null
                ? "全部匹配证券"
                : `先按近30日涨幅选前${gainerTopN}名，再在该范围内搜索或重排`}{" "}
              · 榜内实际 {integer(summary.rank_scope_total)} 只 · 全部可排名{" "}
              {integer(
                summary.available_ranked_total ?? data?.summary?.covered_stocks,
              )}{" "}
              只
            </small>
          </div>
        )}
        {isSector && (
          <>
            <div className="result-toolbar">
              <div className="segmented">
                <button
                  className={kind === "concept" ? "active" : ""}
                  onClick={() => selectKind("concept")}
                >
                  概念题材
                </button>
                <button
                  className={kind === "industry" ? "active" : ""}
                  onClick={() => selectKind("industry")}
                >
                  行业分类
                </button>
              </div>
              <div className="button-group">
                {kind === "industry" && levels.length > 0 && (
                  <select
                    value={level}
                    onChange={(e) => setLevel(e.target.value)}
                    aria-label="行业层级"
                  >
                    {levels.map((v) => (
                      <option value={v} key={v}>
                        {v} 级
                      </option>
                    ))}
                  </select>
                )}
                <Badge>{filtered.length} 项</Badge>
              </div>
            </div>
            <div className="ranking-controls">
              {[
                ...(hasFundFlow
                  ? [
                      ["net_inflow", "净流入"],
                      ["net_outflow", "净流出"],
                    ]
                  : []),
                ["change_pct", "上涨榜"],
                ["decline", "下跌榜"],
                ...(!hasFundFlow
                  ? [
                      ["amount", "成交额"],
                      ["limit_up_count", "涨停家数"],
                    ]
                  : []),
              ].map(([key, name]) => (
                <button
                  className={rank === key ? "active" : ""}
                  key={key}
                  onClick={() => {
                    setRank(key);
                    setColumnSort(null);
                  }}
                >
                  {name}
                </button>
              ))}
              <span>
                {tableLoading || !tableData ? (
                  "涨跌数量待读取"
                ) : (
                  <>
                    已返回样本：<span className="up">上涨 {risingCount}</span> ·{" "}
                    <span className="down">下跌 {fallingCount}</span> ·
                    未知值不计涨跌
                  </>
                )}
              </span>
            </div>
            {conceptFlow ? (
              <div className="market-concept-flow-note">
                <strong>概念行情可独立观察 · 净流入尚未接入</strong>
                <span>
                  下表允许查看 {date}{" "}
                  已留存概念的真实涨跌与成交额；成交额不代表净流入。没有该日历史成分时，成员字段保持未知。
                </span>
              </div>
            ) : hasFundFlow ? (
              <div className="panel-footnote">
                来源范围：东财资金页当前行业目录，已取得{" "}
                {integer(data?.coverage?.observed_count ?? rows.length)} /{" "}
                {integer(data?.coverage?.selected_count)}{" "}
                项。概念资金尚未按同口径全量接入；目录采集日与历史资金日期分别保留。
              </div>
            ) : (
              kind === "industry" && (
                <div className="panel-footnote">
                  供应商行业目录包含不同层级。
                  {levels.length
                    ? "仅在已选同层级内比较，未知层级不进入当前排行。"
                    : "当前层级尚未核验，下表是目录观察，不能视为一级行业完整排名。"}
                </div>
              )
            )}
          </>
        )}
        {tableLoading ? (
          <Loading />
        ) : (
          <Table
            sorting="external"
            key={
              route +
              date +
              kind +
              level +
              rank +
              filter +
              (activeSort?.key || "source") +
              activeSort?.order
            }
            rows={filtered}
            columns={decorateMarketColumns(
              (conceptFlow
                ? [
                    "name",
                    "change_pct",
                    "amount",
                    "calculation",
                    "quote_date",
                    "member_count",
                    "limit_up_count",
                  ]
                : hasFundFlow
                  ? [
                      "name",
                      "change_pct",
                      "net_inflow",
                      "net_inflow_pct",
                      "calculation",
                    ]
                  : info.columns
              )
                .concat(isSector ? ["sector_chart"] : [])
                .map((key) => ({
                  key,
                  ...(key === "sector_chart"
                    ? {
                        title: "板块走势",
                        sortable: false,
                        render: (_value: unknown, row: Row) => {
                          const target = sectorChartTarget(
                            row,
                            route === "flow" ? list(sectorResult.data) : rows,
                          );
                          return (
                            <button
                              className="btn small market-sector-chart-button"
                              disabled={!target.topicCode}
                              title={target.reason || target.notice}
                              aria-label={`查看${row.name || "分类"}在${date}的板块K线`}
                              onClick={() => {
                                if (target.topicCode) {
                                  setMemberTopic(null);
                                  setSectorChart({
                                    ...target,
                                    topicCode: target.topicCode,
                                    scopeDate: date,
                                    scopeRoute: route,
                                  });
                                }
                              }}
                            >
                              板块K线
                            </button>
                          );
                        },
                      }
                    : {}),
                  ...(key === "name" && isSector
                    ? {
                        render: (_value: unknown, row: Row) => (
                          <div>
                            <button
                              className="market-topic-link"
                              onClick={() => setMemberTopic(row)}
                              disabled={
                                row.members_available === false ||
                                row.membership_match_type === "unavailable"
                              }
                              title={
                                row.membership_unavailable_reason ||
                                row.membership_basis ||
                                "查看所选盘面日留存成员"
                              }
                              aria-label={`查看${row.name || "分类"}在${date}的成分股`}
                            >
                              <strong>
                                {row.name ||
                                  row.topic_id ||
                                  row.topic_code ||
                                  "未知分类"}
                              </strong>
                              <ChevronRight size={13} />
                            </button>
                            {(row.members_available === false ||
                              row.membership_match_type === "unavailable") && (
                              <small className="market-gap-note">
                                该日历史成分未留存
                              </small>
                            )}
                            {row.membership_match_type ===
                              "cross_provider_name_proxy" && (
                              <small className="market-gap-note">
                                本地同名行业 · 同花顺口径
                              </small>
                            )}
                          </div>
                        ),
                      }
                    : {}),
                  ...(key === "change_30d_pct"
                    ? {
                        render: (value: any, row: Row) => (
                          <div
                            title={
                              row.return_30d_reason ||
                              `${row.return_30d_start} → ${row.return_30d_end}，按市场交易日端点计算`
                            }
                          >
                            <span className={tone(value)}>
                              {percent(value)}
                            </span>
                            {row.return_30d_missing_sessions > 0 && (
                              <small className="market-gap-note">
                                区间缺 {row.return_30d_missing_sessions} 日报价
                                · 按端点计算
                              </small>
                            )}
                            {value == null && (
                              <small className="market-gap-note">
                                {row.return_30d_reason || "数据待核验"}
                              </small>
                            )}
                          </div>
                        ),
                      }
                    : {}),
                  ...(key === "net_inflow"
                    ? {
                        render: (value: any, row: Row) => (
                          <CurrencyCell
                            value={value}
                            field="net_inflow"
                            description={
                              row.net_inflow_reason ||
                              "供应商定义的净流入，非机构持仓变动"
                            }
                          />
                        ),
                      }
                    : {}),
                  ...(key === "core_role"
                    ? {
                        title: "人工核心标记",
                        render: (value: any, row: Row) => (
                          <span
                            className="muted"
                            title={
                              row.core_role_reason ||
                              "尚未人工标注龙头或核心，不依据连板高度自动评级"
                            }
                          >
                            {!value || ["待研究", "未评级"].includes(value)
                              ? "未标注"
                              : textValue(value)}
                          </span>
                        ),
                      }
                    : {}),
                  ...(key === "limit_up_ytd"
                    ? {
                        render: (_value: any, row: Row) => (
                          <YearLimitCountCell row={row} />
                        ),
                      }
                    : {}),
                  ...(key === "last_limit_up_date"
                    ? {
                        title: "最近一次涨停",
                        render: (_value: any, row: Row) => (
                          <RecentLimitCell row={row} />
                        ),
                      }
                    : {}),
                })),
              activeSort,
              changeColumnSort,
            )}
            onStock={onStock}
            pageSize={isSector ? 10 : 25}
            emptyTitle={
              tableData?.unavailable_reason
                ? "此数据尚未就绪"
                : isSector && rank === "decline"
                  ? "当前分类没有已确认的下跌记录"
                  : isSector && rank === "change_pct"
                    ? "当前分类没有已确认的上涨记录"
                    : isGainers && gainerQuery
                      ? "全市场未找到匹配记录"
                      : "所选日暂无记录"
            }
            emptyDescription={
              tableData?.unavailable_reason ||
              "数据源未提供或历史快照未留存时，不显示为市场没有发生。"
            }
          />
        )}
        <div className="panel-footnote market-sort-scope">
          {sortScope}；点击表头切换升降序，未知值始终排在末尾。
        </div>
        {isGainers && data && (
          <div className="market-server-pagination">
            <span>
              {gainerTopN == null ? "全市场" : `前${gainerTopN}名内`}匹配{" "}
              {integer(data.total ?? filtered.length)} 只 · 第 {gainerPage} /{" "}
              {Math.max(
                1,
                Number(data.pages) || Math.ceil(Number(data.total || 0) / 25),
              )}{" "}
              页 · 每页 25 只
            </span>
            <div className="button-group">
              <button
                className="btn small"
                disabled={gainerPage <= 1 || result.loading}
                onClick={() => setGainerPage((page) => Math.max(1, page - 1))}
              >
                上一页
              </button>
              <button
                className="btn small"
                disabled={
                  result.loading ||
                  gainerPage >=
                    (Number(data.pages) ||
                      Math.ceil(Number(data.total || filtered.length) / 25))
                }
                onClick={() => setGainerPage((page) => page + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </Panel>
      {memberTopic && (
        <TopicMembersDialog
          key={
            date +
            (memberTopic.topic_id || memberTopic.topic_code || memberTopic.name)
          }
          topic={memberTopic}
          date={date}
          onClose={() => setMemberTopic(null)}
          onStock={onStock}
        />
      )}
      {sectorChart &&
        sectorChart.scopeDate === date &&
        sectorChart.scopeRoute === route && (
          <SectorChartDialog
            key={date + sectorChart.topicCode}
            topic={sectorChart}
            date={date}
            onClose={() => setSectorChart(null)}
          />
        )}
      {route === "flow" && (
        <div className="two-col">
          {data?.history?.length ? (
            <FundTrend data={data} />
          ) : (
            <Panel title="全天资金轮动时序">
              <Empty
                title="缺少连续分类资金时序"
                description="后续需接入带时间戳、分类及口径说明的数据，不能用成交量虚构资金进攻路径。"
              />
            </Panel>
          )}
          <Panel
            title="连板样本反馈"
            subtitle="上一交易日已留存连板样本 · 当日实际涨跌"
          >
            <SortableMarketTable
              sortScope="当前返回的上一交易日已留存连板样本"
              rows={data?.feedback || []}
              columns={[
                { key: "name" },
                { key: "previous_board_count", title: "前日连板" },
                { key: "previous_board_date", title: "样本日" },
                { key: "change_pct" },
                { key: "feedback", title: "当日反馈" },
              ]}
              onStock={onStock}
              pageSize={10}
              emptyTitle="没有留存的可比连板样本"
              emptyDescription="样本反馈只描述已留存标的，不代表全市场赚钱效应或机构资金决策。"
            />
            <div className="panel-footnote">
              不代表全市场赚钱效应；不将成交额解释为机构资金方向。
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
