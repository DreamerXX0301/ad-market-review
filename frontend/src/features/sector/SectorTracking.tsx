import { useEffect, useState } from "react";
import { RefreshCw, Settings2, Trash2, Users } from "lucide-react";
import { list, post, requestKey, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime, num, percent, tone } from "../../lib/format";
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
import SectorChartDialog from "../market/SectorChartDialog";
import SectorEvidence from "./SectorEvidence";
import SectorMembersDialog from "./SectorMembersDialog";
import type { SectorMemberStockCallback } from "./SectorMembersDialog";
import SectorPolicyEditor from "./SectorPolicyEditor";
import { sectorKindLabel } from "./sectorPresentation";
import TrackingTabs from "../tracking/TrackingTabs";
import "./SectorWorkspace.css";

export default function SectorTracking({
  onStock,
  initialStrategyId,
}: {
  onStock: SectorMemberStockCallback;
  initialStrategyId?: string;
}) {
  const strategies = useApi<Row>("/sector-strategies"),
    fields = useApi<Row>("/sector-strategy-fields");
  const [strategyId, setStrategyId] = useState(initialStrategyId || ""),
    [tab, setTab] = useState("tracking"),
    [triggered, setTriggered] = useState(false),
    [kind, setKind] = useState("all"),
    [query, setQuery] = useState(""),
    [members, setMembers] = useState<Row | null>(null),
    [chart, setChart] = useState<Row | null>(null),
    [policy, setPolicy] = useState<Row | null>(null),
    [remove, setRemove] = useState<Row | null>(null),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    if (initialStrategyId) setStrategyId(initialStrategyId);
  }, [initialStrategyId]);
  const params = new URLSearchParams({
    status: tab === "ended" ? "ended" : tab === "alerts" ? "all" : "tracking",
    triggered_only: String(tab === "tracking" && triggered),
  });
  if (strategyId) params.set("strategy_id", strategyId);
  const result = useApi<Row>(`/sector-tracking-rounds?${params}`),
    events = useApi<Row>(
      `/sector-tracking-events?event_type=alert&limit=500${strategyId ? `&strategy_id=${encodeURIComponent(strategyId)}` : ""}`,
    );
  const date = result.data?.business_date || "",
    rounds = list(result.data),
    byRound = new Map(rounds.map((round) => [round.round_id, round]));
  const rows = (
    tab === "alerts"
      ? list(events.data).map((event) => ({
          ...byRound.get(event.round_id),
          ...event,
          // A saved alert's quote belongs to its trigger day, never today's
          // mutable round view. Missing historical proof stays unknown.
          today_change_pct:
            event.evidence?.metrics_by_period?.D?.change_pct ?? null,
          current_price: event.evidence?.metrics_by_period?.D?.close ?? null,
          latest_price: event.evidence?.metrics_by_period?.D?.close ?? null,
          tracking_change_pct:
            event.evidence?.metrics_by_period?.D?.tracking_change_pct ?? null,
        }))
      : rounds
  ).filter(
    (row) =>
      (kind === "all" || row.sector_kind === kind) &&
      (!query ||
        `${row.name} ${row.topic_code} ${row.strategy_name}`
          .toLowerCase()
          .includes(query.toLowerCase())),
  );
  const toast = useToast();
  function refresh() {
    result.reload();
    events.reload();
    strategies.reload();
  }
  async function review() {
    setBusy("review");
    setError("");
    try {
      const data = await post("/sector-tracking-rounds/review", {
        request_key: requestKey(),
        ...(strategyId ? { strategy_id: strategyId } : {}),
      });
      toast(data.message || "板块跟踪已复核");
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function removeRound() {
    if (!remove) return;
    setBusy("remove");
    setError("");
    try {
      await post(`/sector-tracking-rounds/${remove.round_id}/remove`, {
        reason: reason.trim() || "手动移除",
      });
      setRemove(null);
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="page-stack sector-workspace">
      <div className="section-intro">
        <p>
          跟踪板块指数自身 · 行情观察日 {date || "待核验"} · 成分股仅展开查看
        </p>
        <button className="btn primary" disabled={!!busy} onClick={review}>
          {busy === "review" ? <Spinner /> : <RefreshCw size={14} />}
          复核板块跟踪
        </button>
      </div>
      <Panel
        title="板块策略跟踪池"
        actions={
          <button className="btn small" onClick={refresh}>
            <RefreshCw size={13} />
            刷新
          </button>
        }
      >
        <div className="result-toolbar tracking-view-toolbar">
          <TrackingTabs
            value={tab}
            onChange={setTab}
            activeKey="tracking"
            historyKey="ended"
            counts={{
              active: result.data?.counts?.active,
              alerts: events.data?.total,
              history: result.data?.counts?.history,
            }}
          />
          <div className="button-group">
            <select
              aria-label="板块跟踪策略"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            >
              <option value="">全部板块策略</option>
              {list(strategies.data).map((strategy) => (
                <option key={strategy.strategy_id} value={strategy.strategy_id}>
                  {strategy.name}
                </option>
              ))}
            </select>
            <select
              aria-label="板块跟踪分类"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="all">全部板块</option>
              <option value="industry">行业</option>
              <option value="concept">概念</option>
            </select>
            <input
              aria-label="搜索跟踪板块"
              placeholder="板块名称 / 代码"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {tab === "tracking" && (
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={triggered}
                  onChange={(e) => setTriggered(e.target.checked)}
                />
                只看触发提醒
              </label>
            )}
          </div>
        </div>
        {result.error && (
          <ErrorBox message={result.error} retry={result.reload} />
        )}{" "}
        {fields.error && (
          <ErrorBox message={fields.error} retry={fields.reload} />
        )}{" "}
        {tab === "alerts" && events.error && (
          <ErrorBox message={events.error} retry={events.reload} />
        )}{" "}
        {error && <ErrorBox message={error} />}
        {result.loading || (tab === "alerts" && events.loading) ? (
          <Loading />
        ) : rows.length ? (
          <Table
            key={`${tab}-${strategyId}-${kind}-${query}-${triggered}`}
            rows={rows}
            rowKey={tab === "alerts" ? "event_id" : "round_id"}
            columns={[
              {
                key: "name",
                title: "板块",
                render: (_, row) => (
                  <button
                    className="table-link sector-asset-name"
                    disabled={!date || !row.topic_code}
                    onClick={() => setChart({ ...row, date })}
                  >
                    <strong>{row.name}</strong>
                    <small>
                      {row.topic_code} · {sectorKindLabel(row.sector_kind)}
                    </small>
                  </button>
                ),
              },
              { key: "strategy_name", title: "策略" },
              { key: "entry_business_date", title: "入池日" },
              {
                key: "entry_price",
                title: "入池点位",
                render: (value) => num(value),
              },
              {
                key: "current_price",
                title: "最新点位",
                sortValue: (row) => row.current_price ?? row.latest_price,
                render: (value, row) => num(value ?? row.latest_price),
              },
              {
                key: "today_change_pct",
                title: "观察日涨幅",
                render: (value) => (
                  <span className={tone(value)}>{percent(value)}</span>
                ),
              },
              {
                key: "tracking_change_pct",
                title: "跟踪涨幅",
                render: (value) => (
                  <span className={tone(value)}>{percent(value)}</span>
                ),
              },
              {
                key: "trading_age",
                title: "跟踪进度",
                render: (value, row) => (
                  <span>
                    {value == null ? "待核验" : `第${value}日`} /{" "}
                    {row.duration_sessions ??
                      row.policy?.duration_sessions ??
                      "—"}
                    日
                  </span>
                ),
              },
              {
                key: "status",
                title: "状态",
                render: (value) => <Status value={value} />,
              },
              {
                key: "latest_event",
                title: tab === "alerts" ? "触发依据" : "最新事件",
                sortable: false,
                render: (value, row) => (
                  <div>
                    {tab === "alerts" ? (
                      <>
                        <Badge>
                          {row.event_type === "alert" ? "提醒" : row.event_type}
                        </Badge>
                        <small>
                          {dateTime(row.business_date || row.created_at)}
                        </small>
                      </>
                    ) : value ? (
                      <span>
                        {value.label ||
                          value.reason ||
                          value.event_type ||
                          value.type ||
                          "已记录"}
                      </span>
                    ) : (
                      <span className="muted">暂无事件</span>
                    )}
                    <SectorEvidence
                      checks={
                        row.checks ||
                        row.evidence?.alerts?.checks ||
                        row.evidence?.exits?.checks ||
                        row.evidence?.checks ||
                        value?.evidence?.alerts?.checks ||
                        value?.checks
                      }
                      fields={list(fields.data)}
                    />
                  </div>
                ),
              },
              {
                key: "_actions",
                title: "操作",
                sortable: false,
                render: (_, row) => (
                  <div className="button-group">
                    <button
                      className="btn small"
                      disabled={!date || !row.round_id}
                      onClick={() => setMembers(row)}
                    >
                      <Users size={12} />
                      成分股
                    </button>
                    <button
                      className="icon-btn"
                      disabled={!row.round_id || !list(fields.data).length}
                      title="设置板块提醒与结束条件"
                      onClick={() => setPolicy(row)}
                    >
                      <Settings2 size={14} />
                    </button>
                    {row.status === "tracking" && (
                      <button
                        className="icon-btn danger-text"
                        title="移除板块跟踪"
                        onClick={() => {
                          setRemove(row);
                          setReason("");
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        ) : (
          <Empty
            title={
              tab === "alerts"
                ? "暂无板块触发提醒"
                : tab === "ended"
                  ? "暂无已结束板块"
                  : "暂无跟踪板块"
            }
            description="在板块策略运行结果中勾选满足条件的板块加入跟踪。"
          />
        )}
        <div className="panel-footnote">
          板块跟踪与个股跟踪独立。入池第0日默认不提醒、不提前结束；打开成分股不会自动生成个股跟踪。
        </div>
      </Panel>
      {members && (
        <SectorMembersDialog
          round={members}
          date={date}
          onClose={() => setMembers(null)}
          onStock={onStock}
        />
      )}
      {chart && (
        <SectorChartDialog
          topic={{
            topicCode: chart.topic_code,
            name: chart.name,
            proxy: false,
            notice: "同花顺板块指数",
          }}
          date={chart.date}
          onClose={() => setChart(null)}
        />
      )}
      {policy && (
        <SectorPolicyEditor
          round={policy}
          fields={list(fields.data)}
          period={
            policy.entry_period ||
            list(strategies.data).find(
              (strategy) => strategy.strategy_id === policy.strategy_id,
            )?.period ||
            "D"
          }
          onClose={() => setPolicy(null)}
          onSaved={refresh}
        />
      )}
      {remove && (
        <Modal
          title={`移除板块跟踪 · ${remove.name}`}
          onClose={() => setRemove(null)}
        >
          <div className="modal-body">
            <Warning>
              仅结束这个板块跟踪轮次，保留历史记录；不会修改个股池或其他轮次。
            </Warning>
            <label>
              结束原因
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="手动移除"
              />
            </label>
          </div>
          <div className="modal-footer">
            <button className="btn" onClick={() => setRemove(null)}>
              取消
            </button>
            <button
              className="btn danger"
              disabled={!!busy}
              onClick={removeRound}
            >
              {busy === "remove" && <Spinner />}确认移除
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
