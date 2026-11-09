import { useEffect, useId, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  KeyRound,
  Settings2,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { requestKey, useApi, usePageActive } from "../../lib/api";
import { dateTime } from "../../lib/format";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  Spinner,
  Warning,
} from "../../components/UI";
import { aiWrite, useAITask } from "./aiApi";
import { publishAIChange, subscribeAIChanges } from "./aiTabSync";
import {
  aiReason,
  plainText,
  selectableModel,
  taskLabel,
  terminalTask,
  verificationPassed,
} from "./aiState";
import type { AIProvider } from "./aiTypes";
import AIModelPicker from "./AIModelPicker";
import "../../components/SettingsDialog.css";
import "./AI.css";
import "./AIProviderSettings.css";

const capabilityLabels: Record<string, string> = {
  authentication: "账号与模型目录",
  model_request: "所选模型调用",
  structured_output: "结构化输出",
};
const stateLabels: Record<string, string> = {
  passed: "通过",
  failed: "未通过",
  unknown: "待核验",
  skipped: "未执行",
  unverified: "尚未验证",
  verifying: "验证中",
  temporary_error: "暂未通过",
  stale: "配置已变化",
};

function ProviderEditor({
  provider,
  onClose,
  onSaved,
}: {
  provider: AIProvider;
  onClose: () => void;
  onSaved: (saved: AIProvider, verify: boolean) => Promise<void>;
}) {
  const [key, setKey] = useState("");
  const [model, setModel] = useState(provider.model);
  const [budget, setBudget] = useState(
    provider.limits?.daily_budget_usd || "0.50",
  );
  const [count, setCount] = useState(
    provider.limits?.daily_request_limit || 20,
  );
  const [timeout, setTimeoutSeconds] = useState(
    provider.limits?.timeout_seconds || 45,
  );
  const [busy, setBusy] = useState<"save" | "verify" | null>(null);
  const [error, setError] = useState("");
  const keyId = useId();
  const active = usePageActive();
  const modelAllowed = selectableModel(provider.provider_id, model);
  useEffect(() => {
    if (!active) {
      setKey("");
      onClose();
    }
  }, [active, onClose]);
  async function save(verify: boolean) {
    if (!modelAllowed) {
      setError("请选择受支持的常用GPT模型");
      return;
    }
    setBusy(verify ? "verify" : "save");
    setError("");
    const changed =
      !!key.trim() ||
      model.trim() !== provider.model ||
      timeout !== (provider.limits?.timeout_seconds || 45);
    try {
      const saved = await aiWrite<AIProvider>(
        `/providers/${provider.provider_id}`,
        {
          expected_revision: provider.config_revision,
          model: model.trim(),
          enabled: changed ? false : provider.enabled,
          ...(key.trim() ? { api_key: key.trim() } : {}),
          limits: {
            ...(budget !== (provider.limits?.daily_budget_usd || "0.50")
              ? { daily_budget_usd: budget }
              : {}),
            ...(count !== (provider.limits?.daily_request_limit || 20)
              ? { daily_request_limit: count }
              : {}),
            ...(timeout !== (provider.limits?.timeout_seconds || 45)
              ? { timeout_seconds: timeout }
              : {}),
          },
        },
        "PUT",
      );
      setKey("");
      if (saved.config_revision !== provider.config_revision + 1) {
        await onSaved(saved, false);
        setError("配置已在其他页面更改，请重新打开设置后核对。");
        return;
      }
      await onSaved(saved, verify);
      onClose();
    } catch (e: any) {
      setKey("");
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <Modal
      title={`配置 ${provider.name}`}
      onClose={() => {
        setKey("");
        onClose();
      }}
    >
      <form
        onInvalidCapture={(event) => {
          if (!(event.target instanceof HTMLElement)) return;
          const limits =
            event.target.closest<HTMLDetailsElement>(".ai-editor-limits");
          if (limits) limits.open = true;
        }}
        onSubmit={(event) => {
          event.preventDefault();
          void save(false);
        }}
      >
        <div className="modal-body settings-dialog-body ai-editor">
          <div className="settings-dialog-intro">
            <Badge tone="info">官方 API</Badge>
            <p>
              {provider.provider_id === "openai"
                ? "api.openai.com"
                : "api.deepseek.com"}
            </p>
          </div>
          <div className="ai-editor-fields">
            <div className="settings-dialog-field-title">
              <label htmlFor={keyId}>API Key</label>
              <Badge tone={provider.configured ? "good" : "neutral"}>
                {provider.configured ? "已配置" : "待配置"}
              </Badge>
            </div>
            <input
              id={keyId}
              type="password"
              value={key}
              autoComplete="new-password"
              spellCheck={false}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                provider.configured ? "留空保留现有 Key" : "输入官方 API Key"
              }
            />
          </div>
          <div className="ai-editor-fields">
            <AIModelPicker
              provider={provider}
              value={model}
              onChange={setModel}
            />
          </div>
          <details className="ai-details ai-editor-limits">
            <summary>
              <ChevronRight size={14} aria-hidden="true" />
              <span>费用与限制</span>
            </summary>
            <div className="ai-editor-limit-fields">
              <label>
                每日预算（美元）
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  required
                  value={budget}
                  onChange={(e) => setBudget(e.target.value)}
                />
              </label>
              <label>
                每日付费请求上限
                <input
                  type="number"
                  min="1"
                  max="200"
                  required
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                />
              </label>
              <label>
                单次等待上限
                <select
                  value={timeout}
                  onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
                >
                  {[30, 45, 60].map((seconds) => (
                    <option key={seconds} value={seconds}>
                      {seconds} 秒
                    </option>
                  ))}
                </select>
              </label>
              <p className="muted">
                两家服务共用本实例预算，验证计入用量。实际账单以供应商为准。
              </p>
            </div>
          </details>
          <div className="settings-dialog-note">
            <ShieldCheck size={15} aria-hidden="true" />
            <p>
              Key 仅由本机后端加密保存。更换
              Key、模型或等待上限后，需重新验证并手动启用。
            </p>
          </div>
          {error && <ErrorBox message={error} />}
        </div>
        <div className="modal-footer settings-dialog-footer">
          <button
            type="button"
            className="btn"
            disabled={!!busy}
            onClick={() => {
              setKey("");
              onClose();
            }}
          >
            取消
          </button>
          <button
            type="submit"
            className="btn"
            disabled={!!busy || !modelAllowed}
          >
            {busy === "save" && <Spinner />}仅保存
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!!busy || !modelAllowed}
            onClick={(event) => {
              if (event.currentTarget.form?.reportValidity()) void save(true);
            }}
          >
            {busy === "verify" ? <Spinner /> : <ShieldCheck size={14} />}
            保存并验证
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function AIProviderSettings() {
  const providers = useApi<{
    items: AIProvider[];
    usage?: {
      day?: string;
      requests?: number | null;
      amount_usd?: string | null;
      state?: string;
    };
  }>("/ai/providers");
  const [editing, setEditing] = useState<AIProvider | null>(null);
  const [clearing, setClearing] = useState<AIProvider | null>(null);
  const [taskId, setTaskId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const task = useAITask(taskId);
  const completedTask = useRef("");
  const verificationAttempt = useRef<{ signature: string; key: string } | null>(
    null,
  );
  useEffect(() => {
    const verifying = providers.data?.items.find(
      (provider) =>
        provider.verification?.state === "verifying" &&
        provider.verification.task_id,
    );
    if (
      verifying?.verification?.task_id &&
      !task.pending &&
      verifying.verification.task_id !== taskId
    )
      setTaskId(verifying.verification.task_id);
  }, [providers.data, task.pending, taskId]);
  useEffect(() => {
    if (
      !taskId ||
      !terminalTask(task.data?.state) ||
      completedTask.current === taskId
    )
      return;
    completedTask.current = taskId;
    providers.reload();
    publishAIChange({ scope: "providers" });
    if (
      task.data?.state === "failed" ||
      task.data?.state === "interrupted_manual_retry"
    )
      setError(aiReason(task.data.reason_code));
  }, [taskId, task.data, providers.reload]);
  const active = usePageActive();
  useEffect(() => {
    if (!active) return;
    return subscribeAIChanges(
      (change) => {
        if (change.scope === "providers") providers.reload();
        if (change.scope === "task" && change.id === taskId) task.reload();
      },
      () => {
        providers.reload();
        if (taskId) task.reload();
      },
      providers.reload,
    );
  }, [active, taskId, providers.reload, task.reload]);
  useEffect(() => {
    if (!active) {
      setEditing(null);
      setClearing(null);
    }
  }, [active]);
  async function verify(provider: AIProvider) {
    setBusy(provider.provider_id);
    setError("");
    const signature = `${provider.provider_id}:${provider.config_revision}`;
    if (verificationAttempt.current?.signature !== signature)
      verificationAttempt.current = { signature, key: requestKey() };
    try {
      const response = await aiWrite<{ task_id: string }>(
        `/providers/${provider.provider_id}/verify`,
        {
          expected_revision: provider.config_revision,
          request_key: verificationAttempt.current.key,
        },
      );
      setTaskId(response.task_id);
      verificationAttempt.current = null;
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  async function toggle(provider: AIProvider) {
    setBusy(provider.provider_id);
    setError("");
    try {
      await aiWrite(
        `/providers/${provider.provider_id}`,
        {
          expected_revision: provider.config_revision,
          model: provider.model,
          enabled: !provider.enabled,
        },
        "PUT",
      );
      providers.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="ai-settings page-stack">
      <div className="ai-heading">
        <div>
          <span className="sources-eyebrow">AI 推理服务</span>
          <p>分别配置、逐项验证，再手动启用。仅在确认发送后调用官方模型。</p>
        </div>
        <Badge>
          <KeyRound size={12} />
          凭据只写入本机后端
        </Badge>
      </div>
      <section className="ai-provider-usage" aria-label="本实例今日用量">
        <header>
          <strong>本实例今日用量</strong>
          {providers.data?.usage?.day && (
            <time dateTime={providers.data.usage.day}>
              {providers.data.usage.day}
            </time>
          )}
        </header>
        <dl>
          <div>
            <dt>已发请求</dt>
            <dd>{providers.data?.usage?.requests ?? "未知"}</dd>
          </div>
          <div>
            <dt>预估及预留合计</dt>
            <dd>
              {providers.data?.usage?.amount_usd != null
                ? `${plainText(providers.data.usage.amount_usd)} USD`
                : "未知"}
            </dd>
          </div>
        </dl>
        <p>两家服务合计，实际账单以供应商为准。</p>
      </section>
      {providers.error && (
        <ErrorBox message={providers.error} retry={providers.reload} />
      )}
      {error && <ErrorBox message={error} />}
      {taskId && (
        <div className="ai-task" role="status">
          <div>
            {task.pending && <Spinner />}
            <strong>{taskLabel(task.data?.state)}</strong>
            <span>固定小请求验证会产生少量费用</span>
          </div>
          {task.error && <ErrorBox message={task.error} retry={task.reload} />}
          {task.pending && (
            <button
              className="btn small"
              onClick={async () => {
                try {
                  await aiWrite(`/tasks/${taskId}/cancel`);
                  task.reload();
                } catch (e: any) {
                  setError(e.message);
                }
              }}
            >
              取消验证
            </button>
          )}
        </div>
      )}
      {providers.loading && !providers.data ? (
        <Loading />
      ) : providers.data?.items.length ? (
        <div className="ai-provider-grid">
          {(providers.data?.items || []).map((provider) => (
            <section
              className={
                "sources-connection ai-provider" +
                (provider.enabled ? " is-active" : "")
              }
              key={provider.provider_id}
            >
              <div className="sources-connection-head">
                <div className="sources-monogram">
                  {provider.provider_id === "openai" ? "O" : "D"}
                </div>
                <div className="sources-identity">
                  <h2>{provider.name}</h2>
                  <span>官方 API · 独立凭据</span>
                </div>
                <Badge tone={provider.enabled ? "good" : "neutral"}>
                  {provider.enabled ? "已启用" : "已关闭"}
                </Badge>
              </div>
              <p className="ai-model-id">{provider.model}</p>
              <div className="ai-meta">
                <Badge tone={provider.configured ? "good" : "neutral"}>
                  {provider.configured ? "Key 已配置" : "Key 尚未配置"}
                </Badge>
                <span>
                  {stateLabels[provider.verification?.state || "unverified"] ||
                    "待核验"}
                </span>
              </div>
              <ul className="ai-checks">
                {["authentication", "model_request", "structured_output"].map(
                  (capability) => {
                    const item = provider.verification?.items?.find(
                      (entry) =>
                        (entry.capability || entry.name) === capability,
                    );
                    return (
                      <li key={capability}>
                        <div>
                          <span>{capabilityLabels[capability]}</span>
                          <Badge
                            tone={
                              item?.state === "passed"
                                ? "good"
                                : item?.state === "failed"
                                  ? "warn"
                                  : "neutral"
                            }
                          >
                            {stateLabels[item?.state || "unknown"] || "待核验"}
                          </Badge>
                        </div>
                        {item?.reason_code && (
                          <p>{aiReason(item.reason_code)}</p>
                        )}
                      </li>
                    );
                  },
                )}
              </ul>
              <p className="muted">
                最近验证{" "}
                {provider.verification?.checked_at_ms
                  ? dateTime(provider.verification.checked_at_ms)
                  : "尚无记录"}
              </p>
              <div className="ai-actions">
                <button
                  className="btn small"
                  disabled={!!busy || task.pending}
                  onClick={() => setEditing(provider)}
                >
                  <Settings2 size={13} />
                  配置
                </button>
                <button
                  className="btn small"
                  disabled={
                    !!busy ||
                    task.pending ||
                    !provider.configured ||
                    !selectableModel(provider.provider_id, provider.model)
                  }
                  onClick={() => verify(provider)}
                >
                  <ShieldCheck size={13} />
                  验证所选模型
                </button>
                <button
                  className={"btn small" + (provider.enabled ? "" : " primary")}
                  disabled={
                    !!busy ||
                    (!provider.enabled &&
                      (!verificationPassed(provider) ||
                        !selectableModel(
                          provider.provider_id,
                          provider.model,
                        ) ||
                        task.pending))
                  }
                  onClick={() => toggle(provider)}
                >
                  <Check size={13} />
                  {provider.enabled ? "关闭 AI" : "启用 AI"}
                </button>
              </div>
              {provider.configured && (
                <button
                  className="link ai-delete-key"
                  disabled={!!busy || task.pending}
                  onClick={() => setClearing(provider)}
                >
                  <Trash2 size={12} />
                  清除 Key
                </button>
              )}
            </section>
          ))}
        </div>
      ) : providers.error ? null : (
        <Empty
          title="暂无 AI 服务配置"
          description="请确认后端 AI 模块已初始化。"
        />
      )}
      <Warning>
        验证只证明本次认证、调用与格式兼容，不证明内容正确。关闭或清除 Key
        后仍可阅读本地历史会话；历史默认保留 30 天，可手动保留或删除。
      </Warning>
      {editing && (
        <ProviderEditor
          provider={editing}
          onClose={() => setEditing(null)}
          onSaved={async (saved, shouldVerify) => {
            providers.reload();
            if (shouldVerify) await verify(saved);
          }}
        />
      )}
      {clearing && (
        <Modal
          title={`清除 ${clearing.name} Key`}
          onClose={() => setClearing(null)}
        >
          <div className="modal-body settings-dialog-body">
            <p>清除凭据将关闭该服务并使当前验证失效。已保存会话继续保留。</p>
          </div>
          <div className="modal-footer settings-dialog-footer">
            <button
              className="btn"
              disabled={!!busy}
              onClick={() => setClearing(null)}
            >
              取消
            </button>
            <button
              className="btn danger"
              disabled={!!busy}
              onClick={async () => {
                setBusy(clearing.provider_id);
                setError("");
                try {
                  await aiWrite(
                    `/providers/${clearing.provider_id}/credential?expected_revision=${clearing.config_revision}`,
                    undefined,
                    "DELETE",
                  );
                  setClearing(null);
                  providers.reload();
                } catch (e: any) {
                  setError(e.message);
                } finally {
                  setBusy("");
                }
              }}
            >
              <Trash2 size={13} />
              清除 Key
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
