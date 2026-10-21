import { useEffect, useRef, useState } from "react";
import {
  ChevronRight,
  FlaskConical,
  History,
  Play,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { api, list, post, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  amount,
  dateTime,
  integer,
  num,
  percent,
  textValue,
  tone,
} from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  inferColumns,
  Loading,
  Modal,
  Panel,
  Spinner,
  Stat,
  Status,
  Table,
  useToast,
  Warning,
  Warnings,
} from "../../components/UI";
import { EquityChart } from "../../components/Charts";
import { TradeRuleSummary } from "../strategies/TradeRules";
import { summarizeChecks } from "../strategies/Conditions";
const defaultConfig = {
  initial_capital: 100000,
  max_positions: 5,
  position_pct: 20,
  hold_days: 10,
  stop_loss_pct: 8,
  take_profit_pct: 20,
  commission_bps: 3,
  min_commission: 5,
  sell_tax_bps: 5,
  slippage_bps: 5,
};
function warningTexts(value: any): string[] {
  return [
    ...new Set(
      (Array.isArray(value) ? value : value ? [value] : [])
        .map((item: any) =>
          typeof item === "string"
            ? item
            : item.message || item.reason || JSON.stringify(item),
        )
        .filter(Boolean),
    ),
  ] as string[];
}
function BoundaryDetails({
  warnings,
  quality,
  summary,
  config = false,
}: {
  warnings: any;
  quality?: Row;
  summary: string;
  config?: boolean;
}) {
  const texts = warningTexts(warnings);
  return (
    <details
      className={
        "quality-details compact-boundary " +
        (config ? "config-boundary" : "report-boundary")
      }
    >
      <summary>
        <span className="boundary-summary">{summary}</span>
        <span className="boundary-more">
          {config ? "查看区间与研究边界" : "本次数据范围与研究限制"}
        </span>
      </summary>
      <div className="boundary-body">
        {texts.length > 0 && (
          <ul>
            {texts.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
        )}
        {quality && (
          <details className="technical-boundary">
            <summary>完整样本与验收字段</summary>
            <pre>{JSON.stringify(quality, null, 2)}</pre>
          </details>
        )}
      </div>
    </details>
  );
}
export default function Backtests({
  onStock,
  initialStrategyId,
}: {
  onStock: (id: string) => void;
  initialStrategyId?: string;
}) {
  const strategies = useApi<Row>("/strategies"),
    history = useApi<Row>("/backtests");
  const [strategyId, setStrategyId] = useState(initialStrategyId || ""),
    [availability, setAvailability] = useState<Row | null>(null),
    [checking, setChecking] = useState(false),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [config, setConfig] = useState<Row>(defaultConfig),
    [advanced, setAdvanced] = useState(false),
    [selectedId, setSelectedId] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [tab, setTab] = useState("trades"),
    [orderFilter, setOrderFilter] = useState("all"),
    [trade, setTrade] = useState<Row | null>(null);
  const strategyVersion = list(strategies.data).find(
    (s) => s.strategy_id === strategyId,
  )?.version;
  const report = useApi<Row>(selectedId ? `/backtests/${selectedId}` : null);
  const toast = useToast(),
    checkId = useRef(0);
  useEffect(() => {
    if (initialStrategyId) setStrategyId(initialStrategyId);
  }, [initialStrategyId]);
  useEffect(() => {
    if (!strategyId && list(strategies.data)[0])
      setStrategyId(list(strategies.data)[0].strategy_id);
  }, [strategies.data]);
  useEffect(() => {
    if (!selectedId && list(history.data)[0])
      setSelectedId(list(history.data)[0].backtest_id);
  }, [history.data]);
  useEffect(() => {
    if (!strategyId) return;
    const seq = ++checkId.current;
    setChecking(true);
    setAvailability(null);
    post("/backtests/availability", { strategy_id: strategyId })
      .then((r) => {
        if (seq !== checkId.current) return;
        setAvailability(r);
        setStart(r.default_start || r.min_date || "");
        setEnd(r.default_end || r.max_date || "");
      })
      .catch((e) => {
        if (seq === checkId.current) setError(e.message);
      })
      .finally(() => {
        if (seq === checkId.current) setChecking(false);
      });
  }, [strategyId, strategyVersion]);
  const r = report.data;
  useEffect(() => {
    if (!r || !["queued", "running", "pending"].includes(r.status)) return;
    const timer = setTimeout(report.reload, 1500);
    return () => clearTimeout(timer);
  }, [r, report.reload]);
  useEffect(() => {
    if (r?.status === "completed") history.reload();
  }, [r?.status, selectedId]);
  async function run() {
    setError("");
    if (!start || !end || start > end) {
      setError("请选择有效的回测起止日期");
      return;
    }
    if (
      [start, end].some((d) =>
        [0, 6].includes(new Date(d + "T12:00:00").getDay()),
      )
    ) {
      setError("起止日期必须是实际交易日，周末无法提交");
      return;
    }
    setBusy(true);
    try {
      const res = await post("/backtests", {
        strategy_id: strategyId,
        start_date: start,
        end_date: end,
        ...config,
        request_key: crypto.randomUUID(),
      });
      setSelectedId(res.backtest_id);
      history.reload();
      report.reload();
      toast("已提交回测，后台正在计算真实行情");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const stats = r?.stats || {};
  const equity = r?.equity || r?.curve || [];
  const finalPositions = (r?.positions || []).filter(
    (p: Row) => !p.date || p.date === (r?.end_date || r?.config?.end_date),
  );
  const orders = (r?.orders || []).filter(
    (o: Row) => orderFilter === "all" || o.status === orderFilter,
  );
  return (
    <div className="page-stack">
      <div className="section-intro">
        <p>
          独立模拟账户 · 收盘生成信号，次日开盘尝试成交 ·
          真实可测区间由数据依赖决定
        </p>
        <Badge tone="neutral">研究回测 · 无实盘委托</Badge>
      </div>
      <Panel
        title="回测配置"
        subtitle="冻结策略、行情版本、交易参数与费用口径"
        actions={
          <button className="btn small" onClick={() => setAdvanced((v) => !v)}>
            <Settings2 size={13} />
            {advanced ? "收起参数" : "交易与费用参数"}
          </button>
        }
      >
        <div className="backtest-form">
          <label className="strategy-choice">
            选择策略
            <select
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            >
              <option value="">请选择已有策略</option>
              {list(strategies.data).map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.name} · V{s.version} · {s.period === "W" ? "周线" : "日线"}
                </option>
              ))}
            </select>
          </label>
          <label>
            起始交易日
            <input
              type="date"
              min={availability?.min_date}
              max={availability?.max_date}
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <span className="date-range-separator">—</span>
          <label>
            结束交易日
            <input
              type="date"
              min={availability?.min_date}
              max={availability?.max_date}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button
            className="btn primary"
            disabled={
              !strategyId ||
              busy ||
              checking ||
              availability?.available === false
            }
            onClick={run}
          >
            {busy ? <Spinner /> : <Play size={15} />}运行回测
          </button>
        </div>
        {checking ? (
          <div className="inline-hint">
            <Spinner />
            核对策略依赖与预热区间…
          </div>
        ) : (
          availability && (
            <div
              className={
                "availability " +
                (availability.available === false ? "blocked" : "")
              }
            >
              <Badge tone={availability.available === false ? "warn" : "good"}>
                {availability.available === false
                  ? "当前不可运行"
                  : "区间可核验"}
              </Badge>
              <span>
                {availability.available === false
                  ? availability.reason
                  : `可用范围 ${availability.min_date || "—"} → ${availability.max_date || "—"}`}
              </span>
              {availability.min_sessions && (
                <small>至少 {availability.min_sessions} 个交易日</small>
              )}
            </div>
          )
        )}
        {warningTexts(availability?.warnings).length > 0 && (
          <BoundaryDetails
            config
            warnings={availability?.warnings}
            summary={`受限研究样本 · 未复权撮合 · 排除区间分红送转${availability?.warmup_bars ? " · " + availability.warmup_bars + "期预热" : ""}`}
          />
        )}
        <TradeRuleSummary
          config={config}
          value={
            list(strategies.data).find(
              (item) => item.strategy_id === strategyId,
            )?.trade_rules
          }
        />
        <div className="panel-footnote">
          买入、卖出条件在策略管理中编辑，并随策略版本冻结；止盈、止损与持有期限在“交易与费用参数”中设置。
        </div>
        {advanced && (
          <div className="parameter-grid">
            {[
              ["initial_capital", "初始资金 · 元"],
              ["max_positions", "最多持仓数"],
              ["position_pct", "单股目标仓位 · %"],
              ["hold_days", "持有交易日"],
              ["stop_loss_pct", "收盘止损阈值 · %"],
              ["take_profit_pct", "收盘止盈阈值 · %"],
              ["commission_bps", "佣金 · 基点"],
              ["min_commission", "最低佣金 · 元"],
              ["sell_tax_bps", "卖出税费 · 基点"],
              ["slippage_bps", "滑点 · 基点"],
            ].map(([key, caption]) => (
              <label key={key}>
                {caption}
                <input
                  type="number"
                  min={key === "initial_capital" ? 1000 : 0}
                  step={key === "position_pct" ? 1 : "any"}
                  value={config[key]}
                  onChange={(e) =>
                    setConfig((v) => ({ ...v, [key]: Number(e.target.value) }))
                  }
                />
              </label>
            ))}
            <label>
              收盘跌破退出均线
              <select
                value={config.exit_ma || 0}
                onChange={(event) =>
                  setConfig((value) => ({
                    ...value,
                    exit_ma: Number(event.target.value),
                  }))
                }
              >
                <option value={0}>不使用</option>
                {[5, 10, 20, 30, 60, 250].map((n) => (
                  <option key={n} value={n}>
                    MA{n}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        <div className="panel-footnote">
          期末不强平，未平仓按收盘估值；胜率与盈亏比仅使用已闭合交易。费用参数为本次模拟配置，不构成现行收费制度承诺。
        </div>
      </Panel>
      {error && <ErrorBox message={error} />}
      <div className="backtest-workspace">
        <div className="page-stack">
          {report.error && (
            <ErrorBox message={report.error} retry={report.reload} />
          )}{" "}
          {!selectedId ? (
            <Panel title="回测报告">
              <Empty
                title="还没有回测结果"
                description="选择策略和有覆盖的区间，运行后查看收益、风险与逐笔交易。"
              />
            </Panel>
          ) : report.loading && !r ? (
            <Loading />
          ) : (
            r && (
              <>
                {r.status !== "completed" ? (
                  <Panel title="回测任务" actions={<Status value={r.status} />}>
                    <div className="task-status">
                      <FlaskConical size={28} />
                      <div>
                        <h3>
                          {r.status === "failed"
                            ? "回测未完成"
                            : "正在核对与运行回测"}
                        </h3>
                        <p>
                          {r.error ||
                            r.message ||
                            "任务在本地后台执行，可稍后从回测历史打开结果。"}
                        </p>
                      </div>
                      {["queued", "running"].includes(r.status) && <Spinner />}
                    </div>
                    <Warnings items={r.warnings} />
                  </Panel>
                ) : (
                  <>
                    <div className="report-heading">
                      <div>
                        <h2>
                          {r.strategy_name ||
                            list(strategies.data).find(
                              (s) => s.strategy_id === r.strategy_id,
                            )?.name ||
                            "回测报告"}
                        </h2>
                        <p>
                          {r.start_date || r.config?.start_date} —{" "}
                          {r.end_date || r.config?.end_date} <span>·</span>{" "}
                          {r.backtest_id?.slice(0, 10)}
                        </p>
                      </div>
                      <Badge tone="good">已完成</Badge>
                    </div>
                    <TradeRuleSummary
                      value={r.trade_rules}
                      config={r.config}
                      compact
                    />
                    <div className="metrics-seven">
                      {[
                        ["total_return", "总收益"],
                        ["annual_return", "年化收益"],
                        ["max_drawdown", "最大回撤"],
                        ["win_rate", "胜率"],
                        ["payoff_ratio", "盈亏比"],
                        ["trade_count", "交易数"],
                        ["final_equity", "最终权益"],
                      ].map(([key, caption]) => (
                        <Stat
                          key={key}
                          label={caption}
                          value={
                            <span
                              className={
                                ["total_return", "annual_return"].includes(key)
                                  ? tone(stats[key])
                                  : key === "max_drawdown"
                                    ? "down"
                                    : ""
                              }
                            >
                              {[
                                "total_return",
                                "annual_return",
                                "max_drawdown",
                                "win_rate",
                              ].includes(key)
                                ? key === "max_drawdown" || key === "win_rate"
                                  ? stats[key] == null
                                    ? "—"
                                    : num(stats[key]) + "%"
                                  : percent(stats[key])
                                : key === "final_equity"
                                  ? num(stats[key])
                                  : key === "trade_count"
                                    ? integer(stats[key])
                                    : num(stats[key])}
                            </span>
                          }
                          sub={
                            key === "payoff_ratio" && stats[key] == null
                              ? "无足够盈亏样本"
                              : undefined
                          }
                        />
                      ))}
                    </div>
                    {(r.data_quality ||
                      warningTexts(r.warnings).length > 0) && (
                      <BoundaryDetails
                        warnings={r.warnings}
                        quality={r.data_quality}
                        summary={`受限研究样本${r.data_quality?.eligible_symbols != null && r.data_quality?.total_symbols != null ? " " + integer(r.data_quality.eligible_symbols) + " / " + integer(r.data_quality.total_symbols) : ""} · 未复权撮合，已排除区间分红送转 · 收盘信号次日撮合`}
                      />
                    )}
                    <Panel
                      title="账户权益与回撤"
                      subtitle={`初始权益 ${num(r.config?.initial_capital ?? config.initial_capital)} 元 · 累计费用 ${num(stats.total_fees)} 元`}
                    >
                      <EquityChart rows={equity} />
                    </Panel>
                    <Panel
                      title="交易与账户明细"
                      actions={
                        <Badge>{(r.trades || []).length} 笔闭合交易</Badge>
                      }
                    >
                      <div className="result-toolbar">
                        <div className="tabs">
                          {[
                            ["trades", "闭合交易"],
                            ["orders", "委托记录"],
                            ["fills", "成交记录"],
                            ["positions", "期末持仓"],
                          ].map(([key, name]) => (
                            <button
                              key={key}
                              className={tab === key ? "active" : ""}
                              onClick={() => setTab(key)}
                            >
                              {name}{" "}
                              <span>
                                {key === "positions"
                                  ? finalPositions.length
                                  : (r[key] || []).length}
                              </span>
                            </button>
                          ))}
                        </div>
                        {tab === "orders" && (
                          <select
                            value={orderFilter}
                            onChange={(e) => setOrderFilter(e.target.value)}
                            aria-label="委托状态"
                          >
                            <option value="all">全部状态</option>
                            {[
                              ...new Set(
                                (r.orders || []).map((x: Row) => x.status),
                              ),
                            ].map((x: any) => (
                              <option key={x} value={x}>
                                {(
                                  {
                                    filled: "已成交",
                                    rejected: "已拒绝",
                                    unexecuted: "未执行",
                                    not_filled: "未成交",
                                    pending: "待执行",
                                  } as Row
                                )[x] || x}
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                      {tab === "trades" ? (
                        <Table
                          rows={r.trades || []}
                          columns={[
                            { key: "name" },
                            { key: "entry_date" },
                            { key: "exit_date" },
                            { key: "entry_price" },
                            { key: "exit_price" },
                            { key: "quantity" },
                            { key: "net_pnl" },
                            { key: "return_pct" },
                            { key: "exit_reason" },
                            {
                              key: "detail",
                              title: "详情",
                              render: (_, row) => (
                                <button
                                  className="link"
                                  onClick={() => setTrade(row)}
                                >
                                  逐笔依据 <ChevronRight size={12} />
                                </button>
                              ),
                            },
                          ]}
                          onStock={onStock}
                          emptyTitle="暂无闭合交易"
                          emptyDescription="期末未平仓仍计入权益，但不进入胜率与盈亏比。"
                        />
                      ) : tab === "orders" ? (
                        <Table
                          rows={orders}
                          columns={inferColumns(orders, [
                            "date",
                            "name",
                            "side",
                            "status",
                            "quantity",
                            "filled_quantity",
                            "price",
                            "reason",
                          ])}
                          onStock={onStock}
                          emptyTitle="暂无委托记录"
                        />
                      ) : tab === "fills" ? (
                        <Table
                          rows={r.fills || []}
                          columns={inferColumns(r.fills || [], [
                            "date",
                            "name",
                            "side",
                            "quantity",
                            "price",
                            "fee",
                          ])}
                          onStock={onStock}
                          emptyTitle="暂无实际模拟成交"
                        />
                      ) : (
                        <Table
                          rows={finalPositions}
                          columns={inferColumns(finalPositions, [
                            "name",
                            "quantity",
                            "entry_date",
                            "entry_price",
                            "close",
                            "market_value",
                            "unrealized_pnl",
                          ])}
                          onStock={onStock}
                          emptyTitle="期末无持仓"
                        />
                      )}
                    </Panel>
                  </>
                )}
              </>
            )
          )}
        </div>
        <Panel
          title="回测历史"
          subtitle="后端留存 · 参数与数据版本冻结"
          actions={
            <button
              className="icon-btn"
              title="刷新历史"
              onClick={history.reload}
            >
              <RefreshCw size={13} />
            </button>
          }
          className="backtest-history"
        >
          {history.loading ? (
            <Loading />
          ) : list(history.data).length ? (
            <div className="run-history">
              {list(history.data).map((x) => (
                <button
                  key={x.backtest_id}
                  className={selectedId === x.backtest_id ? "active" : ""}
                  onClick={() => {
                    setSelectedId(x.backtest_id);
                    setTab("trades");
                  }}
                >
                  <div>
                    <strong>
                      {x.strategy_name ||
                        list(strategies.data).find(
                          (s) => s.strategy_id === x.strategy_id,
                        )?.name ||
                        "策略回测"}
                    </strong>
                    <Status value={x.status} />
                  </div>
                  <p>
                    {x.start_date} → {x.end_date}
                  </p>
                  <footer>
                    <span>{dateTime(x.created_at ?? x.created_at_ms)}</span>
                    <span
                      className={tone(x.stats?.total_return ?? x.total_return)}
                    >
                      {percent(x.stats?.total_return ?? x.total_return)}
                    </span>
                  </footer>
                </button>
              ))}
            </div>
          ) : (
            <Empty title="暂无历史" description="运行结果将在这里留存。" />
          )}
        </Panel>
      </div>
      {trade && (
        <Modal
          title={`逐笔交易 · ${trade.name || trade.code}`}
          wide
          onClose={() => setTrade(null)}
        >
          <div className="modal-body">
            <div className="stats-grid">
              <Stat label="买入日" value={trade.entry_date} />
              <Stat label="卖出日" value={trade.exit_date} />
              <Stat
                label="净盈亏"
                value={
                  <span className={tone(trade.net_pnl)}>
                    {num(trade.net_pnl)}
                  </span>
                }
              />
              <Stat label="收益率" value={percent(trade.return_pct)} />
            </div>
            <div className="detail-list">
              {Object.entries(trade)
                .filter(
                  ([key]) => key !== "exit_checks" && key !== "entry_checks",
                )
                .map(([key, value]) => (
                  <p key={key}>
                    <span>
                      {(
                        {
                          entry_price: "买入价格",
                          exit_price: "卖出价格",
                          quantity: "成交数量",
                          exit_reason: "离场原因",
                          total_fees: "交易费用",
                          instrument_id: "标的代码",
                          entry_date: "买入日",
                          exit_date: "卖出日",
                        } as Row
                      )[key] || key}
                    </span>
                    <b>{textValue(value)}</b>
                  </p>
                ))}
            </div>
            {trade.entry_checks && (
              <Panel title="买入触发依据" subtitle="信号日收盘的冻结条件值">
                <p className="prose">{summarizeChecks(trade.entry_checks)}</p>
              </Panel>
            )}
            {trade.exit_checks && (
              <Panel title="卖出触发依据" subtitle="信号日收盘的冻结条件值">
                <p className="prose">{summarizeChecks(trade.exit_checks)}</p>
              </Panel>
            )}
            <Warning>
              信号日与成交日分开；不能用当日收盘生成信号后回填当日开盘成交。
            </Warning>
          </div>
        </Modal>
      )}
    </div>
  );
}
