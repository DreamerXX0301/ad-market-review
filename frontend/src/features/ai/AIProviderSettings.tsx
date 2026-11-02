import { useEffect, useRef, useState } from "react";
import { Check, KeyRound, Settings2, ShieldCheck, Trash2 } from "lucide-react";
import { api, requestKey, useApi, usePageActive } from "../../lib/api";
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
import {
  aiReason,
  modelChoices,
  plainText,
  taskLabel,
  terminalTask,
  verificationPassed,
} from "./aiState";
import type { AIProvider } from "./aiTypes";
import "./AI.css";

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
  onSaved: (verify: boolean) => Promise<void>;
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = usePageActive();
  const choices = [...(modelChoices[provider.provider_id] || [])];
  for (const item of [
    ...(provider.models || []),
    ...(provider.discovered_models || []),
  ]) {
    const id = typeof item === "string" ? item : item.id;
    if (id && !choices.some((choice) => choice.id === id))
      choices.push({ id, name: id });
  }
  useEffect(() => {
    if (!active) {
      setKey("");
      onClose();
    }
  }, [active, onClose]);
  async function save(verify: boolean) {
    setBusy(true);
    setError("");
    const changed =
      !!key.trim() ||
      model.trim() !== provider.model ||
      timeout !== (provider.limits?.timeout_seconds || 45);
    try {
      await aiWrite(
        `/providers/${provider.provider_id}`,
        {
          expected_revision: provider.config_revision,
          model: model.trim(),
          enabled: changed ? false : provider.enabled,
          ...(key.trim() ? { api_key: key.trim() } : {}),
          limits: {
            daily_budget_usd: budget,
            daily_request_limit: count,
            timeout_seconds: timeout,
          },
        },
        "PUT",
      );
      setKey("");
      await onSaved(verify);
      onClose();
    } catch (e: any) {
      setKey("");
      setError(e.message);
    } finally {
      setBusy(false);
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
        onSubmit={(event) => {
          event.preventDefault();
          void save(false);
        }}
      >
        <div className="modal-body ai-editor">
          <p>
            官方服务 ·{" "}
            {provider.provider_id === "openai"
              ? "api.openai.com"
              : "api.deepseek.com"}
          </p>
          <label>
            API Key{" "}
            <span className="muted">
              {provider.configured ? "已配置 · 留空保留" : "尚未配置"}
            </span>
            <input
              type="password"
              value={key}
              autoComplete="new-password"
              spellCheck={false}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                provider.configured ? "输入新 Key 以替换" : "输入官方 API Key"
              }
            />
          </label>
          <label>
            常用模型
            <select
              value={
                choices.some((choice) => choice.id === model) ? model : "custom"
              }
              onChange={(e) =>
                setModel(e.target.value === "custom" ? "" : e.target.value)
              }
            >
              {choices.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.name} · {choice.id}
                </option>
              ))}
              <option value="custom">填写其他模型 ID</option>
            </select>
          </label>
          <label>
            实际模型 ID
            <input
              value={model}
              maxLength={100}
              spellCheck={false}
              onChange={(e) => setModel(e.target.value)}
              required
              pattern="[A-Za-z0-9][A-Za-z0-9_.:\\-]{0,99}"
            />
          </label>
          <p className="muted">
            预置名称不代表账号已有权限或效果等级。验证会主动刷新账号模型目录；未知单价模型须先核验价格。
          </p>
          <details className="ai-details">
            <summary>费用与限制</summary>
            <div className="ai-editor">
              <label>
                本实例每日预算（美元）
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
                本实例每日最多付费请求
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
                两家服务共用本实例预算，验证推理计入用量；这是本地预估保护，实际账单以供应商为准。
              </p>
            </div>
          </details>
          <Warning>
            Key 只提交给本机后端加密保存，不回显，不写入浏览器存储。变更 Key
            或模型后须重新验证，再手动启用。
          </Warning>
          {error && <ErrorBox message={error} />}
        </div>
        <div className="modal-footer ai-actions">
          <button
            type="button"
            className="btn"
            disabled={busy}
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
            disabled={busy || !model.trim()}
          >
            {busy && <Spinner />}仅保存
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={busy || !model.trim()}
            onClick={(event) => {
              if (event.currentTarget.form?.reportValidity()) void save(true);
            }}
          >
            <ShieldCheck size={14} />
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
    if (
      task.data?.state === "failed" ||
      task.data?.state === "interrupted_manual_retry"
    )
      setError(aiReason(task.data.reason_code));
  }, [taskId, task.data, providers.reload]);
  const active = usePageActive();
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
          limits: provider.limits,
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
      <div className="ai-meta ai-instance-usage">
        <span>
          本实例今日
          {providers.data?.usage?.day ? `（${providers.data.usage.day}）` : ""}
          已发请求：{providers.data?.usage?.requests ?? "未知"}
        </span>
        <span>
          预估及预留合计：
          {providers.data?.usage?.amount_usd != null
            ? `${plainText(providers.data.usage.amount_usd)} USD`
            : "未知"}
        </span>
        <span>两家服务合计；实际账单以供应商为准</span>
      </div>
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
      ) : (
        <div className="ai-provider-grid">
          {(providers.data?.items || []).map((provider) => (
            <section
              className="sources-connection ai-provider"
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
                <span>
                  {provider.configured ? "Key 已配置" : "Key 尚未配置"}
                </span>
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
                  className="btn"
                  disabled={!!busy || task.pending}
                  onClick={() => setEditing(provider)}
                >
                  <Settings2 size={13} />
                  配置
                </button>
                <button
                  className="btn"
                  disabled={!!busy || task.pending || !provider.configured}
                  onClick={() => verify(provider)}
                >
                  <ShieldCheck size={13} />
                  验证所选模型
                </button>
                <button
                  className={"btn " + (provider.enabled ? "" : "primary")}
                  disabled={
                    !!busy ||
                    (!provider.enabled &&
                      (!verificationPassed(provider) || task.pending))
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
      )}
      {!providers.loading &&
        !providers.error &&
        !providers.data?.items.length && (
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
          onSaved={async (shouldVerify) => {
            providers.reload();
            if (shouldVerify) {
              const latest = await api<{ items: AIProvider[] }>(
                "/ai/providers",
              );
              const provider = latest.items.find(
                (item) => item.provider_id === editing.provider_id,
              );
              if (provider) await verify(provider);
            }
          }}
        />
      )}
      {clearing && (
        <Modal
          title={`清除 ${clearing.name} Key`}
          onClose={() => setClearing(null)}
        >
          <div className="modal-body">
            <p>清除凭据将关闭该服务并使当前验证失效。已保存会话继续保留。</p>
          </div>
          <div className="modal-footer">
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
