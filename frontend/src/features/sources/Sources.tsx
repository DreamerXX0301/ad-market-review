import { lazy, Suspense, useEffect, useState } from "react";
import {
  Check,
  Eye,
  EyeOff,
  LockKeyhole,
  Settings2,
  ShieldCheck,
  XCircle,
  CircleHelp,
  ChevronRight,
  Database,
  Layers3,
} from "lucide-react";
import { list, post, useApi } from "../../lib/api";
import type { Row } from "../../lib/api";
import { dateTime } from "../../lib/format";
import {
  capabilityCounts,
  capabilityState,
  capabilityStateLabels,
  sourceCapabilities,
} from "./sourcePresentation";
import "./Sources.css";
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
  Warnings,
} from "../../components/UI";
const capLabels: Row = {
  adjustment: "复权与除权",
  minutes: "分钟K",
  level2: "五档盘口",
  financial: "财务",
  bulk: "全市场批量",
  daily_raw: "个股日K",
  daily_k: "日K",
  daily: "日K",
  index: "指数",
  index_daily: "指数日K",
  calendar: "交易日历",
  daily_batch: "批量日K",
  corporate_actions: "企业行动",
  adjustment_factors: "复权因子",
  factors: "复权因子",
  topics: "行业题材",
  topic: "行业题材",
  current_members: "当前成员",
  financials: "财务",
  realtime: "实时行情",
  minute: "分钟",
  depth: "五档",
  chips: "筹码",
  news: "资讯摘要",
  announcements: "公告原文",
  limit_up: "涨停池",
  ladder: "连板梯队",
  abnormal: "异动监管",
};
function sourceSetup(source: Row) {
  const id = source.source_id || source.id;
  return {
    requiresKey: source.requires_key ?? id !== "tickflow-public",
    label:
      id === "fuyao"
        ? "注册免费 · 需 Key"
        : id === "tickflow-public"
          ? "免费 · 免 Key"
          : "账号 Key · 按实际权限",
    note:
      source.configuration_note ||
      (id === "fuyao"
        ? "扶摇使用免费注册 Key，无需选择付费套餐。各项能力分别验证。"
        : id === "tickflow-public"
          ? "公共历史服务免费使用，不需要配置 Key。"
          : "免费与付费账号都填写自己的 Key，权限以实际接口探测为准。"),
  };
}
function SourceEditor({
  source,
  onClose,
  onSaved,
}: {
  source: Row;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [key, setKey] = useState(""),
    [show, setShow] = useState(false),
    [active, setActive] = useState(source.active === true),
    [enabled, setEnabled] = useState(source.enabled !== false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const toast = useToast();
  const setup = sourceSetup(source);
  async function save() {
    setBusy(true);
    setError("");
    try {
      await post(`/sources/${source.source_id || source.id}/configure`, {
        enabled,
        active,
        ...(key.trim() ? { api_key: key.trim() } : {}),
      });
      setKey("");
      toast("配置已保存，可点击验证能力查看逐项结果");
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e.message);
      setKey("");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`配置 ${source.name}`}
      onClose={() => {
        setKey("");
        onClose();
      }}
    >
      <div className="modal-body">
        <Badge>{setup.label}</Badge>
        <p>{setup.note}</p>
        {setup.requiresKey && (
          <label>
            API Key{" "}
            <span className="muted">
              {source.configured ? "已配置，留空则保留" : ""}
            </span>
            <div className="secret-input">
              <input
                autoComplete="off"
                spellCheck={false}
                type={show ? "text" : "password"}
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  source.configured
                    ? "留空保留现有 Key"
                    : "输入来源提供的 API Key"
                }
              />
              <button
                className="icon-btn"
                aria-label={show ? "隐藏密钥" : "显示密钥"}
                onClick={() => setShow((v) => !v)}
              >
                {show ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </label>
        )}
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          启用此数据源
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={active}
            onChange={(e) => setActive(e.target.checked)}
          />
          设为当前来源
        </label>
        <Warning>
          {setup.requiresKey
            ? "Key 只提交给本机后端并加密保管；不写入浏览器存储、日志或导出。"
            : "此来源免 Key，可直接验证能力。"}
          {source.sync_supported === false
            ? " 全市场盘后同步请使用扶摇免费来源。"
            : " 保存配置不会自动取数。"}
        </Warning>
        {error && <ErrorBox message={error} />}
      </div>
      <div className="modal-footer">
        <button
          className="btn"
          onClick={() => {
            setKey("");
            onClose();
          }}
        >
          取消
        </button>
        <button className="btn primary" disabled={busy} onClick={save}>
          {busy ? <Spinner /> : <Check size={14} />}保存配置
        </button>
      </div>
    </Modal>
  );
}
function MarketSources() {
  const result = useApi<Row>("/sources");
  const [editing, setEditing] = useState<Row | null>(null),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [probe, setProbe] = useState<Row | null>(null);
  const toast = useToast();
  useEffect(() => {
    const clearOnLeave = (event: Event) => {
      if ((event as CustomEvent).detail !== "sources") setEditing(null);
    };
    window.addEventListener("workbench-route", clearOnLeave);
    return () => window.removeEventListener("workbench-route", clearOnLeave);
  }, []);
  async function verify(source: Row) {
    const id = source.source_id || source.id;
    setBusy(id);
    setError("");
    try {
      const r = await post(`/sources/${id}/verify`);
      setProbe({ ...r, source_name: source.name });
      result.reload();
      toast("能力验证完成，请查看逐项结果");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  const sources = list(result.data);
  const supplement = result.data?.public_supplement;
  return (
    <div className="page-stack sources-page">
      <div className="sources-heading">
        <div>
          <span className="sources-eyebrow">数据连接</span>
          <p>配置一次，逐项查看实际可用能力。</p>
        </div>
        <Badge>
          <ShieldCheck size={12} />
          密钥仅在本机后端保管
        </Badge>
      </div>
      {result.error && (
        <ErrorBox message={result.error} retry={result.reload} />
      )}
      {error && <ErrorBox message={error} />}
      <div className="sources-connections">
        {result.loading ? (
          <Loading />
        ) : sources.length ? (
          sources.map((source) => {
            const id = source.source_id || source.id;
            const setup = sourceSetup(source);
            const caps = sourceCapabilities(source);
            const counts = capabilityCounts(caps);
            return (
              <section
                key={id}
                className={`sources-connection ${source.active ? "is-active" : ""}`}
              >
                <div className="sources-connection-head">
                  <div className="sources-monogram" aria-hidden="true">
                    {id === "fuyao" ? "扶" : "T"}
                  </div>
                  <div className="sources-identity">
                    <h2>{source.name}</h2>
                    <span>{setup.label}</span>
                  </div>
                  {source.active && <Badge tone="good">当前</Badge>}
                </div>
                <div className="sources-purpose">
                  <Database size={13} />
                  {source.sync_supported
                    ? "全市场盘后同步"
                    : "历史查询与交叉核验"}
                </div>
                <div
                  className="sources-capabilities"
                  aria-label={`${source.name}能力状态`}
                >
                  {caps.length ? (
                    caps.map((cap: Row, i: number) => {
                      const state = capabilityState(cap);
                      const name =
                        capLabels[cap.key || cap.name] ||
                        cap.label ||
                        cap.name ||
                        cap.key;
                      return (
                        <span
                          key={cap.key || cap.name || i}
                          className={`sources-capability is-${state}`}
                          title={`${name} · ${capabilityStateLabels[state]}${cap.reason ? "：" + cap.reason : ""}`}
                        >
                          {state === "verified" ? (
                            <Check size={12} />
                          ) : state === "denied" ? (
                            <LockKeyhole size={11} />
                          ) : state === "unsupported" ? (
                            <XCircle size={11} />
                          ) : (
                            <CircleHelp size={11} />
                          )}
                          {name}
                          <span className="sources-visually-hidden">
                            ：{capabilityStateLabels[state]}
                          </span>
                        </span>
                      );
                    })
                  ) : (
                    <span className="muted">配置后手动验证实际权限</span>
                  )}
                </div>
                <div className="sources-capability-counts">
                  <span>
                    <b>{counts.verified}</b> 项已验证
                  </span>
                  {counts.unknown > 0 && <span>{counts.unknown} 项待核验</span>}
                  {counts.denied > 0 && (
                    <span className="sources-access-denied">
                      {counts.denied} 项拒绝访问
                    </span>
                  )}
                  {counts.unsupported > 0 && (
                    <span>{counts.unsupported} 项未接入</span>
                  )}
                </div>
                <details className="sources-details">
                  <summary>
                    <ChevronRight size={13} />
                    能力详情与来源说明
                  </summary>
                  <div className="sources-details-body">
                    <p>{source.description || setup.note}</p>
                    <ul className="sources-capability-detail">
                      {caps.map((cap: Row, i: number) => (
                        <li key={cap.key || i}>
                          <div>
                            <strong>
                              {capLabels[cap.key] || cap.name || cap.key}
                            </strong>
                            <span
                              className={`sources-result is-${capabilityState(cap)}`}
                            >
                              {capabilityStateLabels[capabilityState(cap)]}
                            </span>
                          </div>
                          <p>{cap.reason || "尚无此账号的核验结果。"}</p>
                          {cap.checked_at && (
                            <time>{dateTime(cap.checked_at)}</time>
                          )}
                        </li>
                      ))}
                    </ul>
                    <Warnings items={source.warnings} />
                  </div>
                </details>
                <div className="sources-connection-state">
                  <span>
                    <i
                      className={`status-dot ${source.enabled ? "good" : ""}`}
                    />
                    {source.enabled ? "已启用" : "已停用"} ·{" "}
                    {!setup.requiresKey
                      ? "免 Key"
                      : source.configured
                        ? "凭据已配置"
                        : "待配置凭据"}
                  </span>
                  <span>
                    最近验证{" "}
                    {source.last_verified_at || source.verified_at
                      ? dateTime(source.last_verified_at || source.verified_at)
                      : "尚未验证"}
                  </span>
                </div>
                <div className="sources-connection-actions">
                  <button className="btn" onClick={() => setEditing(source)}>
                    <Settings2 size={13} />
                    配置
                  </button>
                  <button
                    className="btn primary"
                    disabled={
                      !!busy ||
                      !source.enabled ||
                      (setup.requiresKey && !source.configured)
                    }
                    onClick={() => verify(source)}
                  >
                    {busy === id ? <Spinner /> : <ShieldCheck size={13} />}
                    验证能力
                  </button>
                </div>
                {(!source.enabled ||
                  (setup.requiresKey && !source.configured)) && (
                  <small className="sources-next-step">
                    {!source.enabled
                      ? "启用来源后可验证。"
                      : "先保存 Key，再手动验证能力。"}
                  </small>
                )}
              </section>
            );
          })
        ) : (
          <Panel title="数据源">
            <Empty
              title="暂无来源配置"
              description="请确认后端数据源注册表已初始化。"
            />
          </Panel>
        )}
      </div>
      <div className="sources-legend" aria-label="能力状态图例">
        <span>
          <Check size={12} />
          本次验证可用
        </span>
        <span>
          <LockKeyhole size={11} />
          接口拒绝访问
        </span>
        <span>
          <CircleHelp size={12} />
          待核验
        </span>
        <span>
          <XCircle size={12} />
          本项目尚未接入
        </span>
      </div>
      <section className="sources-supplement">
        <div className="sources-supplement-head">
          <div className="sources-supplement-title">
            <Layers3 size={18} />
            <div>
              <h2>公开数据补充</h2>
              <p>AKShare同源公开接口 · 免 Key</p>
            </div>
          </div>
          <a className="btn small" href="#data">
            到数据中心补齐
            <ChevronRight size={13} />
          </a>
        </div>
        <div className="sources-supplement-stats">
          <div>
            <span>封板明细</span>
            <strong>
              {supplement?.seal_rows ?? "—"}
              <small> 条</small>
            </strong>
            <span>
              {supplement?.seal_complete ? "本次完整" : "完整性待核验"}
            </span>
          </div>
          <div>
            <span>板块资金</span>
            <strong
              className={
                supplement?.fund_flow_complete
                  ? "sources-value-good"
                  : "sources-value-warn"
              }
            >
              {supplement?.fund_flow_complete ? "已核验" : "有缺口"}
            </strong>
            <span>{supplement?.target_date || "尚无来源日期"}</span>
          </div>
          <div>
            <span>年内涨停完整日</span>
            <strong>
              {supplement?.annual_covered_days ?? "—"}
              <small> / {supplement?.annual_expected_days ?? "—"}</small>
            </strong>
            <span>按来源池口径</span>
          </div>
          <div>
            <span>年内跌停已取得</span>
            <strong>
              {supplement?.annual_down_observed_days ?? "—"}
              <small> / {supplement?.annual_down_expected_days ?? "—"}</small>
            </strong>
            <span>取得不等于完整覆盖</span>
          </div>
        </div>
        <details className="sources-details sources-supplement-details">
          <summary>
            <ChevronRight size={13} />
            查看补充范围与限制
          </summary>
          <div className="sources-details-body">
            <p>
              {supplement?.description ||
                "补充封板细节和板块资金，分别核验来源日期、单位与完整性；批量日K仍由同步来源提供。"}
            </p>
            <p>
              连续封单稳定性需要逐时盘口序列，一次收盘封单金额不能代替稳定性。免费接口有限重试后仍可能失败，失败日期保留并可手动补拉。
            </p>
            <Warnings items={supplement?.failures} />
          </div>
        </details>
      </section>
      <details className="sources-guide">
        <summary>
          <div>
            <CircleHelp size={16} />
            <strong>如何选择与理解权限</strong>
          </div>
          <span>
            配置建议 · 状态解释
            <ChevronRight size={14} />
          </span>
        </summary>
        <div className="sources-guide-content">
          <div className="sources-guide-grid">
            <div>
              <Badge tone="good">盘后同步</Badge>
              <h3>扶摇 · 免费注册 Key</h3>
              <p>配置后逐项验证，在数据中心手动初始化与增量更新。</p>
            </div>
            <div>
              <Badge>已有账号</Badge>
              <h3>TickFlow · 填现有 Key</h3>
              <p>无需手选免费或付费套餐，日K与指数以当前账号实际探测为准。</p>
            </div>
            <div>
              <Badge>免 Key 查询</Badge>
              <h3>TickFlow 公共历史</h3>
              <p>用于个股、指数历史和交叉核验，当前未接入全市场批量同步。</p>
            </div>
          </div>
          <p className="sources-guide-note">
            验证仅代表本次接口和当前凭据结果，不代表全年完整或持续稳定。拒绝访问不等于需要付费；超时仍待核验。保存配置和打开本页均不会自动验证或取数。
          </p>
        </div>
      </details>
      {probe && (
        <Panel
          title={`最近能力检查 · ${probe.source_name}`}
          actions={
            <button className="btn small" onClick={() => setProbe(null)}>
              收起
            </button>
          }
        >
          {Array.isArray(probe.capabilities || probe.items) ? (
            <Table
              rows={probe.capabilities || probe.items}
              columns={[
                {
                  key: "name",
                  title: "能力",
                  render: (v, r) => capLabels[r.key] || v || r.key,
                },
                {
                  key: "status",
                  title: "状态",
                  render: (_v, row) => (
                    <span
                      className={`sources-result is-${capabilityState(row)}`}
                    >
                      {capabilityStateLabels[capabilityState(row)]}
                    </span>
                  ),
                },
                { key: "reason", title: "检查说明" },
                {
                  key: "checked_at",
                  title: "检查时间",
                  render: (v) => dateTime(v),
                },
              ]}
              emptyTitle="本次未返回能力列表"
            />
          ) : (
            <div className="sources-details-body">
              <p>
                {probe.message ||
                  probe.reason ||
                  "验证已完成，能力状态已刷新。"}
              </p>
              <Warnings items={probe.warnings} />
            </div>
          )}
        </Panel>
      )}
      {editing && (
        <SourceEditor
          source={editing}
          onClose={() => setEditing(null)}
          onSaved={result.reload}
        />
      )}
    </div>
  );
}

const AIProviderSettings = lazy(() => import("../ai/AIProviderSettings"));
export default function Sources({
  initialTab = "market",
}: {
  initialTab?: "market" | "ai";
}) {
  const [tab, setTab] = useState(initialTab);
  useEffect(() => setTab(initialTab), [initialTab]);
  return (
    <div className="page-stack">
      <div className="sources-tabs" role="tablist" aria-label="连接配置类型">
        <button
          role="tab"
          id="source-market-tab"
          aria-controls="source-config-panel"
          aria-selected={tab === "market"}
          onClick={() => setTab("market")}
        >
          行情数据源
        </button>
        <button
          role="tab"
          id="source-ai-tab"
          aria-controls="source-config-panel"
          aria-selected={tab === "ai"}
          onClick={() => setTab("ai")}
        >
          AI 模型
        </button>
      </div>
      <div
        role="tabpanel"
        id="source-config-panel"
        aria-labelledby={
          tab === "market" ? "source-market-tab" : "source-ai-tab"
        }
      >
        {tab === "market" ? (
          <MarketSources />
        ) : (
          <Suspense fallback={<Loading />}>
            <AIProviderSettings />
          </Suspense>
        )}
      </div>
    </div>
  );
}
