import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  Clock3,
  Edit3,
  FlaskConical,
  Play,
  Plus,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { api, list, patch, post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, integer, percent, textValue } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Panel,
  Spinner,
  Status,
  Table,
  useToast,
  Warning,
} from "../../components/UI";
import { fallbackFields, fieldLabel, summarizeChecks } from "./Conditions";
import RuleTreeEditor from "./RuleTreeEditor";
import RuleEvidence from "./RuleEvidence";
import {
  historyRuleSummary,
  normalizeRuleTree,
  ruleCount,
  validateVisualRule,
} from "./historicalRules";
import StrategyAutomation from "./StrategyAutomation";
import UniverseEditor, { splitUniverse, withUniverse } from "./Universe";
import PolicyScope from "./PolicyScope";
import TradeRulesEditor, {
  emptyTradeRules,
  TradeRuleSummary,
} from "./TradeRules";
import type { TradeRulesValue } from "./TradeRules";
import { createStockNavigation } from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import SectorStrategies from "../sector/SectorStrategies";
import { sectorPeriodLabel } from "../sector/sectorPresentation";
import "../sector/SectorWorkspace.css";
type StrategiesProps = {
  onStock: (id: string, navigation?: StockNavigation) => void;
  onNavigate: (route: string, context?: Row) => void;
  onRefresh?: () => void;
};
export default function Strategies(props: StrategiesProps) {
  const [target, setTarget] = useState("stock");
  return (
    <div className="page-stack">
      <div className="sector-target-tabs" role="tablist" aria-label="策略目标">
        <button
          role="tab"
          aria-selected={target === "stock"}
          className={target === "stock" ? "active" : ""}
          onClick={() => setTarget("stock")}
        >
          个股策略
        </button>
        <button
          role="tab"
          aria-selected={target === "sector"}
          className={target === "sector" ? "active" : ""}
          onClick={() => setTarget("sector")}
        >
          行业 / 概念策略
        </button>
      </div>
      {target === "stock" ? (
        <StockStrategies {...props} />
      ) : (
        <SectorStrategies onNavigate={props.onNavigate} />
      )}
    </div>
  );
}
function StrategyEditor({
  strategy,
  fields,
  topics,
  onClose,
  onSaved,
}: {
  strategy?: Row;
  fields: Row[];
  topics: Row[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const initial = splitUniverse(strategy?.definition);
  const [universe, setUniverse] = useState(initial.universe);
  const [tradeRules, setTradeRules] = useState<TradeRulesValue>(
    strategy?.trade_rules || emptyTradeRules(),
  );
  const [name, setName] = useState(strategy?.name || ""),
    [period, setPeriod] = useState(strategy?.period || "D"),
    [definitionTree, setDefinitionTree] = useState<Row>(
      normalizeRuleTree(
        strategy
          ? initial.definition
          : {
              all: [
                { field: "volume_ratio", op: ">=", value: 3 },
                { field: "close", op: ">", right_field: "ma30" },
              ],
            },
      ),
    ),
    [advanced, setAdvanced] = useState(false),
    [json, setJson] = useState(
      JSON.stringify(initial.definition || {}, null, 2),
    ),
    [busy, setBusy] = useState(false),
    [duration, setDuration] = useState(
      strategy?.policy?.duration_sessions || 30,
    ),
    [applyActive, setApplyActive] = useState(true),
    [error, setError] = useState("");
  const toast = useToast();
  async function save() {
    setError("");
    if (!name.trim()) {
      setError("请填写策略名称");
      return;
    }
    try {
      let definition: Row = definitionTree;
      if (advanced) definition = JSON.parse(json);
      if (!advanced && ruleCount(definition)) {
        const problem = validateVisualRule(definition);
        if (problem) throw new Error(problem);
      }
      for (const side of ["entry", "exit"] as const) {
        if (tradeRules[side]) {
          const problem = validateVisualRule(
            normalizeRuleTree(tradeRules[side]),
          );
          if (problem)
            throw new Error(
              `${side === "entry" ? "买入" : "卖出"}条件：${problem}`,
            );
        }
      }
      if (
        !ruleCount(definition) &&
        !advanced &&
        universe.mode === "all" &&
        universe.st === "all"
      )
        throw new Error("请至少配置一项条件");
      definition = withUniverse(definition, universe);
      setBusy(true);
      const body = {
        name: name.trim(),
        period,
        definition,
        trade_rules: tradeRules,
        policy: {
          ...(strategy?.policy || {}),
          duration_sessions: Number(duration),
        },
        ...(strategy ? { apply_to_active_tracking: applyActive } : {}),
      };
      const saved = strategy
        ? await patch(`/strategies/${strategy.strategy_id}`, body)
        : await post("/strategies", body);
      toast(
        strategy && applyActive
          ? `策略已保存 · 更新 ${saved.policy_application?.applied_count ?? 0} 个跟踪轮次，保留 ${saved.policy_application?.skipped_custom_count ?? 0} 个自定义轮次`
          : "策略已保存，默认期限用于之后新入池轮次",
      );
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={strategy ? "编辑策略 · 创建新版本" : "创建选股策略"}
      onClose={onClose}
      wide
    >
      <div className="modal-body">
        <div className="form-grid">
          <label>
            策略名称
            <input
              placeholder="例如：三倍量 + MA30趋势"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            判断周期
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="D">日线 · 每交易日</option>
              <option value="W">周线 · 完整周</option>
              <option value="M">月线 · 完整月</option>
            </select>
          </label>
        </div>
        <UniverseEditor value={universe} onChange={setUniverse} />
        <div className="form-section-title">
          <h3>选股条件</h3>
          <div className="button-group">
            <button
              className="btn small"
              onClick={() => {
                if (!advanced) {
                  setJson(JSON.stringify(definitionTree, null, 2));
                  setAdvanced(true);
                } else {
                  try {
                    const tree = JSON.parse(json);
                    setDefinitionTree(normalizeRuleTree(tree));
                    setAdvanced(false);
                  } catch {
                    setError("请先修正JSON格式后切换编辑方式");
                  }
                }
              }}
            >
              {advanced ? "可视化编辑" : "结构化规则"}
            </button>
          </div>
        </div>
        {advanced ? (
          <label>
            安全规则树 JSON
            <textarea
              className="code-input"
              rows={12}
              value={json}
              onChange={(e) => setJson(e.target.value)}
            />
          </label>
        ) : (
          <RuleTreeEditor
            value={definitionTree}
            onChange={setDefinitionTree}
            fields={fields}
            topics={topics}
            period={period}
          />
        )}
        <Warning>
          “收盘价 &gt; MA30”判断所选周期收盘是否在均线上；“收盘上穿
          MA30”要求前一可比周期未站上、本周期才站上，是不同事件。日、周、月均按每项条件所选周期判断，周/月只使用完整周期。
          <br />
          三倍量为所选条件周期成交量 /
          前一可比完整周期成交量达到3倍；停牌、零量或覆盖不足时待核验。涨停事件、N个交易日新高及跟踪日基准等日线专属条件独立固定为日K；结构化规则只执行后端白名单字段。
        </Warning>
        <TradeRulesEditor
          value={tradeRules}
          onChange={setTradeRules}
          fields={fields}
          period={period}
        />
        <div className="form-grid">
          <label>
            默认跟踪期限 · 交易日
            <input
              type="number"
              min={1}
              max={250}
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          </label>
          <div className="form-help">
            入池当天为第 0
            日，重复命中不重置价格与期限。提醒与提前结束可在跟踪池单独设置。
          </div>
        </div>
        {strategy && (
          <PolicyScope
            strategy={strategy}
            checked={applyActive}
            onChange={setApplyActive}
          />
        )}
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" onClick={onClose}>
          取消
        </button>
        <button className="btn primary" disabled={busy} onClick={save}>
          {busy ? <Spinner /> : <Check size={15} />}保存策略
        </button>
      </div>
    </Modal>
  );
}
function StockStrategies({
  onStock,
  onNavigate,
  onRefresh,
}: {
  onStock: (id: string, navigation?: StockNavigation) => void;
  onNavigate: (route: string, context?: Row) => void;
  onRefresh?: () => void;
}) {
  const result = useApi<Row>("/strategies"),
    fieldsResult = useApi<Row>("/strategy-fields"),
    topicsResult = useApi<Row>("/topics");
  const strategies = list(result.data),
    fields = (
      list(fieldsResult.data).length ? list(fieldsResult.data) : fallbackFields
    ).map((f: Row): Row => ({ ...f, label: fieldLabel(f.field, f.label) }));
  const [selectedId, setSelectedId] = useState(""),
    [editor, setEditor] = useState<Row | null | undefined>(undefined),
    [busy, setBusy] = useState(""),
    [run, setRun] = useState<Row | null>(null),
    [decision, setDecision] = useState("matched"),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [error, setError] = useState("");
  const history = useApi<Row>(
    selectedId ? `/strategy-runs?strategy_id=${selectedId}` : null,
  );
  const chosen =
    strategies.find((s) => s.strategy_id === selectedId) || strategies[0];
  const toast = useToast();
  useEffect(() => {
    if (!selectedId && strategies[0]) setSelectedId(strategies[0].strategy_id);
  }, [strategies]);
  useEffect(() => {
    setRun(null);
    setSelected(new Set());
  }, [selectedId]);
  async function bootstrap() {
    setBusy("bootstrap");
    try {
      await post("/strategies/bootstrap");
      result.reload();
      topicsResult.reload();
      toast("已创建默认策略，命中结果由真实行情计算");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function runStrategy() {
    if (!chosen) return;
    setBusy("run");
    setError("");
    try {
      const data = await post(`/strategies/${chosen.strategy_id}/runs`, {
        request_key: requestKey(),
      });
      setRun(data);
      setDecision("matched");
      setSelected(new Set());
      history.reload();
      result.reload();
      toast(
        `运行完成：${data.matched_count ?? list(data).filter((x) => x.decision === "matched").length} 只满足`,
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function join() {
    if (!run || !selected.size) return;
    setBusy("join");
    try {
      const data = await post("/tracking-rounds", {
        run_id: run.run_id,
        instrument_ids: [...selected],
      });
      toast(
        `已加入 ${data.added_count ?? list(data).length} 只${data.skipped?.length ? "，跳过 " + data.skipped.length + " 只" : ""}`,
      );
      setSelected(new Set());
      result.reload();
      onRefresh?.();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function loadRun(id: string) {
    setBusy("history");
    try {
      setRun(await api(`/strategy-runs/${id}`));
      setSelected(new Set());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  const candidates = (run?.items || run?.candidates || []).filter(
    (r: Row) => r.decision === decision,
  );
  return (
    <div className="page-stack">
      <div className="section-intro">
        <p>定义选股条件 → 运行真实行情 → 选择命中 → 加入对应策略跟踪池</p>
        <button className="btn primary" onClick={() => setEditor(null)}>
          <Plus size={15} />
          新建策略
        </button>
      </div>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      <div className="workspace-split">
        <Panel
          title="选股策略"
          actions={
            <button
              className="icon-btn"
              title="刷新策略"
              onClick={result.reload}
            >
              <RefreshCw size={14} />
            </button>
          }
          className="strategy-sidebar"
        >
          {result.loading ? (
            <Loading />
          ) : strategies.length ? (
            <div className="strategy-list">
              {strategies.map((s) => (
                <button
                  className={
                    "strategy-card " +
                    (chosen?.strategy_id === s.strategy_id ? "active" : "")
                  }
                  key={s.strategy_id}
                  disabled={!!busy}
                  onClick={() => setSelectedId(s.strategy_id)}
                >
                  <div>
                    <strong>{s.name}</strong>
                    <Badge>
                      {sectorPeriodLabel(s.period)} · V{s.version}
                    </Badge>
                  </div>
                  <p>
                    {ruleCount(s.definition)} 项条件 ·{" "}
                    {s.definition?.logic === "OR" || s.definition?.any
                      ? "任一满足"
                      : "全部满足"}
                  </p>
                  <footer>
                    <span>
                      跟踪中 <b>{s.tracking_count ?? 0}</b>
                    </span>
                    <ArrowRight size={14} />
                  </footer>
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="还没有选股策略"
              description="从真实数据创建默认策略，或自定义你的规则。"
              action={
                <button className="btn" disabled={!!busy} onClick={bootstrap}>
                  {busy === "bootstrap" ? <Spinner /> : <Plus size={14} />}
                  创建默认策略
                </button>
              }
            />
          )}
        </Panel>
        <div className="page-stack">
          {chosen && (
            <Panel
              title={chosen.name}
              subtitle={`规则 V${chosen.version} · 默认${sectorPeriodLabel(chosen.period)}（周/月仅完整周期） · 默认跟踪 ${chosen.policy?.duration_sessions || 30} 个交易日`}
              actions={
                <>
                  <button
                    className="btn small"
                    onClick={() => setEditor(chosen)}
                  >
                    <Edit3 size={13} />
                    编辑
                  </button>
                  <button
                    className="btn small"
                    onClick={() =>
                      onNavigate("backtest", {
                        strategy_id: chosen.strategy_id,
                      })
                    }
                  >
                    <FlaskConical size={13} />
                    回测
                  </button>
                  <button
                    className="btn primary"
                    disabled={!!busy}
                    onClick={runStrategy}
                  >
                    {busy === "run" ? <Spinner /> : <Play size={14} />}运行选股
                  </button>
                </>
              }
            >
              <div className="saved-rule-summary">
                <small className="muted">
                  {chosen.definition?.any || chosen.definition?.logic === "OR"
                    ? "任一满足"
                    : "全部满足"}
                </small>
                {(
                  normalizeRuleTree(chosen.definition).all ||
                  normalizeRuleTree(chosen.definition).any || [
                    chosen.definition,
                  ]
                ).map((node: Row, index: number) => (
                  <div
                    key={index}
                    className={node.history_event ? "history-summary" : ""}
                  >
                    {node.history_event ? (
                      <Clock3 size={15} />
                    ) : (
                      <Check size={15} />
                    )}
                    <span>{historyRuleSummary(node, fieldLabel)}</span>
                  </div>
                ))}
              </div>
              <TradeRuleSummary value={chosen.trade_rules} compact />
              <div className="panel-footnote">
                数据不足的条件独立标记待核验；选股候选与已跟踪轮次分别保存。
              </div>
            </Panel>
          )}
          {chosen && (
            <StrategyAutomation
              key={chosen.strategy_id}
              strategyId={chosen.strategy_id}
            />
          )}
          {error && <ErrorBox message={error} />}
          <Panel
            title="运行结果"
            subtitle={
              run
                ? `${run.business_date} · 策略 V${run.strategy_version} · ${run.run_id?.slice(0, 12)}`
                : "运行后展示逐股命中依据"
            }
            actions={
              history.data && list(history.data).length ? (
                <select
                  aria-label="历史选股运行"
                  disabled={!!busy}
                  value={run?.run_id || ""}
                  onChange={(e) => e.target.value && loadRun(e.target.value)}
                >
                  <option value="">选择历史运行</option>
                  {list(history.data).map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {r.business_date} · 命中{" "}
                      {r.matched_count ?? r.counts?.matched ?? "—"} ·{" "}
                      {r.run_id.slice(0, 6)}
                    </option>
                  ))}
                </select>
              ) : undefined
            }
          >
            {run ? (
              <>
                <div className="result-toolbar">
                  <div className="tabs">
                    {[
                      ["matched", "满足", run.matched_count],
                      ["unknown", "待核验", run.unknown_count],
                      ["unmatched", "未满足", run.unmatched_count],
                    ].map(([key, name, count]) => (
                      <button
                        key={key}
                        className={decision === key ? "active" : ""}
                        onClick={() => {
                          setDecision(String(key));
                          setSelected(new Set());
                        }}
                      >
                        {name}
                        <span>
                          {count ??
                            (run.items || []).filter(
                              (r: Row) => r.decision === key,
                            ).length}
                        </span>
                      </button>
                    ))}
                  </div>
                  {decision === "matched" && (
                    <div className="button-group">
                      <button
                        className="btn small"
                        onClick={() =>
                          setSelected(
                            new Set(
                              candidates.map((r: Row) => r.instrument_id),
                            ),
                          )
                        }
                      >
                        全选命中
                      </button>
                      <button
                        className="btn primary"
                        disabled={!selected.size || !!busy}
                        onClick={join}
                      >
                        {busy === "join" ? <Spinner /> : <Plus size={14} />}
                        加入跟踪 {selected.size || ""}
                      </button>
                    </div>
                  )}
                </div>
                <Table
                  rows={candidates.map((r: Row) => ({
                    ...r,
                    close: r.close_price ?? r.close,
                    volume_ratio: r.metrics?.volume_ratio,
                    change_pct: r.metrics?.change_pct,
                    signal_date:
                      r.metrics?.signal_date ?? r.metrics?.observed_date,
                  }))}
                  columns={[
                    { key: "name" },
                    { key: "close" },
                    { key: "signal_date", title: "默认周期信号日" },
                    { key: "change_pct", title: "默认周期涨幅" },
                    { key: "volume_ratio", title: "默认周期量能倍数" },
                    {
                      key: "topics",
                      sortValue: (row) =>
                        Array.isArray(row.topics) && row.topics.length
                          ? textValue(row.topics)
                          : null,
                    },
                    {
                      key: "checks",
                      title: "命中依据",
                      render: (v) => <RuleEvidence value={v} />,
                    },
                  ]}
                  onStock={onStock}
                  onStockNavigate={(id, orderedRows) =>
                    onStock(
                      id,
                      createStockNavigation(orderedRows, {
                        scopeKey: `${run.run_id}:${decision}`,
                        label: `${chosen?.name || "选股结果"} · ${decision === "matched" ? "满足条件" : decision === "unknown" ? "待核验" : "未满足"}`,
                        asOf: run.business_date || undefined,
                      }),
                    )
                  }
                  selection={selected}
                  onSelect={
                    decision === "matched"
                      ? (id) =>
                          setSelected((v) => {
                            const n = new Set(v);
                            n.has(id) ? n.delete(id) : n.add(id);
                            return n;
                          })
                      : undefined
                  }
                  emptyTitle={
                    decision === "matched"
                      ? "本次没有满足条件的标的"
                      : decision === "unknown"
                        ? "无待核验标的"
                        : "无未满足标的"
                  }
                />
              </>
            ) : (
              <Empty
                title="选择策略并运行选股"
                description="运行与候选结果保存在后端，不会因为切换盘面日期而改变。"
              />
            )}
          </Panel>
        </div>
      </div>
      {editor !== undefined && (
        <StrategyEditor
          strategy={editor || undefined}
          fields={fields}
          topics={list(topicsResult.data)}
          onClose={() => setEditor(undefined)}
          onSaved={result.reload}
        />
      )}
    </div>
  );
}
