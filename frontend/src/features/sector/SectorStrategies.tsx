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
import SectorTaskProgress from "./SectorTaskProgress";
import {
  sectorDefaultLeaf,
  sectorKindLabel,
  sectorPeriodLabel,
  sectorRuleText,
  sectorStrategyBody,
  validateSectorRule,
} from "./sectorPresentation";
import "./SectorWorkspace.css";

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
}: {
  onNavigate: (route: string, context?: Row) => void;
}) {
  const result = useApi<Row>("/sector-strategies"),
    fieldResult = useApi<Row>("/sector-strategy-fields");
  const fields = list(fieldResult.data).filter(
      (field) => !field.tracking_only && !field.alerts_only,
    ),
    strategies = list(result.data);
  const [selectedId, setSelectedId] = useState(""),
    [editor, setEditor] = useState<Row | null | undefined>(undefined),
    [run, setRun] = useState<Row | null>(null),
    [decision, setDecision] = useState("matched"),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [chart, setChart] = useState<Row | null>(null),
    [activeRequest, setActiveRequest] = useState("");
  const chosen =
    strategies.find((item) => item.strategy_id === selectedId) || strategies[0];
  const history = useApi<Row>(
    chosen
      ? `/sector-strategy-runs?strategy_id=${encodeURIComponent(chosen.strategy_id)}`
      : null,
  );
  const toast = useToast(),
    generation = useRef(0);
  useEffect(() => {
    if (!selectedId && strategies[0]) setSelectedId(strategies[0].strategy_id);
  }, [strategies, selectedId]);
  useEffect(() => {
    generation.current++;
    setRun(null);
    setSelected(new Set());
    setError("");
  }, [selectedId]);
  async function execute(id?: string) {
    if (!chosen) return;
    const token = ++generation.current;
    setBusy(id ? "history" : "run");
    const key = id ? "" : requestKey();
    setActiveRequest(key);
    setError("");
    try {
      const data = id
        ? await api(`/sector-strategy-runs/${encodeURIComponent(id)}`)
        : await post(`/sector-strategies/${chosen.strategy_id}/runs`, {
            request_key: key,
          });
      if (token !== generation.current) return;
      setRun(data);
      setDecision("matched");
      setSelected(new Set());
      history.reload();
      result.reload();
    } catch (e: any) {
      if (token === generation.current) setError(e.message);
    } finally {
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
                <button
                  key={item.strategy_id}
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
                    disabled={!!busy}
                    onClick={() => execute()}
                  >
                    {busy === "run" ? <Spinner /> : <Play size={14} />}
                    运行板块筛选
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
          <Panel
            title="板块运行结果"
            subtitle={
              run
                ? `${run.business_date} · 策略V${run.strategy_version} · ${run.status || "已完成"}`
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
            {busy === "run" && (
              <SectorTaskProgress requestKey={activeRequest} />
            )}
            {run ? (
              <>
                <div className="result-toolbar">
                  <div className="tabs">
                    {[
                      ["matched", "满足"],
                      ["unknown", "待核验"],
                      ["unmatched", "未满足"],
                    ].map(([key, label]) => (
                      <button
                        key={key}
                        className={decision === key ? "active" : ""}
                        onClick={() => {
                          setDecision(key);
                          setSelected(new Set());
                        }}
                      >
                        {label}
                        <span>
                          {run[`${key}_count`] ??
                            run.counts?.[key] ??
                            list(run).filter((item) => item.decision === key)
                              .length}
                        </span>
                      </button>
                    ))}
                  </div>
                  {decision === "matched" && (
                    <div className="button-group">
                      <button
                        className="btn small"
                        disabled={!!busy || !rows.length}
                        onClick={() =>
                          setSelected(new Set(rows.map((row) => row.target_id)))
                        }
                      >
                        全选本次命中
                      </button>
                      <button
                        className="btn small"
                        disabled={!!busy || !selected.size}
                        onClick={() => setSelected(new Set())}
                      >
                        清空选择
                      </button>
                      <button
                        className="btn primary"
                        disabled={!!busy || !selected.size}
                        onClick={join}
                      >
                        {busy === "join" && <Spinner />}加入板块跟踪（
                        {selected.size}）
                      </button>
                    </div>
                  )}
                </div>
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
                      sortValue: (row) => row.metrics_by_period?.D?.change_pct,
                      render: (_, row) => (
                        <span
                          className={tone(row.metrics_by_period?.D?.change_pct)}
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
              </>
            ) : (
              <Empty
                title="尚未运行板块策略"
                description="运行只计算已留存行情，不会自动请求来源。"
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
