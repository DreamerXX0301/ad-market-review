import { useEffect, useRef, useState } from "react";
import { BookOpen, FileText, Play, RefreshCw } from "lucide-react";
import { api, list, post, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, textValue, label } from "../../lib/format";
import {
  isHiddenIndicatorField,
  visibleIndicatorData,
} from "../../lib/indicatorVisibility";
import InstrumentSearch from "../../components/InstrumentSearch";
import CopyStockCode from "../../components/CopyStockCode";
import {
  Badge,
  Empty,
  Cell,
  Modal,
  ErrorBox,
  Loading,
  Panel,
  Spinner,
  Table,
  useToast,
  Warning,
  Warnings,
} from "../../components/UI";
import { CandleChart } from "../../components/Charts";
import { fieldLabel } from "../strategies/Conditions";
import ResearchDetails from "./ResearchDetails";
import ResearchTaskProgress from "./ResearchTaskProgress";
import { researchTaskError, researchTaskProgress } from "./researchProgress";
import { stockMetricDate } from "../../components/stockExtraMetrics";
import StockLimitSummary from "../../components/StockLimitSummary";
function ResearchSection({ section }: { section: Row }) {
  let content: any = section.content;
  if (typeof content === "string" && content.trim().startsWith("{")) {
    try {
      content = JSON.parse(content);
    } catch {}
  }
  if (content && typeof content === "object" && content["指标"])
    return (
      <Panel
        title={section.title}
        subtitle={`${content["数据日"] || ""} · ${content["价格口径"] === "none" ? "未复权" : content["价格口径"] || ""} · 成交量：${content["成交量单位"] || "以来源为准"}`}
      >
        <Table
          rows={Object.entries(content["指标"])
            .filter(([field]) => !isHiddenIndicatorField(field))
            .map(([field, value]) => ({ field, value }))}
          columns={[
            {
              key: "field",
              title: "观察指标",
              render: (v) => label[v] || fieldLabel(v),
            },
            {
              key: "value",
              title: "实际值",
              render: (v, r) => <Cell field={r.field} value={v} />,
            },
          ]}
          pageSize={25}
        />
      </Panel>
    );
  return (
    <Panel title={section.title}>
      <div className="prose">
        <p style={{ whiteSpace: "pre-wrap" }}>{textValue(content)}</p>
      </div>
    </Panel>
  );
}
export default function Research({
  initialInstrumentId,
  onStock,
}: {
  initialInstrumentId?: string;
  onStock: (id: string, asOf?: string) => void;
}) {
  const [selected, setSelected] = useState<Row | null>(
      initialInstrumentId
        ? { instrument_id: initialInstrumentId, name: initialInstrumentId }
        : null,
    ),
    [report, setReport] = useState<Row | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [taskId, setTaskId] = useState(""),
    [taskSnapshot, setTaskSnapshot] = useState<Row | null>(null),
    [material, setMaterial] = useState<Row | null>(null);
  const history = useApi<Row>("/research/jobs");
  const task = useApi<Row>(taskId ? `/tasks/${taskId}` : null);
  const toast = useToast();
  const generation = useRef(0);
  const selectedRef = useRef<Row | null>(selected);
  const activeTask = useRef<{
    id: string;
    symbol: string;
    generation: number;
  } | null>(null);
  function selectInstrument(instrument: Row) {
    generation.current += 1;
    activeTask.current = null;
    selectedRef.current = instrument;
    setSelected(instrument);
    setReport(null);
    setMaterial(null);
    setTaskId("");
    setTaskSnapshot(null);
    setBusy(false);
    setError("");
  }
  useEffect(() => {
    if (initialInstrumentId)
      selectInstrument({
        instrument_id: initialInstrumentId,
        name: initialInstrumentId,
      });
  }, [initialInstrumentId]);
  useEffect(() => {
    return () => {
      generation.current += 1;
      activeTask.current = null;
    };
  }, []);
  useEffect(() => {
    const binding = activeTask.current;
    if (
      !binding ||
      binding.id !== taskId ||
      binding.generation !== generation.current ||
      binding.symbol !== selectedRef.current?.instrument_id
    )
      return;
    if (!task.data) {
      if (!task.loading) {
        const timer = setTimeout(task.reload, 3000);
        return () => clearTimeout(timer);
      }
      return;
    }
    if (binding.id !== (task.data.task_id || task.data.job_id)) return;
    setTaskSnapshot(task.data);
    if (["running", "queued", "pending"].includes(task.data.status)) {
      if (!task.loading) {
        const timer = setTimeout(task.reload, task.error ? 3000 : 1500);
        return () => clearTimeout(timer);
      }
      return;
    }
    if (["completed", "succeeded", "partial"].includes(task.data.status)) {
      if (task.data.report_available === false) {
        setReport(null);
        setError("研究记录暂不可用，请选择标的后手动运行研究");
      } else {
        const completed = task.data.result || task.data.report;
        if (completed?.instrument?.instrument_id === binding.symbol)
          setReport(completed);
        else setError("研究结果标的未通过核对，请在历史中重新打开正确报告");
      }
      setBusy(false);
      activeTask.current = null;
      setTaskId("");
      history.reload();
    }
    if (task.data.status === "failed") {
      setError(
        researchTaskError(
          task.data.error ||
            task.data.error_summary ||
            task.data.message ||
            "研究任务失败",
        ),
      );
      setBusy(false);
      activeTask.current = null;
      setTaskId("");
    }
    if (["cancelled", "canceled", "paused"].includes(task.data.status)) {
      setBusy(false);
      activeTask.current = null;
      setTaskId("");
      setError(
        task.data.status === "paused"
          ? "研究任务已暂停，可在数据中心手动继续"
          : "研究任务已取消，已发布行情保持原状",
      );
    }
  }, [task.data, taskId, task.error, task.loading]);
  async function run() {
    const symbol = selectedRef.current?.instrument_id;
    if (!symbol) return;
    const requestGeneration = ++generation.current;
    activeTask.current = null;
    setTaskId("");
    setTaskSnapshot({ status: "submitting", stage: "submitting" });
    setMaterial(null);
    setBusy(true);
    setError("");
    setReport(null);
    try {
      const r = await post("/research/jobs", {
        instrument_id: symbol,
        request_key: crypto.randomUUID(),
      });
      if (
        requestGeneration !== generation.current ||
        symbol !== selectedRef.current?.instrument_id
      )
        return;
      setTaskSnapshot(r);
      if (r.task_id && !["completed", "succeeded"].includes(r.status)) {
        activeTask.current = {
          id: r.task_id,
          symbol,
          generation: requestGeneration,
        };
        setTaskId(r.task_id);
      } else {
        const completed = r.report || r.result || r;
        if (completed.instrument?.instrument_id === symbol)
          setReport(completed);
        else setError("研究结果标的未通过核对，请重新运行");
        history.reload();
        setBusy(false);
      }
      toast("个股研究已提交，只使用可核验材料");
    } catch (e: any) {
      if (requestGeneration === generation.current) {
        setError(e.message);
        setTaskSnapshot({ status: "failed", error_summary: e.message });
        setBusy(false);
      }
    }
  }
  async function load(id: string) {
    const requestGeneration = ++generation.current;
    activeTask.current = null;
    setTaskId("");
    setTaskSnapshot(null);
    setReport(null);
    setMaterial(null);
    setError("");
    setBusy(true);
    try {
      const loaded = await api(`/research/jobs/${id}`);
      if (requestGeneration !== generation.current) return;
      setReport(loaded);
      if (loaded.instrument) {
        selectedRef.current = loaded.instrument;
        setSelected(loaded.instrument);
      }
    } catch (e: any) {
      if (requestGeneration === generation.current) setError(e.message);
    } finally {
      if (requestGeneration === generation.current) setBusy(false);
    }
  }
  const marketPreview = selected && (
    <Panel
      title={selected.name + " · 日、周、月行情"}
      actions={
        <button
          className="btn small"
          onClick={() =>
            onStock(
              selected.instrument_id,
              stockMetricDate(report?.data_cutoff) || undefined,
            )
          }
        >
          {report && !stockMetricDate(report.data_cutoff)
            ? "打开最新行情详情"
            : "打开行情详情"}
        </button>
      }
    >
      {report && !stockMetricDate(report.data_cutoff) ? (
        <Empty
          title="研究行情日期待核验"
          description="报告缺少有效行情截止日，暂不展示历史K线与涨停统计。可打开行情详情单独查看最新状态。"
        />
      ) : (
        <CandleChart
          instrumentId={selected.instrument_id}
          asOf={stockMetricDate(report?.data_cutoff) || undefined}
          showStockSummary={!report}
          height={625}
          onLoad={(data) =>
            setSelected((prev: Row | null) =>
              prev?.instrument_id === data.instrument?.instrument_id
                ? { ...prev, ...data.instrument }
                : prev,
            )
          }
        />
      )}
    </Panel>
  );
  return (
    <div className="page-stack">
      <div className="section-intro">
        <p>指定标的后才运行研究。行情查看与公告、财务材料查询分别处理。</p>
        <Badge>当前业务状态 · 独立于盘面日期</Badge>
      </div>
      <Panel title="个股研究" subtitle="检索并选择标的，点击运行后生成留存报告">
        <div className="research-select">
          <InstrumentSearch
            onSelect={(id, row) => {
              selectInstrument({ ...row, instrument_id: id });
            }}
            placeholder="输入股票代码或简称，例如 600519 / 贵州茅台"
          />
          {selected && (
            <div className="selected-instrument">
              <strong>{selected.name}</strong>
              <CopyStockCode instrumentId={selected.instrument_id} />
            </div>
          )}
          <button
            className="btn primary"
            disabled={!selected || busy}
            onClick={run}
          >
            {busy ? <Spinner /> : <Play size={15} />}运行研究
          </button>
        </div>
        {!selected && (
          <div className="panel-footnote">
            没有指定标的时，不会抓取公告、执行研究或创建后台任务。
          </div>
        )}
      </Panel>
      {error && <ErrorBox message={error} />}{" "}
      {taskSnapshot && (
        <ResearchTaskProgress
          task={taskSnapshot}
          pollError={taskId ? task.error : undefined}
          onRetry={taskId ? task.reload : undefined}
        />
      )}
      {busy && !taskSnapshot && <Loading />}
      {!report && marketPreview}
      {report ? (
        <div className="research-report-shell">
          <div className="report-heading">
            <div>
              <h2>
                {report.instrument?.name || selected?.name} ·{" "}
                {report.schema_version === 2 ? "个股调研" : "研究记录"}
              </h2>
              <p>
                行情截止 {report.data_cutoff || "以材料明细为准"} ·{" "}
                {report.research_as_of_ms && (
                  <>材料查询 {dateTime(report.research_as_of_ms)}</>
                )}
              </p>
            </div>
            <div className="research-report-actions">
              <button
                className="btn small"
                onClick={run}
                disabled={busy || !selected}
              >
                <RefreshCw size={13} /> 重新调研
              </button>
              <Badge tone={report.status === "partial" ? "warn" : "good"}>
                {report.status === "partial" ? "已留存 · 材料有缺口" : "已留存"}
              </Badge>
            </div>
          </div>
          {report.schema_version !== 2 &&
            stockMetricDate(report.data_cutoff) &&
            (report.instrument?.instrument_id || selected?.instrument_id) && (
              <StockLimitSummary
                instrumentId={
                  report.instrument?.instrument_id || selected?.instrument_id
                }
                asOf={stockMetricDate(report.data_cutoff)!}
              />
            )}
          {!!report.gaps?.length && (
            <div className="research-summary-notice" role="note">
              <strong>{report.gaps.length} 项材料存在缺口</strong>
              <span>
                {report.gaps
                  .slice(0, 3)
                  .map((gap: Row) => gap.title || "材料")
                  .join("、")}
                {report.gaps.length > 3 ? "等" : ""}
                ；缺失保持未知，详情可在来源说明中查看。
              </span>
            </div>
          )}
          <details className="research-report-fold">
            <summary>材料覆盖与来源说明 · 查看日期、缺口及研究记录信息</summary>
            <div className="research-report-meta">
              <span>
                研究记录 {report.report_id || report.job_id || "待核验"}
              </span>
              <span>
                材料查询 {dateTime(report.research_as_of_ms)} · 行情截止{" "}
                {report.data_cutoff || "待核验"}
              </span>
            </div>
            <Warnings items={report.warnings} />
          </details>
          <ResearchDetails report={report} onMaterial={setMaterial} />
          {report.schema_version !== 2 && marketPreview}
          {report.sections?.length ? (
            <details className="research-report-fold">
              <summary>补充研究记录 · 题材归属与留存文字</summary>
              {report.sections.map((section: Row, i: number) => (
                <ResearchSection key={i} section={section} />
              ))}
            </details>
          ) : (
            <Panel title="研究结果">
              <div className="prose">
                <p>
                  {report.summary ||
                    report.content ||
                    report.message ||
                    "已完成数据核对，详细材料见下方来源列表。"}
                </p>
              </div>
            </Panel>
          )}
          <details className="research-report-fold">
            <summary>材料与来源索引 · 查看原文、报告期和采集日期</summary>
            <Panel
              title="材料与来源"
              subtitle="发布时间、采集时间和报告期分别核对"
            >
              <Table
                rows={report.materials || []}
                columns={[
                  {
                    key: "title",
                    title: "材料",
                    render: (v, r) =>
                      r.url && /^https?:\/\//.test(r.url) ? (
                        <a href={r.url} target="_blank" rel="noreferrer">
                          {v || r.name} ↗
                        </a>
                      ) : (
                        <button className="link" onClick={() => setMaterial(r)}>
                          {v || r.name} · 查看材料
                        </button>
                      ),
                  },
                  { key: "source" },
                  {
                    key: "published_at",
                    title: "发布时间",
                    render: (v, r) => dateTime(v || r.published_at_ms),
                  },
                  { key: "report_period", title: "报告期" },
                  {
                    key: "source_timestamp_ms",
                    title: "来源元数据时间",
                    render: (v) => dateTime(v),
                  },
                  {
                    key: "kind",
                    title: "材料类型",
                    render: (v) =>
                      v === "provider_summary" ? "来源摘要" : v || "待核验",
                  },
                  {
                    key: "fetched_at_ms",
                    title: "采集时间",
                    render: (v) => dateTime(v),
                  },
                ]}
                emptyTitle="没有取得可引用材料"
                emptyDescription="资讯摘要不等于完整公告原文；没有材料时保留缺口。"
              />
            </Panel>
          </details>
        </div>
      ) : (
        !busy && (
          <Panel title="研究报告">
            <Empty
              title="按需生成个股研究"
              description="行情已可独立查看；运行研究后，材料、来源、日期与报告会在这里保留。"
            />
          </Panel>
        )
      )}
      <Panel
        title="研究历史"
        subtitle="报告按最后一次主动打开保留7天；需要最新材料时可手动重新运行"
        actions={
          <button
            className="icon-btn"
            aria-label="刷新研究历史"
            onClick={history.reload}
          >
            <RefreshCw size={14} />
          </button>
        }
      >
        <Table
          rows={list(history.data).map((r) => ({
            ...r,
            name: r.name || r.instrument?.name,
          }))}
          columns={[
            { key: "name" },
            { key: "data_cutoff", title: "行情截止" },
            {
              key: "created_at_ms",
              title: "创建时间",
              render: (v) => dateTime(v),
            },
            {
              key: "status",
              render: (value) =>
                researchTaskProgress({ status: value }).statusLabel,
            },
            {
              key: "report_id",
              title: "报告",
              render: (v, r) => (
                <button
                  className="link"
                  onClick={() => load(v || r.job_id || r.task_id)}
                >
                  打开报告
                </button>
              ),
            },
          ]}
          onStock={(id) => onStock(id)}
          onStockNavigate={(id, _rows, row) =>
            onStock(id, stockMetricDate(row.data_cutoff) || undefined)
          }
          emptyTitle="尚无研究记录"
        />
      </Panel>
      {material && (
        <Modal
          title={material.title || "来源材料"}
          wide
          onClose={() => setMaterial(null)}
        >
          <div className="modal-body">
            <Warning>
              本页保留材料详情。公告正文以官方详情与PDF为准；财报披露日、统计期和来源元数据时间分别核对。
            </Warning>
            {material.url &&
              /^https:\/\/www\.cninfo\.com\.cn\//.test(material.url) && (
                <p>
                  <a href={material.url} target="_blank" rel="noreferrer">
                    打开官方公告详情 ↗
                  </a>
                  {material.document_url && (
                    <>
                      {" "}
                      ·{" "}
                      <a
                        href={material.document_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开公告PDF ↗
                      </a>
                    </>
                  )}
                </p>
              )}
            <pre className="material-json">
              {JSON.stringify(
                visibleIndicatorData(material.data || material),
                null,
                2,
              )}
            </pre>
          </div>
        </Modal>
      )}
      <Warning>
        自动汇总用于研究整理，不会把资讯摘要冒充公告全集，也不会自动给出交易指令。缺少官方监管原文时明确说明。
      </Warning>
    </div>
  );
}
