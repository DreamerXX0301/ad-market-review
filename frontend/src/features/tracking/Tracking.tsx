import { useEffect, useState } from "react";
import { visibleIndicatorData } from "../../lib/indicatorVisibility";
import {
  Check,
  Clock3,
  Filter,
  RefreshCw,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import { api, list, patch, post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import {
  dateTime,
  num,
  percent,
  textValue,
  tone,
  statusText,
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
} from "../../components/UI";
import { fallbackFields, summarizeChecks } from "../strategies/Conditions";
import RuleTreeEditor from "../strategies/RuleTreeEditor";
import { validateVisualRule } from "../strategies/historicalRules";
import { policyRuleTree, policyGroupFromTree } from "./policyRuleTree";
import PolicyScope from "../strategies/PolicyScope";
import TrackingFilters from "./TrackingFilters";
import StrategyPicker from "./StrategyPicker";
import TrackingTabs from "./TrackingTabs";
import WatchlistButton from "../watchlist/WatchlistButton";
import {
  createStockNavigation,
  trackingEventBaseline,
} from "../../components/stockNavigation";
import type { StockNavigation } from "../../components/stockNavigation";
import SectorTracking from "../sector/SectorTracking";
import type { SectorMemberStockCallback } from "../sector/SectorMembersDialog";
import "../sector/SectorWorkspace.css";
export default function Tracking({
  onStock,
  onMemberStock,
  initialTargetType,
  initialStrategyId,
}: {
  onStock: (id: string, round: Row, navigation?: StockNavigation) => void;
  onMemberStock: SectorMemberStockCallback;
  initialTargetType?: string;
  initialStrategyId?: string;
}) {
  const [target, setTarget] = useState(
    initialTargetType === "sector" ? "sector" : "stock",
  );
  useEffect(() => {
    if (initialTargetType)
      setTarget(initialTargetType === "sector" ? "sector" : "stock");
  }, [initialTargetType, initialStrategyId]);
  return (
    <div className="page-stack">
      <div className="sector-target-tabs" role="tablist" aria-label="跟踪目标">
        <button
          role="tab"
          aria-selected={target === "stock"}
          className={target === "stock" ? "active" : ""}
          onClick={() => setTarget("stock")}
        >
          个股跟踪池
        </button>
        <button
          role="tab"
          aria-selected={target === "sector"}
          className={target === "sector" ? "active" : ""}
          onClick={() => setTarget("sector")}
        >
          行业 / 概念跟踪池
        </button>
      </div>
      {target === "stock" ? (
        <StockTracking onStock={onStock} />
      ) : (
        <SectorTracking
          onStock={onMemberStock}
          initialStrategyId={
            initialTargetType === "sector" ? initialStrategyId : undefined
          }
        />
      )}
    </div>
  );
}
import {
  matchesTrackingText,
  topicSortValue,
  trackingProgress,
  trackingTopicMatch,
} from "./trackingFilterLogic";
const policyFields = [
  ...fallbackFields,
  {
    field: "close_below_entry",
    label: "跌破入池事件日收盘",
    type: "boolean",
    operators: ["=="],
  },
  {
    field: "close_above_entry_high",
    label: "收盘价高于本轮事件日最高价",
    type: "boolean",
    category: "跟踪提醒",
    operators: ["=="],
  },
  {
    field: "close_below_entry_low",
    label: "收盘跌破本轮事件日最低价",
    type: "boolean",
    category: "跟踪状态",
    tracking_only: true,
    operators: ["=="],
  },
  {
    field: "tracking_change_pct",
    label: "跟踪涨幅 (%)",
    type: "number",
    operators: [">=", "<=", ">", "<"],
  },
  {
    field: "trading_age",
    label: "跟踪交易日",
    type: "number",
    operators: [">=", "<=", ">", "<"],
  },
];
function PolicyEditor({
  row,
  strategies,
  fields,
  topics,
  onClose,
  onSaved,
}: {
  row: Row;
  strategies: Row[];
  fields: Row[];
  topics: Row[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const strategy = strategies.find((s) => s.strategy_id === row.strategy_id);
  const initial = row.policy || strategy?.policy || {};
  const [duration, setDuration] = useState(initial.duration_sessions || 30),
    [alerts, setAlerts] = useState<Row[]>(initial.alerts || []),
    [exits, setExits] = useState<Row[]>(initial.exits || []),
    [alertLogic, setAlertLogic] = useState(initial.alert_logic || "OR"),
    [exitLogic, setExitLogic] = useState(initial.exit_logic || "OR"),
    [applyActive, setApplyActive] = useState(true),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const toast = useToast();
  const merged = [
    ...fields,
    ...policyFields.filter((f) => !fields.some((x) => x.field === f.field)),
  ];
  const alertFields = merged.map((field) =>
    ["close_above_entry_high", "close_below_entry_low"].includes(field.field)
      ? { ...field, hidden_in_builder: false }
      : field,
  );
  const exitFields = merged
    .filter((field) => field.field !== "close_above_entry_high")
    .map((field) =>
      field.field === "close_below_entry_low"
        ? { ...field, hidden_in_builder: false }
        : field,
    );
  async function save() {
    setBusy(true);
    setError("");
    try {
      for (const [conditions, logic, label] of [
        [alerts, alertLogic, "提醒"],
        [exits, exitLogic, "提前结束"],
      ] as const) {
        if (conditions.length) {
          const problem = validateVisualRule(policyRuleTree(conditions, logic));
          if (problem) throw new Error(`${label}条件：${problem}`);
        }
      }
      const policy = {
        ...initial,
        duration_sessions: Number(duration),
        alert_logic: alertLogic,
        alerts,
        exit_logic: exitLogic,
        exits,
        allow_entry_day_alerts: false,
        allow_entry_day_exits: false,
      };
      if (row.round_id)
        await patch(`/tracking-rounds/${row.round_id}/policy`, {
          policy,
          reason: reason || "用户调整后续核对政策",
        });
      else {
        const saved = await patch(`/strategies/${row.strategy_id}`, {
          policy,
          apply_to_active_tracking: applyActive,
        });
        toast(
          applyActive
            ? `默认政策已保存 · 更新 ${saved.policy_application?.applied_count ?? 0} 个轮次，保留 ${saved.policy_application?.skipped_custom_count ?? 0} 个自定义轮次`
            : "默认跟踪政策已保存，仅用于之后新入池轮次",
        );
      }
      if (row.round_id)
        toast("本轮政策已更新，入池基准保留；手动核对后判断提醒与结束");
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
      title={
        row.round_id
          ? `提醒与结束条件 · ${row.name || row.code}`
          : `默认跟踪政策 · ${strategy?.name || row.name}`
      }
      onClose={onClose}
      wide
    >
      <div className="modal-body">
        <label className="narrow-field">
          跟踪期限 · 市场交易日
          <input
            type="number"
            min={1}
            max={250}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          />
        </label>
        {!row.round_id && (
          <PolicyScope
            strategy={strategy}
            checked={applyActive}
            onChange={setApplyActive}
          />
        )}
        <div className="form-section-title">
          <h3>
            提醒条件 <small>触发后保留跟踪</small>
          </h3>
        </div>
        <RuleTreeEditor
          value={policyRuleTree(alerts, alertLogic)}
          onChange={(tree) => {
            const group = policyGroupFromTree(tree);
            setAlerts(group.conditions);
            setAlertLogic(group.logic);
          }}
          fields={alertFields}
          topics={topics}
          period={strategy?.period || "D"}
          allowTrackingAlerts
          allowTrackingConditions
          scope="alert"
        />
        <div className="button-group">
          {[
            { field: "macd_cross", label: "MACD 金叉" },
            { field: "kd_cross", label: "KDJ 金叉" },
          ].map((item) => (
            <button
              key={item.field}
              className="btn small"
              onClick={() =>
                setAlerts((current) =>
                  current.some((condition) => condition.field === item.field)
                    ? current
                    : [
                        ...current,
                        { field: item.field, op: "==", value: true },
                      ],
                )
              }
            >
              + {item.label}
            </button>
          ))}
          <button
            className="btn small"
            onClick={() =>
              setAlerts((current) =>
                current.some(
                  (condition) => condition.field === "close_above_entry_high",
                )
                  ? current
                  : [
                      ...current,
                      {
                        field: "close_above_entry_high",
                        op: "==",
                        value: true,
                      },
                    ],
              )
            }
          >
            + 收盘价高于本轮事件日最高价
          </button>
        </div>
        <div className="form-section-title">
          <h3>
            提前结束条件 <small>归入结束历史</small>
          </h3>
        </div>
        <RuleTreeEditor
          value={policyRuleTree(exits, exitLogic)}
          onChange={(tree) => {
            const group = policyGroupFromTree(tree);
            setExits(group.conditions);
            setExitLogic(group.logic);
          }}
          fields={exitFields}
          topics={topics}
          period={strategy?.period || "D"}
          allowTrackingConditions
          scope="exit"
        />
        <div className="button-group">
          {[
            { field: "macd_dead_cross", label: "MACD 死叉" },
            { field: "kd_dead_cross", label: "KDJ 死叉" },
          ].map((item) => (
            <button
              key={item.field}
              className="btn small"
              onClick={() =>
                setExits((current) =>
                  current.some((condition) => condition.field === item.field)
                    ? current
                    : [
                        ...current,
                        { field: item.field, op: "==", value: true },
                      ],
                )
              }
            >
              + {item.label}
            </button>
          ))}
          <button
            className="btn small"
            onClick={() =>
              setExits((v) => [
                ...v,
                { field: "close_below_entry", op: "==", value: true },
              ])
            }
          >
            + 跌破入池事件日价格
          </button>
          <button
            className="btn small"
            onClick={() =>
              setExits((current) =>
                current.some(
                  (condition) => condition.field === "close_below_entry_low",
                )
                  ? current
                  : [
                      ...current,
                      { field: "close_below_entry_low", op: "==", value: true },
                    ],
              )
            }
          >
            + 收盘跌破本轮事件日最低价
          </button>
        </div>
        <Warning>
          从入池后第 1
          个交易日核对。事件日价格与事件日最低价是不同基准：分别严格低于冻结日收盘、日K最低价时满足，相等不结束。比较使用同一价格口径；缺失或无法复权对齐时待核验。停牌计时，价格条件不填零。
        </Warning>
        {row.round_id && (
          <label>
            本次调整原因
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="便于之后审计，可选"
            />
          </label>
        )}
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button className="btn" onClick={onClose}>
          取消
        </button>
        <button className="btn primary" disabled={busy} onClick={save}>
          {busy ? <Spinner /> : <Check size={14} />}保存政策
        </button>
      </div>
    </Modal>
  );
}
function StockTracking({
  onStock,
}: {
  onStock: (id: string, round: Row, navigation?: StockNavigation) => void;
}) {
  const strategiesResult = useApi<Row>("/strategies"),
    fieldsResult = useApi<Row>("/strategy-fields"),
    topicsResult = useApi<Row>("/topics");
  const [strategyId, setStrategyId] = useState(""),
    [tab, setTab] = useState("active"),
    [triggered, setTriggered] = useState(false),
    [topicIds, setTopicIds] = useState<string[]>([]),
    [topicMatch, setTopicMatch] = useState<"any" | "all">("any"),
    [stockQuery, setStockQuery] = useState(""),
    [includeUnknown, setIncludeUnknown] = useState(false),
    [policy, setPolicy] = useState<Row | null>(null),
    [detail, setDetail] = useState<Row | null>(null),
    [remove, setRemove] = useState<Row | null>(null),
    [removeReason, setRemoveReason] = useState(""),
    [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const query = new URLSearchParams({
    status: tab === "active" ? "tracking" : "all",
    triggered_only: String(tab === "active" && triggered),
  });
  if (strategyId) query.set("strategy_id", strategyId);
  const result = useApi<Row>(`/tracking-rounds?${query}`);
  const eventsResult = useApi<Row>(
    `/tracking-events?limit=200${strategyId ? "&strategy_id=" + strategyId : ""}&event_type=alert`,
  );
  const topics = list(topicsResult.data);
  const completeTopics = new Set<string>(
    topics
      .filter((topic) => topic.members_complete === true && !topic.archived)
      .map((topic) => topic.topic_id),
  );
  const rounds = list(result.data);
  const byRound = new Map(rounds.map((round) => [round.round_id, round]));
  let records =
    tab === "alerts"
      ? list(eventsResult.data).map((event) => ({
          ...event,
          current_topics: byRound.get(event.round_id)?.current_topics,
          current_topics_observed_date: byRound.get(event.round_id)
            ?.current_topics_observed_date,
        }))
      : tab === "history"
        ? rounds.filter((round) => round.status !== "tracking")
        : rounds;
  records = records.filter((row) => matchesTrackingText(row, stockQuery));
  const checked = records.map((row) => ({
    ...row,
    _topic_filter_state: trackingTopicMatch(
      row,
      topicIds,
      topicMatch,
      completeTopics,
    ),
  }));
  const unknownCount = checked.filter(
    (row) => row._topic_filter_state === "unknown",
  ).length;
  const rows = checked.filter(
    (row) =>
      row._topic_filter_state === "matched" ||
      (includeUnknown && row._topic_filter_state === "unknown"),
  );
  const toast = useToast();
  async function review() {
    setBusy("review");
    setError("");
    try {
      const r = await post("/tracking-rounds/review", {
        ...(strategyId ? { strategy_id: strategyId } : {}),
        request_key: requestKey(),
      });
      toast(
        `核对完成 · ${r.reviewed ?? 0} 条 · 提醒 ${r.alerts ?? 0} · 结束 ${(r.ended ?? 0) + (r.expired ?? 0)}`,
      );
      result.reload();
      eventsResult.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function openDetail(row: Row) {
    try {
      setDetail(await api(`/tracking-rounds/${row.round_id}`));
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function doRemove() {
    if (!remove) return;
    setBusy("remove");
    try {
      await post(`/tracking-rounds/${remove.round_id}/remove`, {
        reason: removeReason || "手动移出",
        ignore_reentry: true,
      });
      toast("已移入结束历史，并忽略自动重新入池");
      setRemove(null);
      result.reload();
      eventsResult.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  const strategies = list(strategiesResult.data);
  const openStockFromList = (
    id: string,
    orderedRows: Row[],
    clickedRow: Row,
  ) => {
    const round = byRound.get(clickedRow.round_id) || clickedRow;
    const navigationRows = orderedRows.map((row) => ({
      ...row,
      eventBaseline: trackingEventBaseline(byRound.get(row.round_id) || row),
      historicalChecks: (byRound.get(row.round_id) || row).entry_basis
        ?.entry_checks,
    }));
    const navigation = createStockNavigation(
      navigationRows,
      {
        source: "tracking",
        scopeKey: JSON.stringify({
          tab,
          strategyId,
          triggered,
          topicIds,
          topicMatch,
          stockQuery,
          includeUnknown,
          businessDate: result.data?.business_date,
        }),
        label: `跟踪池 · ${tab === "alerts" ? "触发列表" : tab === "history" ? "结束历史" : "跟踪中"} · 当前筛选 · 已去重股票`,
        asOf: result.data?.business_date || undefined,
      },
      {
        ...clickedRow,
        eventBaseline: trackingEventBaseline(round),
        historicalChecks: round.entry_basis?.entry_checks,
      },
    );
    onStock(id, round, navigation);
  };
  const currentTopicCell = (value: unknown, row: Row) => (
    <div
      className="tracking-topic-cell"
      title={
        (result.data?.current_topics_scope ||
          "当前业务日有效归属，不是入池时历史归属") +
        (Array.isArray(value) && value.length
          ? "；" +
            value.map((topic: Row) => topic.name || topic.topic_id).join("、")
          : "")
      }
    >
      <div className="tags tracking-topic-preview">
        {Array.isArray(value) && value.length ? (
          value
            .slice(0, 2)
            .map((topic: Row) => (
              <Badge key={topic.topic_id}>{topic.name || topic.topic_id}</Badge>
            ))
        ) : (
          <span className="muted">暂无已确认归属</span>
        )}
      </div>
      {row._topic_filter_state === "unknown" && (
        <Badge tone="warn">筛选归属待核验</Badge>
      )}
      {Array.isArray(value) && value.length > 2 && (
        <details className="tracking-topic-more">
          <summary>+{value.length - 2} 项归属</summary>
          <div className="tags">
            {value.slice(2).map((topic: Row) => (
              <Badge key={topic.topic_id}>{topic.name || topic.topic_id}</Badge>
            ))}
          </div>
          <small>
            有效关系日 {row.current_topics_observed_date || "待核验"}
          </small>
        </details>
      )}
    </div>
  );
  const eventCondition = (row: Row) =>
    row.evidence?.alerts
      ? summarizeChecks(row.evidence.alerts, true)
      : textValue(
          row.condition ||
            row.evidence?.condition ||
            row.evidence?.reason ||
            row.message,
        );
  return (
    <div className="page-stack stock-tracking-workspace">
      <div className="section-intro">
        <p>
          每个策略独立跟踪 · 入池基准冻结 · 期限按策略或单股设置 ·
          提醒与结束各自判断
        </p>
        <div className="button-group">
          <button
            className="btn"
            disabled={!strategyId}
            onClick={() => setPolicy({ strategy_id: strategyId })}
          >
            <Settings2 size={14} />
            默认跟踪政策
          </button>
          <button className="btn primary" disabled={!!busy} onClick={review}>
            {busy === "review" ? <Spinner /> : <RefreshCw size={14} />}
            核对至当前行情日
          </button>
        </div>
      </div>
      <div className="stats-grid">
        <Stat label="当前列表" value={rows.length} />
        <Stat label="最新业务日" value={result.data?.business_date || "—"} />
        <Stat label="提醒记录" value={eventsResult.data?.total ?? 0} />
        <Stat
          label="计时口径"
          value="市场交易日"
          sub="入池当日为 0 · 停牌日计时"
        />
      </div>
      {error && <ErrorBox message={error} />}
      <Panel
        title="策略跟踪池"
        actions={
          <StrategyPicker
            value={strategyId}
            strategies={strategies}
            onChange={setStrategyId}
          />
        }
      >
        <div className="result-toolbar tracking-view-toolbar">
          <TrackingTabs
            value={tab}
            onChange={setTab}
            counts={{
              active: result.data?.counts?.active,
              alerts: eventsResult.data?.total,
              history: result.data?.counts?.history,
            }}
          />
          {tab === "active" && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={triggered}
                onChange={(e) => setTriggered(e.target.checked)}
              />
              <Filter size={13} />
              仅看触发个股
            </label>
          )}
        </div>
        <TrackingFilters
          topics={topics}
          selected={topicIds}
          onSelected={setTopicIds}
          match={topicMatch}
          onMatch={setTopicMatch}
          query={stockQuery}
          onQuery={setStockQuery}
          includeUnknown={includeUnknown}
          onIncludeUnknown={setIncludeUnknown}
          unknownCount={unknownCount}
          scope={result.data?.current_topics_scope}
        />
        {topicsResult.error && (
          <ErrorBox message={topicsResult.error} retry={topicsResult.reload} />
        )}
        {tab === "alerts" && (
          <div className="panel-footnote tracking-event-scope">
            <span>
              最近提醒 <strong>{list(eventsResult.data).length}</strong> /{" "}
              {eventsResult.data?.total ?? "—"} 条 · 当前匹配 {rows.length} 条
            </span>
            <span>
              今日涨幅按各条触发日计算；题材为当前归属。排序范围为已加载提醒。
            </span>
          </div>
        )}
        {result.error ? (
          <ErrorBox message={result.error} retry={result.reload} />
        ) : result.loading || (tab === "alerts" && eventsResult.loading) ? (
          <Loading />
        ) : tab === "alerts" && eventsResult.error ? (
          <ErrorBox message={eventsResult.error} retry={eventsResult.reload} />
        ) : tab === "alerts" ? (
          <Table
            rows={rows}
            rowKey="event_id"
            columns={[
              { key: "business_date", title: "触发日" },
              { key: "name" },
              {
                key: "today_change_pct",
                title: "今日涨幅",
                render: (value, row) => (
                  <span
                    className={tone(value)}
                    title={`触发日 ${row.valuation_date || row.business_date}，较前一市场交易日；缺失或不可比时待核验`}
                  >
                    {percent(value)}
                  </span>
                ),
              },
              { key: "strategy_name", title: "策略" },
              {
                key: "entry_business_date",
                title: "入池 / 事件基准",
                render: (value, row) => (
                  <div className="tracking-entry-cell">
                    <span>{value || "待核验"}</span>
                    <small>事件日收盘 {num(row.entry_price)}</small>
                  </div>
                ),
              },
              {
                key: "current_topics",
                title: "当前题材 / 行业",
                render: currentTopicCell,
                sortValue: topicSortValue,
              },
              { key: "event_type", render: (v) => <Status value={v} /> },
              {
                key: "condition",
                title: "触发条件",
                sortValue: eventCondition,
                render: (_, row) => (
                  <details className="tracking-condition-details">
                    <summary>
                      <span>{eventCondition(row)}</span>
                      <small>展开依据</small>
                    </summary>
                    <p>{eventCondition(row)}</p>
                  </details>
                ),
              },
              {
                key: "recorded_at_ms",
                title: "发现时间",
                render: (v) => dateTime(v),
              },
              {
                key: "actions",
                title: "自选",
                sortable: false,
                render: (_, row) => (
                  <WatchlistButton
                    instrumentId={row.instrument_id}
                    trackingRoundId={row.round_id}
                    sourceKind="tracking"
                    sourceName={row.strategy_name}
                    compact
                  />
                ),
              },
            ]}
            emptyTitle="还没有提醒触发"
            emptyDescription="先在跟踪轮次中设置提醒条件，再核对至最新行情日。"
            onStockNavigate={openStockFromList}
          />
        ) : (
          <Table
            rows={rows}
            rowKey="round_id"
            columns={[
              { key: "name" },
              { key: "strategy_name", title: "所属策略" },
              {
                key: "current_topics",
                title: "当前题材 / 行业",
                render: currentTopicCell,
                sortValue: topicSortValue,
              },
              {
                key: "today_change_pct",
                title: tab === "history" ? "结束日涨幅" : "今日涨幅",
                render: (value, row) => (
                  <span
                    className={tone(value)}
                    title={"可比行情日 " + (row.valuation_date || "待核验")}
                  >
                    {percent(value)}
                  </span>
                ),
              },
              {
                key: "tracking_change_pct",
                title: "跟踪涨幅",
                render: (v, r) => (
                  <span
                    className={tone(v)}
                    title={r.comparison_reason || "冻结入池基准，按同口径比较"}
                  >
                    {percent(v)}
                    {v == null && r.comparison_reason && (
                      <Badge tone="warn">待核验</Badge>
                    )}
                  </span>
                ),
              },
              {
                key: "trading_age",
                title: "进度",
                render: (_, r) => {
                  const progress = trackingProgress(r);
                  return (
                    <div className="tracking-age">
                      {progress.percentage === null ? (
                        <span className="muted">交易日进度待核验</span>
                      ) : (
                        <>
                          <span>
                            第 {progress.age} 日{" "}
                            <small>/ {progress.duration}</small>
                          </span>
                          <div>
                            <i style={{ width: progress.percentage + "%" }} />
                          </div>
                        </>
                      )}
                      {r.status === "tracking" &&
                        (r.duration_reached ? (
                          <Badge tone="warn">已到期限，待核对</Badge>
                        ) : r.policy_review_pending ? (
                          <small className="muted">当前政策待核对</small>
                        ) : null)}
                    </div>
                  );
                },
              },
              { key: "entry_business_date" },
              { key: "entry_price", title: "事件日收盘" },
              {
                key: "latest_event",
                title: "最新事件",
                sortValue: (row) => row.latest_event?.recorded_at_ms ?? null,
                render: (v, r) => (
                  <button className="link" onClick={() => openDetail(r)}>
                    {v
                      ? statusText[v.event_type] ||
                        textValue(v.event_type || v.message || v)
                      : "查看记录"}
                  </button>
                ),
              },
              { key: "status", render: (v) => <Status value={v} /> },
              {
                key: "actions",
                title: "操作",
                render: (_, r) => (
                  <div className="button-group">
                    <WatchlistButton
                      instrumentId={r.instrument_id}
                      trackingRoundId={r.round_id}
                      sourceKind="tracking"
                      sourceName={r.strategy_name}
                      compact
                    />
                    <button
                      className="icon-btn"
                      title="提醒与结束条件"
                      onClick={() => setPolicy(r)}
                    >
                      <Settings2 size={14} />
                    </button>
                    {r.status === "tracking" && (
                      <button
                        className="icon-btn danger-text"
                        title="移出跟踪"
                        onClick={() => {
                          setRemove(r);
                          setRemoveReason("");
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                    {r.status !== "tracking" && (
                      <button
                        className="btn small"
                        onClick={async () => {
                          try {
                            await api(
                              `/strategies/${r.strategy_id}/ignores/${r.instrument_id}`,
                              { method: "DELETE" },
                            );
                            toast("已解除忽略，下次真实命中后可重新入池");
                          } catch (e: any) {
                            setError(e.message);
                          }
                        }}
                      >
                        解除忽略
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
            pageSize={20}
            onStockNavigate={openStockFromList}
            emptyTitle={
              tab === "history" ? "暂无结束轮次" : "还没有跟踪中的股票"
            }
            emptyDescription="先在策略管理运行选股，再将命中结果加入对应跟踪池。"
          />
        )}
      </Panel>
      <Warning>
        页面的当前业务日来自已发布行情，不受盘面回看日期影响。连续浏览按当前筛选、排序后的已加载股票去重，行情观察日固定为当前业务日；每只股票使用自身轮次的事件价线。跟踪结束只是记录状态变化，不代表真实卖出。
      </Warning>
      {policy && (
        <PolicyEditor
          row={policy}
          strategies={strategies}
          fields={list(fieldsResult.data)}
          topics={list(topicsResult.data)}
          onClose={() => setPolicy(null)}
          onSaved={() => {
            result.reload();
            strategiesResult.reload();
            eventsResult.reload();
          }}
        />
      )}
      {detail && (
        <Modal
          title={`跟踪事件 · ${detail.name || detail.instrument_id}`}
          wide
          onClose={() => setDetail(null)}
        >
          <div className="modal-body">
            <div className="stats-grid">
              <Stat label="入池日" value={detail.entry_business_date} />
              <Stat
                label="事件日收盘"
                value={num(detail.entry_price)}
                sub={`跟踪涨幅 ${percent(detail.tracking_change_pct)}`}
              />
              <Stat
                label="事件日最高价"
                value={
                  <span
                    title={
                      detail.entry_high_reason ||
                      `冻结事件日 ${detail.entry_high_date || detail.entry_business_date} 的未复权日K最高价，不是跟踪期间累计最高价`
                    }
                  >
                    {num(detail.entry_high)}
                  </span>
                }
              />
              <Stat
                label="事件日最低价"
                value={
                  <span
                    title={
                      detail.entry_low_reason ||
                      `冻结事件日 ${detail.entry_low_date || detail.entry_business_date} 的未复权日K最低价，不是事件日收盘或跟踪期间最低价`
                    }
                  >
                    {num(detail.entry_low)}
                  </span>
                }
              />
            </div>
            <div className="panel-footnote">
              入池归属快照（
              {detail.topics_snapshot_date || detail.entry_business_date}）：
              {textValue(
                detail.topics_snapshot ?? detail.entry_basis?.entry_topics,
              )}
              。当前筛选使用当前业务日关系，入池快照与历史判断保留。
            </div>
            <Table
              rows={detail.events || []}
              columns={[
                { key: "business_date" },
                { key: "event_type", render: (v) => <Status value={v} /> },
                { key: "trading_age" },
                {
                  key: "outcome",
                  title: "判断结果",
                  render: (value) => <Status value={value} />,
                },
                {
                  key: "evidence",
                  title: "依据",
                  render: (v) => (
                    <details className="evidence-details">
                      <summary>
                        {v?.comparison_reason || "查看指标与判断依据"}
                      </summary>
                      <pre className="material-json">
                        {JSON.stringify(visibleIndicatorData(v), null, 2)}
                      </pre>
                    </details>
                  ),
                },
              ]}
              pageSize={12}
            />
          </div>
        </Modal>
      )}
      {remove && (
        <Modal title="移出跟踪池" onClose={() => setRemove(null)}>
          <div className="modal-body">
            <p>结束「{remove.name}」本轮跟踪，记录和历史事件会保留。</p>
            <label>
              移出原因
              <input
                value={removeReason}
                onChange={(e) => setRemoveReason(e.target.value)}
                placeholder="例如：暂不继续观察"
              />
            </label>
            <Warning>
              同时设置忽略，后续选股不会悄悄重新加入。可以在结束历史中解除。
            </Warning>
          </div>
          <div className="modal-footer">
            <button className="btn" onClick={() => setRemove(null)}>
              取消
            </button>
            <button
              className="btn danger"
              disabled={busy === "remove"}
              onClick={doRemove}
            >
              确认移出
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
