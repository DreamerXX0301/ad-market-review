import { useEffect, useRef, useState } from "react";
import { Edit3, Play, Plus, RefreshCw } from "lucide-react";
import { api, list, patch, post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { num, percent, tone } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Panel,
  Spinner,
  Table,
  useToast,
  Warning,
} from "../../components/UI";
import SectorChartDialog from "../market/SectorChartDialog";
import SectorRuleBuilder from "./SectorRuleBuilder";
import SectorEvidence from "./SectorEvidence";
import {
  finishTaskRequest,
  startTaskRequest,
  strategyTaskBusy,
  useTaskActivity,
} from "../../components/taskActivity";
import {
  sectorDefaultLeaf,
  sectorKindLabel,
  sectorPeriodLabel,
  sectorRuleText,
  sectorStrategyBody,
  validateSectorRule,
} from "./sectorPresentation";
import "./SectorWorkspace.css";
import {
  readStrategySelection,
  rememberStrategySelection,
  resolvedStrategyId,
  savedRunNotice,
} from "../strategies/strategyResultPresentation";
import useSavedStrategyRun from "../strategies/useSavedStrategyRun";
import StrategyBatchToolbar, {
  useBatchStrategySelection,
} from "../strategies/StrategyBatchToolbar";
import StrategyResultTabs, {
  StrategyRunNotice,
} from "../strategies/StrategyResultTabs";

function SectorStrategyEditor({
  strategy,
  fields,
  onClose,
  onSaved,
}: {
  strategy?: Row;
  fields: Row[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(strategy?.name || ""),
    [period, setPeriod] = useState(strategy?.period || "D"),
    [kind, setKind] = useState(strategy?.sector_kind || "all"),
    [definition, setDefinition] = useState<Row>(
      strategy?.definition || { all: [sectorDefaultLeaf(fields)] },
    ),
    [duration, setDuration] = useState(
      strategy?.policy?.duration_sessions ?? 30,
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save() {
    const problem = validateSectorRule(definition, fields, period);
    if (
      !name.trim() ||
      problem ||
      !Number.isInteger(Number(duration)) ||
      Number(duration) < 1 ||
      Number(duration) > 750
    ) {
      setError(
        problem ||
          (!name.trim() ? "请填写策略名称" : "跟踪期限请输入1—750交易日"),
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body = sectorStrategyBody({
        name,
        period,
        sectorKind: kind,
        definition,
        duration: Number(duration),
        existingPolicy: strategy?.policy,
      });
      if (strategy)
        await patch(`/sector-strategies/${strategy.strategy_id}`, body);
      else await post("/sector-strategies", body);
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
      title={strategy ? "编辑板块策略" : "新建板块策略"}
      onClose={onClose}
      wide
    >
      <div className="modal-body">
        <div className="form-grid">
          <label>
            策略名称
            <input
              value={name}
              placeholder="例如：日线60新高＋周MACD金叉"
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            默认判断周期
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="D">日K</option>
              <option value="W">完整周K</option>
              <option value="M">完整月K</option>
            </select>
          </label>
          <label>
            板块范围
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="all">行业与概念</option>
              <option value="industry">行业</option>
              <option value="concept">概念</option>
            </select>
          </label>
          <label>
            默认跟踪期限 · 交易日
            <input
              type="number"
              min={1}
              max={750}
              step={1}
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          </label>
        </div>
        <h3>板块筛选条件</h3>
        <SectorRuleBuilder
          value={definition}
          onChange={setDefinition}
          fields={fields}
          period={period}
        />
        <div className="visual-rule-preview">
          {sectorRuleText(definition, fields, period)}
        </div>
        <Warning>
          日、周、月条件可独立组合；周/月只使用已完成周期。新高窗口固定为前N个市场交易日。此策略筛选板块指数，不包含个股涨停、ST、上市板块条件；成分股不会自动加入个股跟踪池。
        </Warning>
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" onClick={onClose}>
          取消
        </button>
        <button
          className="btn primary"
          disabled={busy || !fields.length}
          onClick={save}
        >
          {busy && <Spinner />}保存板块策略
        </button>
      </div>
    </Modal>
  );
}

export default function SectorStrategies({
  onNavigate,
  dataDate,
}: {
  onNavigate: (route: string, context?: Row) => void;
  dataDate?: string;
}) {
  const result = useApi<Row>("/sector-strategies"),
    fieldResult = useApi<Row>("/sector-strategy-fields");
  const fields = list(fieldResult.data).filter(
      (field) => !field.tracking_only && !field.alerts_only,
    ),
    strategies = list(result.data);
  const [selectedId, setSelectedId] = useState(() =>
      readStrategySelection("sector"),
    ),
    [editor, setEditor] = useState<Row | null | undefined>(undefined),
    [decision, setDecision] = useState("matched"),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [chart, setChart] = useState<Row | null>(null);
  const batchSelection = useBatchStrategySelection(strategies);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchRunRequest, setBatchRunRequest] = useState<Row | null>(null);
  const chosen =
    strategies.find((item) => item.strategy_id === selectedId) || strategies[0];
  const saved = useSavedStrategyRun("sector", chosen, decision, dataDate);
  const { run, setRun } = saved;
  const taskActivity = useTaskActivity();
  const runningChosen = strategyTaskBusy(
    taskActivity,
    chosen?.strategy_id,
    "sector",
  );
  const runningElsewhere =
    taskActivity.tasks.some((task) => task.status === "running") ||
    taskActivity.pending.length > 0;
  const history = useApi<Row>(
    chosen
      ? `/sector-strategy-runs?strategy_id=${encodeURIComponent(chosen.strategy_id)}`
      : null,
  );
  const toast = useToast(),
    generation = useRef(0);
  const runNotice = savedRunNotice(
    run,
    chosen,
    dataDate,
    busy === "run" || runningChosen,
  );
  useEffect(() => {
    if (!strategies.length) return;
    const id = resolvedStrategyId(strategies, selectedId);
    if (id !== selectedId) setSelectedId(id);
    rememberStrategySelection("sector", id);
  }, [strategies, selectedId]);
  useEffect(() => {
    generation.current++;
    setError("");
    setBusy("");
  }, [chosen?.strategy_id]);
  useEffect(() => {
    setSelected(new Set());
  }, [chosen?.strategy_id, run?.run_id, decision]);
  useEffect(() => {
    if (!batchRunRequest || chosen?.strategy_id !== batchRunRequest.strategy_id)
      return;
    saved.loadRun(batchRunRequest.run_id);
    setDecision("matched");
    setSelected(new Set());
    setBatchRunRequest(null);
  }, [batchRunRequest, chosen?.strategy_id, saved.loadRun]);
  async function execute(id?: string) {
    if (id) {
      saved.loadRun(id);
      setDecision("matched");
      setSelected(new Set());
      return;
    }
    if (
      !chosen ||
      busy ||
      (!id && (runningChosen || runningElsewhere || batchBusy))
    )
      return;
    const token = ++generation.current;
    setBusy(id ? "history" : "run");
    const key = id ? "" : requestKey();
    if (!id)
      startTaskRequest({
        request_key: key,
        kind: "selection",
        title: "板块策略筛选",
        target_type: "sector",
        strategy_id: chosen.strategy_id,
        strategy_name: chosen.name,
      });
    setError("");
    try {
      const data = await post(
        `/sector-strategies/${chosen.strategy_id}/runs?response_decision=matched`,
        {
          request_key: key,
        },
      );
      if (token !== generation.current) return;
      setRun(data);
      setDecision("matched");
      setSelected(new Set());
      history.reload();
      result.reload();
      if (!id)
        toast(
          `板块筛选已完成：${data.matched_count ?? data.counts?.matched ?? "—"} 个满足`,
        );
    } catch (e: any) {
      if (token === generation.current) setError(e.message);
    } finally {
      if (!id) finishTaskRequest(key);
      if (token === generation.current) setBusy("");
    }
  }
  async function join() {
    if (!run || !selected.size) return;
    setBusy("join");
    setError("");
    try {
      const data = await post("/sector-tracking-rounds", {
        run_id: run.run_id,
        target_ids: [...selected],
      });
      toast(`已加入 ${data.added_count ?? list(data).length} 个板块跟踪`);
      setSelected(new Set());
      result.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  const rows = list(run).filter((row) => row.decision === decision);
  return (
    <div className="page-stack sector-workspace">
      <div className="section-intro">
        <p>独立筛选行业与概念 → 跟踪板块自身 → 按需展开成分股</p>
        <button
          className="btn primary"
          disabled={!fields.length}
          onClick={() => setEditor(null)}
        >
          <Plus size={15} />
          新建板块策略
        </button>
      </div>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}{" "}
      {fieldResult.error && (
        <ErrorBox message={fieldResult.error} retry={fieldResult.reload} />
      )}
      <StrategyBatchToolbar
        target="sector"
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
          title="板块策略"
          className="strategy-sidebar"
          actions={
            <button
              className="icon-btn"
              aria-label="刷新板块策略"
              onClick={result.reload}
            >
              <RefreshCw size={14} />
            </button>
          }
        >
          {result.loading ? (
            <Loading />
          ) : strategies.length ? (
            <div className="strategy-list">
              {strategies.map((item) => (
                <div
                  className={`strategy-select-card ${batchSelection.selected.has(item.strategy_id) ? "checked" : ""}`}
                  key={item.strategy_id}
                >
                  <label className="strategy-select-checkbox">
                    <input
                      type="checkbox"
                      aria-label={`选择策略：${item.name}`}
                      checked={batchSelection.selected.has(item.strategy_id)}
                      disabled={!!busy || batchBusy || !!item.archived}
                      onChange={() => batchSelection.toggle(item.strategy_id)}
                    />
                  </label>
                  <button
                    className={`strategy-card ${chosen?.strategy_id === item.strategy_id ? "active" : ""}`}
                    disabled={!!busy}
                    onClick={() => setSelectedId(item.strategy_id)}
                  >
                    <div>
                      <strong>{item.name}</strong>
                      <Badge>
                        {sectorPeriodLabel(item.period)} · V{item.version}
                      </Badge>
                    </div>
                    <p>{sectorKindLabel(item.sector_kind)}</p>
                    <footer>跟踪中 {item.tracking_count ?? "—"}</footer>
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="还没有板块策略"
              description="创建独立的行业或概念筛选规则。"
            />
          )}
        </Panel>
        <div className="page-stack">
          {chosen && (
            <Panel
              title={chosen.name}
              subtitle={`${sectorKindLabel(chosen.sector_kind)} · 默认${sectorPeriodLabel(chosen.period)} · 各条件可独立周期`}
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
                      onNavigate("tracking", {
                        target_type: "sector",
                        strategy_id: chosen.strategy_id,
                      })
                    }
                  >
                    查看板块跟踪
                  </button>
                  <button
                    className="btn primary"
                    disabled={
                      !!busy || runningChosen || runningElsewhere || batchBusy
                    }
                    onClick={() => execute()}
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
                      ? "板块筛选中"
                      : run || chosen.latest_run
                        ? "刷新板块筛选"
                        : "运行板块筛选"}
                  </button>
                </>
              }
            >
              <div className="saved-rule-summary">
                {sectorRuleText(chosen.definition, fields, chosen.period)}
              </div>
              <div className="panel-footnote">
                使用已留存板块K线；缺报价或预热不足标记待核验。板块与个股策略、结果及跟踪池分别保存。
              </div>
            </Panel>
          )}
          {error && <ErrorBox message={error} />}
          {saved.error && (
            <ErrorBox message={saved.error} retry={saved.retry} />
          )}
          <Panel
            title="板块运行结果"
            subtitle={
              run
                ? `${run.business_date} · 规则 V${run.strategy_version} · 已保存`
                : "运行后展示板块条件依据"
            }
            actions={
              list(history.data).length ? (
                <select
                  aria-label="板块历史运行"
                  disabled={!!busy}
                  value={run?.run_id || ""}
                  onChange={(e) => e.target.value && execute(e.target.value)}
                >
                  <option value="">选择历史运行</option>
                  {list(history.data).map((item) => (
                    <option key={item.run_id} value={item.run_id}>
                      {item.business_date} · {item.status} · 命中
                      {item.matched_count ?? item.counts?.matched ?? "—"}
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
                        disabled={
                          !!busy ||
                          !rows.length ||
                          saved.restoring ||
                          !saved.candidatesReady
                        }
                        onClick={() =>
                          setSelected(new Set(rows.map((row) => row.target_id)))
                        }
                      >
                        全选本次命中
                      </button>
                      <button
                        className="btn small"
                        disabled={
                          !!busy ||
                          !selected.size ||
                          saved.restoring ||
                          !saved.candidatesReady
                        }
                        onClick={() => setSelected(new Set())}
                      >
                        清空选择
                      </button>
                      <button
                        className="btn primary"
                        disabled={
                          !!busy ||
                          !selected.size ||
                          saved.restoring ||
                          !saved.candidatesReady
                        }
                        onClick={join}
                      >
                        {busy === "join" && <Spinner />}加入板块跟踪（
                        {selected.size}）
                      </button>
                    </div>
                  )}
                </div>
                {saved.restoring ? (
                  <div className="saved-run-loading" role="status">
                    <Spinner />
                    <span>正在读取当前分类的已保存结果</span>
                  </div>
                ) : saved.candidatesReady ? (
                  <Table
                    key={`${run.run_id}-${decision}`}
                    rows={rows}
                    rowKey="target_id"
                    selection={decision === "matched" ? selected : undefined}
                    onSelect={(id) =>
                      setSelected((old) => {
                        const next = new Set(old);
                        next.has(id) ? next.delete(id) : next.add(id);
                        return next;
                      })
                    }
                    columns={[
                      {
                        key: "name",
                        title: "板块",
                        render: (_, row) => (
                          <button
                            className="table-link sector-asset-name"
                            onClick={() =>
                              setChart({ ...row, date: run.business_date })
                            }
                          >
                            <strong>{row.name}</strong>
                            <small>
                              {row.topic_code} ·{" "}
                              {sectorKindLabel(row.sector_kind)}
                            </small>
                          </button>
                        ),
                      },
                      {
                        key: "close_price",
                        title: "观察日指数点位",
                        render: (value) => num(value),
                      },
                      {
                        key: "_day_change",
                        title: "观察日日涨幅",
                        sortValue: (row) =>
                          row.metrics_by_period?.D?.change_pct,
                        render: (_, row) => (
                          <span
                            className={tone(
                              row.metrics_by_period?.D?.change_pct,
                            )}
                          >
                            {percent(row.metrics_by_period?.D?.change_pct)}
                          </span>
                        ),
                      },
                      {
                        key: "checks",
                        title: "多周期判断依据",
                        sortable: false,
                        render: (value) => (
                          <SectorEvidence checks={value} fields={fields} />
                        ),
                      },
                    ]}
                  />
                ) : (
                  <Empty
                    title="当前分类的已保存结果暂未读出"
                    description="请重试读取，已有运行不会因此改变。"
                  />
                )}
              </>
            ) : result.loading ||
              saved.restoring ||
              busy === "run" ||
              runningChosen ? (
              <div className="saved-run-loading" role="status">
                <Spinner />
                <span>
                  {result.loading || saved.restoring
                    ? "正在读取上次已保存的板块结果"
                    : "板块策略正在运行，完成后将保存结果；进度见页面上方"}
                </span>
              </div>
            ) : (
              <Empty
                title={
                  saved.error ? "已保存结果暂未读出" : "还没有已保存的板块结果"
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
      {editor !== undefined && (
        <SectorStrategyEditor
          strategy={editor || undefined}
          fields={fields}
          onClose={() => setEditor(undefined)}
          onSaved={result.reload}
        />
      )}
      {chart && (
        <SectorChartDialog
          topic={{
            topicCode: chart.topic_code,
            name: chart.name,
            proxy: false,
            notice: "同花顺行业/概念板块指数",
          }}
          date={chart.date}
          onClose={() => setChart(null)}
        />
      )}
    </div>
  );
}
