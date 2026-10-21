import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FileSearch, Info, RefreshCw } from "lucide-react";
import { post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, integer } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Panel,
  Spinner,
} from "../../components/UI";
import { datasetCoverageView } from "./dataPresentation";
import ListingIdentityPanel from "./ListingIdentityPanel";
import GapDiagnosisPanel, { GapDiagnosisOverview } from "./GapDiagnosisPanel";
import GapSourceCheck from "./GapSourceCheck";
import "./CoveragePanel.css";

export function corporateActionSummary(actions?: Row) {
  if (!actions) return "可核验窗口内字段与来源全文件特殊行动";
  if (actions.window_ready)
    return `本窗口${integer(actions.window_rows)}条字段通过${actions.outside_window_special_rows ? `；另有窗口外${integer(actions.outside_window_special_rows)}条特殊行动需单独核验` : ""}`;
  return actions.window_invalid_rows
    ? `本窗口${integer(actions.window_invalid_rows)}条行动需单独核验`
    : actions.reason;
}

export function CorporateActionScope({ actions }: { actions?: Row }) {
  if (!actions)
    return <p>先运行本地核验，即可分别查看当前窗口与来源全文件的字段情况。</p>;
  const items = [
    ...(actions.invalid_items || []),
    ...(actions.special_items || []),
  ];
  return (
    <>
      <h3>窗口内结构核验</h3>
      <p>{corporateActionSummary(actions)}。</p>
      <p className="muted">
        {actions.window_begin} → {actions.window_end}
        ；窗口字段核验与来源全文件适用性分别判断。
      </p>
      <h3>来源全文件特殊行动</h3>
      <p>
        来源全文件保留 {integer(actions.file_rows)} 条记录；
        {actions.file_invalid_rows
          ? `${integer(actions.file_invalid_rows)} 条需单独核验`
          : "本地字段已验"}
        。特殊负送转字段可能涉及缩股等行动，保持来源原值，不自动套用当前复权模型；这不等同于当前窗口记录损坏。
      </p>
      {items.length > 0 && (
        <div className="coverage-gap-list">
          <table className="coverage-table">
            <thead>
              <tr>
                <th>股票 / 日期</th>
                <th>范围</th>
                <th>核验说明</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item: Row, index: number) => (
                <tr key={`${item.instrument_id}:${item.date}:${index}`}>
                  <td>
                    <strong>{item.code || item.instrument_id}</strong>
                    <small>{item.date || "日期待核验"}</small>
                  </td>
                  <td>
                    {item.scope === "outside_window"
                      ? "本窗口之外"
                      : item.scope === "window"
                        ? "当前窗口"
                        : "日期范围待核验"}
                  </td>
                  <td>{item.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {actions.items_truncated && (
        <p className="muted">仅列出前20条；统计总数覆盖完整来源文件。</p>
      )}
      <p>
        来源没有提供全市场应有事件总量，因此不计算完整百分比。证券没有返回事件，也不能仅据此断言从未发生公司行动。
      </p>
    </>
  );
}

export default function CoveragePanel({
  datasets,
  audit,
  loading,
  disabled,
  remoteConfigured,
  onReload,
  onTask,
}: {
  datasets: Row[];
  audit?: Row;
  loading: boolean;
  disabled: boolean;
  remoteConfigured: boolean;
  onReload: () => void;
  onTask: (task: Row) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [opened, setOpened] = useState<Row | null>(null);
  const details = useApi<Row>(opened ? "/data/coverage/check" : null);
  const report = audit?.report,
    full = details.data?.report;
  const auditKey = `${audit?.plan_id}:${audit?.stale}:${audit?.report?.checked_at_ms}`;
  const previousAudit = useRef(auditKey);
  useEffect(() => {
    if (previousAudit.current !== auditKey) {
      previousAudit.current = auditKey;
      if (opened) details.reload();
    }
  }, [auditKey, opened?.module, details.reload]);
  const isRepair = opened?.module === "repair";
  const currentPlan =
    !!full && details.data?.plan_id === audit?.plan_id && !details.data?.stale;
  const repair = full?.repair_plan;
  async function checkLocal() {
    setBusy(true);
    setError("");
    try {
      onTask(
        await post("/data/coverage/check", {
          plan_id: audit?.plan_id,
          request_key: requestKey(),
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function startRepair() {
    if (!currentPlan || !repair) return;
    setBusy(true);
    setError("");
    try {
      onTask(
        await post(repair.endpoint, {
          ...repair.payload,
          coverage_plan_id: details.data?.plan_id,
          request_key: requestKey(),
        }),
      );
      setOpened(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function open(row: Row) {
    setOpened(row);
    setError("");
  }
  function localState(module: string) {
    if (!report)
      return audit?.stale ? "数据更新后需重验" : "可核验本地文件与字段";
    if (module === "corporate_actions")
      return corporateActionSummary(report.corporate_actions);
    if (["daily_raw", "analysis_bars"].includes(module))
      return module === "analysis_bars" && report.daily?.ready
        ? "分析行情沿用日K输入；共用同一份逐股缺口诊断，不重复计缺口"
        : module === "daily_raw" &&
            report.file_checks?.some(
              (item: Row) =>
                item.role === "raw-daily.parquet" &&
                (item.sha256_verified !== true ||
                  item.schema_verified !== true),
            )
          ? "原始文件或字段未通过核验，请查看详情"
          : report.daily?.ready
            ? `${integer(report.daily.known_stock_count)}只已知上市范围 · ${integer(report.daily.unquoted_sessions)}个证券交易日无报价`
            : report.daily?.reason;
    if (module === "security_identity")
      return "总名录保留待上市；有依据的待上市单列，未知身份仍计入核验分母";
    if (module === "indicators") return "按日 / 周 / 月及各指标预热规则生成";
    return "比例仅表示本行注明范围，不证明完整历史全集";
  }
  return (
    <>
      <Panel
        title="数据覆盖"
        subtitle="查看可证明的范围与缺口；本地核验不联网，补拉前先确认范围"
        actions={
          <div className="coverage-actions">
            <button
              className="btn small"
              disabled={busy || disabled || !audit?.available}
              onClick={checkLocal}
            >
              {busy ? <Spinner /> : <FileSearch size={14} />}核验本地数据
            </button>
            <button
              className="icon-btn"
              aria-label="刷新数据覆盖"
              onClick={onReload}
            >
              <RefreshCw size={14} />
            </button>
          </div>
        }
      >
        {error && !opened && <ErrorBox message={error} />}
        <div className="coverage-audit-line">
          {report && !report.failures?.length && !report.notices?.length ? (
            <CheckCircle2 size={14} />
          ) : (
            <Info size={14} />
          )}
          <span>
            {report
              ? `最近本地核验 ${dateTime(report.checked_at_ms)}${report.failures?.length ? ` · ${report.failures.length}项文件/结构问题需核验` : ""}${report.notices?.length ? " · 有特殊行动需单独核验" : ""}`
              : audit?.stale
                ? "数据已有更新，请重新核验本地文件与逐股报价"
                : "尚未运行本地核验；来源未声明的总量不会估算为100%"}
          </span>
          {report && (
            <button
              className="link"
              onClick={() => open({ module: "repair", name: "缺口与补拉计划" })}
            >
              缺口处理计划
            </button>
          )}
        </div>
        {loading ? (
          <Loading />
        ) : !datasets.length ? (
          <Empty
            title="尚无已发布数据集"
            description="完成行情初始化后，可在此核验留存文件与实际覆盖范围。"
          />
        ) : (
          <div className="coverage-table-wrap">
            <table className="coverage-table">
              <thead>
                <tr>
                  <th>数据模块</th>
                  <th>当前留存</th>
                  <th>可核验范围</th>
                  <th>说明与操作</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((dataset) => {
                  const coverage = datasetCoverageView(dataset);
                  const daily = ["daily_raw", "analysis_bars"].includes(
                    dataset.module,
                  );
                  return (
                    <tr key={dataset.module}>
                      <td>
                        <strong>{dataset.name}</strong>
                        <small>
                          {dataset.first_date === dataset.last_date
                            ? dataset.last_date
                            : `${dataset.first_date} → ${dataset.last_date}`}
                        </small>
                      </td>
                      <td className="coverage-retained">
                        {dataset.module === "indicators" ? (
                          <>
                            <b>日 / 周 / 月</b>
                            <small>按需计算，不重复存整库指标</small>
                          </>
                        ) : (
                          <>
                            <b>
                              {integer(
                                dataset.module === "security_identity"
                                  ? (coverage.catalogCount ??
                                      report?.identities?.catalog_count ??
                                      report?.identities?.stock_count ??
                                      (coverage.pendingListingCount === null
                                        ? coverage.expected
                                        : null))
                                  : dataset.row_count,
                              )}
                            </b>
                            <small>
                              {dataset.module === "calendar"
                                ? "已留存交易日"
                                : dataset.module === "topics"
                                  ? "当前成员关系"
                                  : dataset.module === "security_identity"
                                    ? "已收录股票 · 含待上市，剔除指数"
                                    : "留存记录"}
                            </small>
                          </>
                        )}
                      </td>
                      <td
                        className={
                          coverage.displayState === "covered"
                            ? "coverage-measure-covered"
                            : undefined
                        }
                      >
                        <div className="coverage-measure-line">
                          <span>{coverage.label}</span>
                          {coverage.percentText && (
                            <b>{coverage.percentText}</b>
                          )}
                        </div>
                        {coverage.known ? (
                          <>
                            <div
                              className="coverage-mini-meter"
                              role="progressbar"
                              aria-label={coverage.label}
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={coverage.percent!}
                            >
                              <i style={{ width: `${coverage.percent}%` }} />
                            </div>
                            <small>
                              {integer(coverage.observed)} /{" "}
                              {integer(coverage.expected)} {coverage.unit}
                            </small>
                            {coverage.displayState === "covered" && (
                              <small className="coverage-complete-state">
                                <CheckCircle2 size={12} aria-hidden="true" />
                                当前范围已覆盖
                              </small>
                            )}
                          </>
                        ) : (
                          <small>
                            {coverage.displayState === "on_demand"
                              ? "各项预热完成后才有值，不以行情条数计算完整率"
                              : coverage.reason}
                          </small>
                        )}
                      </td>
                      <td>
                        {dataset.module === "security_identity" &&
                          coverage.pendingListingCount !== null && (
                            <small className="coverage-identity-counts">
                              待上市 {integer(coverage.pendingListingCount)} 只
                              · 独立列示，不计入当期分母
                              {coverage.unknownIdentityCount
                                ? `；另有 ${integer(coverage.unknownIdentityCount)} 只待核验，仍保留在分母`
                                : ""}
                            </small>
                          )}
                        <span className="coverage-row-note">
                          {localState(dataset.module)}
                        </span>
                        <button
                          className="link"
                          onClick={() =>
                            open(
                              daily
                                ? {
                                    module: "quote_gaps",
                                    name: "日K与分析行情 · 共用缺口诊断",
                                  }
                                : dataset,
                            )
                          }
                        >
                          {daily && report?.daily?.ready
                            ? "查看共用诊断"
                            : dataset.module === "security_identity"
                              ? "查看上市身份"
                              : "查看口径"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {report?.daily?.gap_diagnosis && (
          <GapDiagnosisOverview
            diagnosis={report.daily.gap_diagnosis}
            onOpen={() =>
              open({
                module: "quote_gaps",
                name: "日K与分析行情 · 共用缺口诊断",
              })
            }
          />
        )}
      </Panel>
      {opened && (
        <Modal
          title={opened.name || "数据核验说明"}
          wide
          onClose={() => setOpened(null)}
        >
          <div className="coverage-dialog">
            {error && <ErrorBox message={error} />}
            {details.loading && !details.data ? (
              <Loading />
            ) : details.error ? (
              <ErrorBox message={details.error} retry={details.reload} />
            ) : (
              <>
                {!!opened.coverage_progress && (
                  <div className="coverage-scope-note">
                    <Info size={16} />
                    <p>
                      {opened.coverage_progress.basis}
                      <br />
                      {opened.coverage_progress.reason}
                    </p>
                  </div>
                )}
                {isRepair ? (
                  <>
                    <h3>本地核验与远端补拉分开处理</h3>
                    <ul className="coverage-guidance">
                      <li>
                        <strong>上市日期</strong>：来源尚未提供{" "}
                        {integer(full?.identities?.unknown?.length)}{" "}
                        只股票的有效日期；不能以首根报价或股票名称猜测。
                      </li>
                      <li>
                        <strong>日K无报价</strong>：
                        {integer(full?.daily?.unquoted_sessions)}{" "}
                        个证券交易日尚未出现有效报价。可先查逐股日期并与可用来源核对；来源一致也不能据此判定停牌。
                      </li>
                      <li>
                        <strong>公司行动</strong>
                        ：本地文件与字段可核验；来源未声明全市场总事件量，补拉后也不能自动判为完整。
                      </li>
                    </ul>
                    <h3>
                      盘面来源补拉计划 ·{" "}
                      {repair?.target_date || audit?.target_date}
                    </h3>
                    {repair?.missing_batches?.length ? (
                      <div className="coverage-missing-pools">
                        {repair.missing_batches.map((item: Row) => (
                          <Badge key={item.date + item.pool_kind}>
                            {item.date} ·{" "}
                            {item.pool_kind === "limitdown"
                              ? "跌停池"
                              : "涨停池"}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <p className="muted">
                        当前年度没有完全缺失的来源批次；不完整分页、隔离项等仍按来源证据保留。
                      </p>
                    )}
                    <ul className="coverage-guidance">
                      {(repair?.scope || []).map((item: string) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                    <p className="coverage-plan-caveat">
                      {repair?.reason ||
                        "请先完成当前数据的本地核验，再查看明确补拉范围。"}
                    </p>
                    <button
                      className="btn primary"
                      disabled={
                        busy || disabled || !remoteConfigured || !currentPlan
                      }
                      onClick={startRepair}
                    >
                      {busy ? <Spinner /> : <RefreshCw size={14} />}
                      按此范围修补盘面来源
                    </button>
                    {!remoteConfigured && (
                      <p className="muted">
                        需先配置并启用来源，才可创建联网补充任务。
                      </p>
                    )}
                  </>
                ) : ["daily_raw", "analysis_bars", "quote_gaps"].includes(
                    opened.module,
                  ) ? (
                  <>
                    <GapSourceCheck
                      disabled={disabled}
                      remoteConfigured={remoteConfigured}
                      onTask={onTask}
                      planKey={auditKey}
                      onChanged={() => {
                        details.reload();
                        onReload();
                      }}
                    />
                    <GapDiagnosisPanel
                      daily={full?.daily}
                      stale={details.data?.stale === true}
                    />
                  </>
                ) : opened.module === "security_identity" ? (
                  <ListingIdentityPanel
                    disabled={disabled}
                    onSaved={() => {
                      details.reload();
                      onReload();
                    }}
                  />
                ) : opened.module === "corporate_actions" ? (
                  <CorporateActionScope actions={full?.corporate_actions} />
                ) : (
                  <p>{opened.description}</p>
                )}
                {!!full?.failures?.length && (
                  <ErrorBox message={full.failures.join("；")} />
                )}
                {opened.snapshot_quality_note && (
                  <p className="coverage-original-quality">
                    {opened.snapshot_quality_note}
                  </p>
                )}
              </>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
