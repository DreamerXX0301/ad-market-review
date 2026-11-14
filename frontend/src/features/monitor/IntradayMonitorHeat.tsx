import { useMemo, useState } from "react";
import type { Row } from "../../lib/api";
import { Badge, Table, Warning } from "../../components/UI";
import { num, percent, tone } from "../../lib/format";
import { buildMonitorHeat } from "./monitorHeat";
import type { HeatGroup, HeatPeriod } from "./monitorHeat";
import "./IntradayMonitorHeat.css";

const periods: Record<HeatPeriod, string> = {
  intraday: "盘中",
  "30d": "近30交易日",
  ytd: "年内",
};

export default function IntradayMonitorHeat({
  stocks,
  events,
  topicDate,
  tradeDate,
  chartAsOf,
  eventsLoaded,
  eventsTotal,
  onTopic,
  onStock,
}: {
  stocks: Row[];
  events: Row[];
  topicDate?: string;
  tradeDate?: string;
  chartAsOf?: string;
  eventsLoaded?: number;
  eventsTotal?: number;
  onTopic: (group: HeatGroup) => void;
  onStock: (id: string, orderedRows: Row[]) => void;
}) {
  const [period, setPeriod] = useState<HeatPeriod>(() => {
    const requested = new URLSearchParams(
      typeof window === "undefined" ? "" : window.location?.search || "",
    ).get("monitor_period");
    return requested === "30d" || requested === "ytd" ? requested : "intraday";
  });
  const [kind, setKind] = useState<"industry" | "concept">("concept");
  const [minSamples, setMinSamples] = useState<1 | 3 | 5>(3);
  const result = useMemo(
    () =>
      buildMonitorHeat(stocks, events, {
        topicDate,
        tradeDate,
        period,
        kind,
        minSamples,
      }),
    [stocks, events, topicDate, tradeDate, period, kind, minSamples],
  );
  const partialAlerts = result.alert_scope === "loaded_events";
  const noQuotes = result.valid_stock_count === 0;
  return (
    <section className="intraday-monitor-heat" aria-label="监控池内热点">
      <div className="intraday-monitor-heat-controls">
        <label>
          观察周期
          <select
            aria-label="池内热点周期"
            value={period}
            onChange={(event) => setPeriod(event.target.value as HeatPeriod)}
          >
            <option value="intraday">盘中</option>
            <option value="30d">近30交易日（已发布）</option>
            <option value="ytd">年内（已发布）</option>
          </select>
        </label>
        <label>
          分组
          <select
            aria-label="池内热点分类"
            value={kind}
            onChange={(event) =>
              setKind(event.target.value as "industry" | "concept")
            }
          >
            <option value="concept">概念 / 人工题材</option>
            <option value="industry">行业</option>
          </select>
        </label>
        <label>
          最少有效样本
          <select
            aria-label="池内热点最少有效样本"
            value={minSamples}
            onChange={(event) =>
              setMinSamples(Number(event.target.value) as 1 | 3 | 5)
            }
          >
            <option value={1}>1只</option>
            <option value={3}>3只</option>
            <option value={5}>5只</option>
          </select>
        </label>
      </div>
      <div className="intraday-monitor-heat-context">
        <strong>
          当前筛选 {num(result.stock_count, 0)} 只 · {periods[period]}有效涨幅{" "}
          {num(result.valid_stock_count, 0)} 只
        </strong>
        <span>
          分类归属日 {topicDate || "待核验"} ·{" "}
          {num(result.unclassified_count, 0)} 只没有可用的当前分类
        </span>
        <span>
          达到最少有效样本的分组 {result.groups.length} /{" "}
          {result.allGroups.length}；默认按涨幅中位数、上涨比例及有效样本排序。
        </span>
      </div>
      {noQuotes && (
        <Warning>
          {period === "intraday"
            ? "当前筛选股票尚无可用的盘中涨幅，池内强弱保持未知。可切换“近30交易日”或“年内”查看已发布行情。"
            : "当前筛选股票的已发布周期指标尚未准备或覆盖不足，池内强弱保持未知。"}
        </Warning>
      )}
      <div className="intraday-monitor-compact-table intraday-monitor-heat-table">
        <Table
          rows={result.groups}
          rowKey="topic_id"
          pageSize={15}
          columns={[
            {
              key: "name",
              title: kind === "industry" ? "池内行业" : "池内概念 / 题材",
              render: (_, row) => (
                <button
                  type="button"
                  className="stock-link intraday-monitor-heat-topic"
                  aria-label={`查看${row.name}监控股票`}
                  onClick={() => onTopic(row as HeatGroup)}
                >
                  <strong>{row.name}</strong>
                  <small>查看当前匹配股票</small>
                </button>
              ),
            },
            {
              key: "stock_count",
              title: "池内股票数",
              align: "right",
              render: (value) => num(value, 0),
            },
            {
              key: "valid_count",
              title: "有效覆盖",
              align: "right",
              render: (_, row) => (
                <div className="intraday-monitor-heat-coverage">
                  <strong>
                    {num(row.valid_count, 0)} / {num(row.stock_count, 0)}
                  </strong>
                  <small>{num(row.unknown_count, 0)} 只涨幅未知</small>
                </div>
              ),
            },
            {
              key: "up_count",
              title: "上涨家数",
              align: "right",
              render: (value) => num(value, 0),
            },
            {
              key: "up_ratio_pct",
              title: "上涨比例",
              align: "right",
              render: (value) => percent(value, 1),
            },
            {
              key: "strong_count",
              title: "≥3%家数",
              align: "right",
              render: (value) => (
                <span title="所选周期涨幅至少3%的股票家数，非涨停统计">
                  {num(value, 0)}
                </span>
              ),
            },
            {
              key: "median_pct",
              title: "涨幅中位数",
              align: "right",
              render: (_, row) => (
                <div className="intraday-monitor-heat-median">
                  <strong className={tone(row.median_pct)}>
                    {percent(row.median_pct)}
                  </strong>
                  <small>均值 {percent(row.mean_pct)}</small>
                </div>
              ),
            },
            {
              key: "alert_stock_count",
              title: "今日提醒股票数",
              align: "right",
              render: (value) => (
                <span>
                  {partialAlerts ? "≥ " : ""}
                  {num(value, 0)}
                </span>
              ),
            },
            {
              key: "leaders",
              title: "池内领涨股",
              sortable: false,
              render: (_, row) => (
                <div className="intraday-monitor-heat-leaders">
                  {row.leaders.length ? (
                    row.leaders.map((stock: Row) => (
                      <button
                        type="button"
                        className="stock-link"
                        key={stock.instrument_id}
                        aria-label={`查看${stock.name || stock.code}K线`}
                        disabled={!chartAsOf}
                        onClick={() => onStock(stock.instrument_id, row.stocks)}
                      >
                        <span>{stock.name || stock.code}</span>
                        <b className={tone(stock.heat_change_pct)}>
                          {percent(stock.heat_change_pct)}
                        </b>
                      </button>
                    ))
                  ) : (
                    <span className="muted">涨幅待核验</span>
                  )}
                </div>
              ),
            },
          ]}
          emptyTitle={
            noQuotes
              ? "暂无可排名的有效样本"
              : result.allGroups.length === 0
                ? "当前筛选没有可用的分类分组"
                : `暂无达到${minSamples}只有效样本的分组`
          }
          emptyDescription={
            noQuotes
              ? period === "intraday"
                ? "缺少盘中采样时不填零，也不使用昨日涨幅代替。"
                : "已发布周期缺少有效指标时保持未知，不用零代替。"
              : "可以调整当前页筛选、行业/概念或最少有效样本。"
          }
        />
      </div>
      <div className="intraday-monitor-heat-notes">
        <p>
          <Badge>{periods[period]}</Badge>{" "}
          上涨比例仅以本组有效涨幅样本为分母；未知样本单列覆盖。“≥3%家数”表示所选周期涨幅达到3%，不是涨停家数。
        </p>
        <p>
          {partialAlerts
            ? `今日提醒股票数仅覆盖已加载提醒范围（${num(eventsLoaded ?? events.length, 0)} / ${num(eventsTotal, 0)}条），可能低于全部持久提醒数量。`
            : "今日提醒股票数按各股票的全部已保存当日提醒计数，同股多条提醒只计一只。"}
        </p>
        <p>
          这里只比较当前监控池和当前筛选的样本强弱，不是全市场板块指数涨幅或资金流。股票可属于多个概念，各组数量不可相加为全市场总量。点击分组查看对应监控股票，领涨股打开截至{" "}
          {chartAsOf || "待核验"} 的已发布日K。
        </p>
      </div>
    </section>
  );
}
