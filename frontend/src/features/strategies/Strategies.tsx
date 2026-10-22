import { useEffect, useMemo, useState } from "react";
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
  Trash2,
  RotateCcw,
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
import RecycleConfirm from "../../components/RecycleConfirm";
import PermanentDeleteDialog, {
  PendingPurgeJobs,
} from "../../components/PermanentDeleteDialog";
import {
  purgeBlocksRestore,
  recyclePageOffset,
} from "../../components/permanentDeletePresentation";
import type { PurgeTarget } from "../../components/permanentDeletePresentation";
import {
  deletionReason,
  restorationReason,
  recycleListWithout,
  selectionAfterRecycle,
  visibleRecycleRows,
} from "../../components/recyclePresentation";
import { fallbackFields, fieldLabel, summarizeChecks } from "./Conditions";
import RuleTreeEditor from "./RuleTreeEditor";
import RuleEvidence from "./RuleEvidence";
import {
  historyRuleSummary,
  hasHiddenConditionRule,
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
import {
  finishTaskRequest,
  startTaskRequest,
  strategyTaskBusy,
  useTaskActivity,
} from "../../components/taskActivity";
import {
  readStrategySelection,
  rememberStrategySelection,
  resolvedStrategyId,
  savedRunNotice,
} from "./strategyResultPresentation";
import useSavedStrategyRun from "./useSavedStrategyRun";
import StrategyBatchToolbar, {
  useBatchStrategySelection,
} from "./StrategyBatchToolbar";
import StrategyResultTabs, { StrategyRunNotice } from "./StrategyResultTabs";
type StrategiesProps = {
  onStock: (id: string, navigation?: StockNavigation) => void;
  onNavigate: (route: string, context?: Row) => void;
  onRefresh?: () => void;
  dataDate?: string;
};
export default function Strategies(props: StrategiesProps) {
  const [target, setTarget] = useState(() =>
    readStrategySelection("target") === "sector" ? "sector" : "stock",
  );
  useEffect(() => rememberStrategySelection("target", target), [target]);
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
        <SectorStrategies
          onNavigate={props.onNavigate}
          dataDate={props.dataDate}
        />
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
      if (advanced) {
        definition = JSON.parse(json);
        if (hasHiddenConditionRule(definition))
          throw new Error("包含不再展示的指标条件，请使用可视化条件编辑");
      }
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
              disabled={!advanced && hasHiddenConditionRule(definitionTree)}
              title={
                hasHiddenConditionRule(definitionTree)
                  ? "已保存的隐藏条件按原配置保留，可在可视化编辑中移除"
                  : undefined
              }
              onClick={() => {
                if (!advanced) {
                  setJson(JSON.stringify(definitionTree, null, 2));
                  setAdvanced(true);
                } else {
                  try {
                    const tree = JSON.parse(json);
                    if (hasHiddenConditionRule(tree)) {
                      setError("包含不再展示的指标条件，请移除后切换编辑方式");
                      return;
                    }
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
  dataDate,
}: {
  onStock: (id: string, navigation?: StockNavigation) => void;
  onNavigate: (route: string, context?: Row) => void;
  onRefresh?: () => void;
  dataDate?: string;
}) {
  const [strategyGeneration, setStrategyGeneration] = useState(0);
  const [trashOpen, setTrashOpen] = useState(false);
  const [trashOffset, setTrashOffset] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<Row | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<PurgeTarget | null>(null);
  const [recycleBusy, setRecycleBusy] = useState("");
  const [recycleError, setRecycleError] = useState("");
  const trash = useApi<Row>(
    trashOpen
      ? `/strategies?deleted=true&limit=25&offset=${trashOffset}&_refresh=${strategyGeneration}`
      : null,
  );
  const trashRows = visibleRecycleRows(list(trash.data), true, "strategy_id");
  useEffect(() => {
    setTrashOffset((current) => recyclePageOffset(trash.data?.total, current));
  }, [trash.data?.total]);
  const result = useApi<Row>(
      `/strategies?deleted=false&_refresh=${strategyGeneration}`,
    ),
    fieldsResult = useApi<Row>("/strategy-fields"),
    topicsResult = useApi<Row>("/topics");
  const strategies = useMemo(
      () => visibleRecycleRows(list(result.data), false, "strategy_id"),
      [result.data],
    ),
    fields = (
      list(fieldsResult.data).length ? list(fieldsResult.data) : fallbackFields
    ).map((f: Row): Row => ({ ...f, label: fieldLabel(f.field, f.label) }));
  const [selectedId, setSelectedId] = useState(() =>
      readStrategySelection("stock"),
    ),
    [editor, setEditor] = useState<Row | null | undefined>(undefined),
    [busy, setBusy] = useState(""),
    [decision, setDecision] = useState("matched"),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [error, setError] = useState("");
  const batchSelection = useBatchStrategySelection(strategies);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchRunRequest, setBatchRunRequest] = useState<Row | null>(null);
  const history = useApi<Row>(
    selectedId ? `/strategy-runs?strategy_id=${selectedId}` : null,
  );
  const chosen =
    strategies.find((s) => s.strategy_id === selectedId) || strategies[0];
  const saved = useSavedStrategyRun("stock", chosen);
  const { run, setRun } = saved;
  const taskActivity = useTaskActivity();
  const runningChosen = strategyTaskBusy(taskActivity, chosen?.strategy_id);
  const runningElsewhere =
    taskActivity.tasks.some((task) => task.status === "running") ||
    taskActivity.pending.length > 0;
  const toast = useToast();
  const runNotice = savedRunNotice(
    run,
    chosen,
    dataDate,
    busy === "run" || runningChosen,
  );
  useEffect(() => {
    if (!result.data) return;
    const id = strategies.length
      ? resolvedStrategyId(strategies, selectedId)
      : "";
    if (id !== selectedId) setSelectedId(id);
    rememberStrategySelection("stock", id);
  }, [strategies, selectedId, result.data]);
  useEffect(() => {
    setSelected(new Set());
    setError("");
  }, [selectedId]);
  useEffect(() => {
    if (!batchRunRequest || chosen?.strategy_id !== batchRunRequest.strategy_id)
      return;
    const request = new AbortController();
    setBusy("history");
    api<Row>(`/strategy-runs/${encodeURIComponent(batchRunRequest.run_id)}`, {
      signal: request.signal,
    })
      .then((data) => {
        if (!request.signal.aborted) {
          setRun(data);
          setDecision("matched");
          setSelected(new Set());
        }
      })
      .catch((problem) => {
        if (!request.signal.aborted) setError(problem.message);
      })
      .finally(() => {
        if (!request.signal.aborted) {
          setBusy("");
          setBatchRunRequest(null);
        }
      });
    return () => request.abort();
  }, [batchRunRequest, chosen?.strategy_id, setRun]);
  async function recycleStrategy(item: Row, restore = false) {
    if (recycleBusy) return;
    const id = item.strategy_id;
    setRecycleBusy(id);
    setRecycleError("");
    try {
      const response = restore
        ? await post(`/strategies/${encodeURIComponent(id)}/restore`)
        : await api<Row>(`/strategies/${encodeURIComponent(id)}`, {
            method: "DELETE",
          });
      // A changed URL retires older in-flight responses without permanent local tombstones.
      setStrategyGeneration((current) => current + 1);
      if (restore) {
        trash.setData((current) =>
          recycleListWithout(current, id, "strategy_id"),
        );
        const total =
          typeof trash.data?.total === "number" ? trash.data.total - 1 : null;
        if (total !== null && trashOffset > 0 && total <= trashOffset)
          setTrashOffset(Math.max(0, trashOffset - 25));
      } else {
        result.setData((current) =>
          recycleListWithout(current, id, "strategy_id"),
        );
        setSelectedId((current) =>
          selectionAfterRecycle(current, id, strategies, "strategy_id"),
        );
        setEditor((current) =>
          current?.strategy_id === id ? undefined : current,
        );
      }
      setDeleteTarget(null);
      toast(
        restore
          ? `策略已恢复，连带恢复 ${response.affected_backtests ?? 0} 条回测`
          : `策略与 ${response.affected_backtests ?? 0} 条关联回测已移到回收站`,
      );
    } catch (problem: any) {
      setRecycleError(problem.message);
      result.reload();
      trash.reload();
    } finally {
      setRecycleBusy("");
    }
  }
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
    if (!chosen || busy || runningElsewhere || runningChosen || batchBusy)
      return;
    const key = requestKey();
    const strategyId = chosen.strategy_id;
    setBusy("run");
    setError("");
    startTaskRequest({
      request_key: key,
      kind: "selection",
      title: "个股策略选股",
      target_type: "stock",
      strategy_id: strategyId,
      strategy_name: chosen.name,
    });
    try {
      const data = await post(`/strategies/${strategyId}/runs`, {
        request_key: key,
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
      finishTaskRequest(key);
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
        <div className="button-group">
          <button
            className="btn"
            onClick={() => {
              setTrashOpen(true);
              setTrashOffset(0);
              setRecycleError("");
            }}
          >
            <Trash2 size={14} />
            回收站
          </button>
          <button className="btn primary" onClick={() => setEditor(null)}>
            <Plus size={15} />
            新建策略
          </button>
        </div>
      </div>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      <StrategyBatchToolbar
        target="stock"
        strategies={strategies}
        selection={batchSelection.selected}
        onSelectAll={batchSelection.setAll}
        disabled={!!busy || runningElsewhere}
        dataDate={dataDate}
        onBusyChange={setBatchBusy}
        onUpdated={() => {
          result.reload();
          history.reload();
        }}
        onOpenRun={(item) => {
          if (
            !strategies.some(
              (strategy) => strategy.strategy_id === item.strategy_id,
            )
          ) {
            setError("该批次策略已不在当前列表，请在历史策略记录中查看。");
            return;
          }
          setSelectedId(item.strategy_id);
          setBatchRunRequest(item);
        }}
      />
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
                <div
                  className={`strategy-select-card ${batchSelection.selected.has(s.strategy_id) ? "checked" : ""}`}
                  key={s.strategy_id}
                >
                  <label className="strategy-select-checkbox">
                    <input
                      type="checkbox"
                      aria-label={`选择策略：${s.name}`}
                      checked={batchSelection.selected.has(s.strategy_id)}
                      disabled={!!busy || batchBusy || !!s.archived}
                      onChange={() => batchSelection.toggle(s.strategy_id)}
                    />
                  </label>
                  <button
                    className={
                      "strategy-card " +
                      (chosen?.strategy_id === s.strategy_id ? "active" : "")
                    }
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
                </div>
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
                    className="icon-btn recycle-delete"
                    aria-label={`删除策略：${chosen.name}`}
                    title={
                      deletionReason(chosen) ||
                      "删除策略与关联回测，可从回收站恢复"
                    }
                    disabled={!!recycleBusy || !!deletionReason(chosen)}
                    onClick={() => {
                      setDeleteTarget(chosen);
                      setRecycleError("");
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
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
                    disabled={
                      !!busy || runningChosen || runningElsewhere || batchBusy
                    }
                    onClick={runStrategy}
                    title={
                      runningElsewhere && !runningChosen
                        ? "已有任务运行，可在页面上方查看当前任务"
                        : undefined
                    }
                  >
                    {busy === "run" || runningChosen ? (
                      <Spinner />
                    ) : (
                      <Play size={14} />
                    )}
                    {busy === "run" || runningChosen
                      ? "选股运行中"
                      : run || chosen.latest_run
                        ? "刷新选股"
                        : "运行选股"}
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
          {saved.error && (
            <ErrorBox message={saved.error} retry={saved.retry} />
          )}
          <Panel
            title="运行结果"
            subtitle={
              run
                ? `${run.business_date} · 规则 V${run.strategy_version} · 已保存`
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
            <StrategyRunNotice notice={runNotice} />
            {run ? (
              <>
                <div className="result-toolbar">
                  <StrategyResultTabs
                    run={run}
                    value={decision}
                    onChange={(key) => {
                      setDecision(key);
                      setSelected(new Set());
                    }}
                  />
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
            ) : result.loading ||
              saved.restoring ||
              busy === "run" ||
              runningChosen ? (
              <div className="saved-run-loading" role="status">
                <Spinner />
                <span>
                  {result.loading || saved.restoring
                    ? "正在读取上次已保存的选股结果"
                    : "策略正在运行，完成后将保存结果；进度见页面上方"}
                </span>
              </div>
            ) : (
              <Empty
                title={
                  saved.error ? "已保存结果暂未读出" : "还没有已保存的选股结果"
                }
                description={
                  saved.error
                    ? "请重试读取；已有结果不会因此丢失。"
                    : "首次运行后会保存结果，返回页面或刷新浏览器可直接恢复。"
                }
              />
            )}
          </Panel>
        </div>
      </div>
      {deleteTarget && (
        <RecycleConfirm
          kind="strategy"
          item={deleteTarget}
          busy={!!recycleBusy}
          error={recycleError}
          onClose={() => {
            setDeleteTarget(null);
            setRecycleError("");
          }}
          onConfirm={() => recycleStrategy(deleteTarget)}
        />
      )}
      {purgeTarget && (
        <PermanentDeleteDialog
          key={purgeTarget.purgeId || purgeTarget.id}
          target={purgeTarget}
          onStock={onStock}
          onChanged={() => {
            setStrategyGeneration((current) => current + 1);
            onRefresh?.();
          }}
          onClose={() => {
            setPurgeTarget(null);
            setStrategyGeneration((current) => current + 1);
          }}
        />
      )}
      {trashOpen && !purgeTarget && (
        <Modal
          title="策略回收站"
          onClose={() => !recycleBusy && setTrashOpen(false)}
        >
          <div className="modal-body recycle-confirm-body">
            <p>
              恢复时，仅恢复本次连带移入的回测。永久删除会清理专属跟踪与关联回测；涉及自选股票的策略受到保护。
            </p>
            <PendingPurgeJobs
              kind="strategy"
              refresh={strategyGeneration}
              onOpen={setPurgeTarget}
              onChanged={() => setStrategyGeneration((current) => current + 1)}
            />
            {trash.error && (
              <ErrorBox message={trash.error} retry={trash.reload} />
            )}
            {recycleError && <ErrorBox message={recycleError} />}
            {trash.loading && !trash.data ? (
              <Loading />
            ) : trashRows.length ? (
              <div className="strategy-recycle-list">
                {trashRows.map((item) => (
                  <div className="strategy-recycle-item" key={item.strategy_id}>
                    <div>
                      <strong>{item.name}</strong>
                      <p>
                        删除于 {dateTime(item.deleted_at_ms)} · 关联回测{" "}
                        {integer(item.backtest_count)} 条
                      </p>
                    </div>
                    <div className="recycle-row-actions">
                      <button
                        className="btn small"
                        disabled={
                          !!recycleBusy ||
                          purgeBlocksRestore(item.active_purge) ||
                          !!restorationReason(item)
                        }
                        title={
                          purgeBlocksRestore(item.active_purge)
                            ? "请先查看永久删除清理状态"
                            : restorationReason(item) ||
                              "恢复策略与本次连带删除的回测"
                        }
                        onClick={() => recycleStrategy(item, true)}
                      >
                        {recycleBusy === item.strategy_id ? (
                          <Spinner />
                        ) : (
                          <RotateCcw size={13} />
                        )}
                        恢复
                      </button>
                      <button
                        type="button"
                        className={`btn small ${item.active_purge ? "" : "danger"}`}
                        disabled={!!recycleBusy}
                        onClick={() =>
                          setPurgeTarget({
                            kind: "strategy",
                            id: item.strategy_id,
                            name: item.name,
                            purgeId: item.active_purge?.purge_id,
                          })
                        }
                      >
                        {item.active_purge ? "查看清理" : "永久删除"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                title="策略回收站为空"
                description="删除的策略与关联回测可以在这里恢复。"
              />
            )}
            {(trashOffset > 0 || trash.data?.has_more) && (
              <div className="strategy-recycle-pagination">
                <button
                  className="btn small"
                  disabled={trashOffset === 0 || !!recycleBusy || trash.loading}
                  onClick={() =>
                    setTrashOffset((value) => Math.max(0, value - 25))
                  }
                >
                  上一页
                </button>
                <span>
                  第 {Math.floor(trashOffset / 25) + 1} 页 · 共{" "}
                  {integer(trash.data?.total)} 条
                </span>
                <button
                  className="btn small"
                  disabled={
                    !trash.data?.has_more || !!recycleBusy || trash.loading
                  }
                  onClick={() => setTrashOffset((value) => value + 25)}
                >
                  下一页
                </button>
              </div>
            )}
          </div>
          <div className="modal-footer">
            <button
              className="btn"
              disabled={!!recycleBusy}
              onClick={() => setTrashOpen(false)}
            >
              关闭
            </button>
          </div>
        </Modal>
      )}
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
