import type { AIItem, AIProvider, AISession } from "./aiTypes";

export function chatSessionTitle(session: AISession) {
  const title = typeof session.title === "string" ? session.title.trim() : "";
  if (title === "请解读这份冻结证据。") {
    const label = session.kind === "stock" ? "个股研究" : "行情复盘";
    const subject =
      session.kind === "stock" ? session.instrument_id : session.as_of;
    return subject ? `${label} · ${subject}` : label;
  }
  return plainText(
    title || session.instrument_id || session.as_of,
    session.kind === "stock" ? "个股研究对话" : "行情复盘对话",
  );
}

const reasons: Record<string, string> = {
  authentication_failed: "Key 未通过认证（401），请核对或替换凭据。",
  invalid_api_key: "Key 未通过认证（401），请核对或替换凭据。",
  insufficient_quota: "供应商额度或余额不足，请到官方账户核对。",
  quota_exceeded: "供应商额度或余额不足，请到官方账户核对。",
  quota_exhausted: "供应商额度或余额不足，请到官方账户核对。",
  rate_limited: "供应商请求受限（429），请稍后手动重试。",
  rate_limit_exceeded: "供应商请求受限（429），请稍后手动重试。",
  timeout: "模型等待超时；请求可能已产生费用，请手动核对后重试。",
  request_timeout: "模型等待超时；请求可能已产生费用，请手动核对后重试。",
  invalid_response: "模型响应未满足格式要求，本次未发布结果。",
  schema_validation_failed: "模型响应未满足结构化格式要求，本次未发布结果。",
  reference_validation_failed: "模型引用未通过核验，本次未发布结果。",
  output_truncated: "模型输出被截断，本次未发布结果。",
  refusal: "供应商拒绝了本次请求。",
  unknown_price: "该模型的单价尚未核验，付费验证与生成暂不可用。",
  unsupported_model: "请选择受支持的常用GPT模型，并验证后启用。",
  model_unavailable: "当前账号尚未确认所选模型可用，请核对模型 ID。",
  incompatible_parameters: "模型参数或结构化输出能力不兼容，请查看逐项验证。",
  unsupported_parameters: "模型参数或结构化输出能力不兼容，请查看逐项验证。",
  invalid_output: "模型响应未满足结构化格式要求，本次未发布结果。",
  invalid_reference: "模型引用超出本次证据范围，本次未发布结果。",
  ungrounded_number: "模型文字含未核验数值或日期，本次未发布结果。",
  incomplete_response: "模型未完整完成响应，本次未发布结果。",
  response_too_large: "模型响应超过大小限制，本次未发布结果。",
  provider_unavailable: "供应商暂时不可用，请稍后手动重试。",
  provider_error: "供应商未完成请求，请核对用量后手动重试。",
  credential_unavailable: "凭据或外置主密钥不可用，请重新配置。",
  verification_required: "须先验证并启用当前模型与 Key。",
  configuration_changed: "模型或凭据配置已变化，请新建会话。",
  context_changed: "资料已更新，请重新发送问题。",
  context_unavailable: "暂时无法取得相关资料，请稍后重试。",
  input_too_large: "问题或相关资料较多，请缩小问题后再试。",
  context_ineligible: "暂时无法取得所需资料，请稍后重试。",
  session_capacity: "本地会话已达 200 条，请删除不再需要的会话。",
  message_capacity: "该会话已达 40 条双方消息上限，请新建会话。",
  session_busy: "该会话仍有未结束的请求，请等待或手动取消。",
  session_deleted: "原会话已删除，请新建会话。",
  consent_required: "请重新发送这条问题。",
  request_key_conflict: "请求标识对应的参数已变化，请新建一次操作。",
  web_search_unavailable: "这次暂时无法查询网络资料，请稍后再试。",
  budget_exceeded:
    "本次请求超过今日应用额度（含费用预留）。请到“模型设置”查看或调整；调用次数按模型请求计算。",
  request_limit_exceeded: "本实例当日请求次数已用尽。",
  config_changed: "模型设置已更改，请开始新对话。",
  interrupted_manual_retry: "本次调用已中断，可能已计费；不会自动补发。",
  network_error: "网络请求未完成；不会自动重发模型请求。",
};
export function aiReason(code?: string, status?: number) {
  if (code && reasons[code]) return reasons[code];
  if (status === 401) return reasons.authentication_failed;
  if (status === 402) return reasons.insufficient_quota;
  if (status === 409) return "暂时无法完成这次操作，请刷新后重试。";
  if (status === 429) return "请求受到限制，请查看额度或频率状态后手动重试。";
  if (status === 400 || status === 422)
    return "请求条件或模型参数不符合要求，请核对配置与材料。";
  if (status === 404) return "该配置、会话或任务不存在，可能已到期清理。";
  return "本次请求未完成，请查看本地任务状态后手动重试。";
}
export const terminalTask = (state?: string) =>
  !!state &&
  [
    "completed",
    "failed",
    "cancelled",
    "interrupted_manual_retry",
    "stale",
  ].includes(state);
export const taskLabel = (state?: string) =>
  ({
    queued: "排队等待",
    running: "正在处理…",
    completed: "已完成",
    failed: "未完成",
    cancelled: "已取消",
    interrupted_manual_retry: "已中断 · 需手动重试",
    stale: "配置已变化",
  })[state || ""] || "正在读取任务状态";
export const verificationPassed = (provider?: AIProvider) =>
  provider?.verification?.state === "passed";
export const canGenerate = (provider?: AIProvider) =>
  !!provider?.configured &&
  !!provider.enabled &&
  verificationPassed(provider) &&
  selectableModel(provider.provider_id, provider.model);
export function aiModelSetupGuide(items: AIProvider[], provider?: AIProvider) {
  const configured = items.find((item) => item.configured);
  const current = provider || items.find(canGenerate) || configured;
  if (!current?.configured)
    return {
      title: configured ? "当前模型尚未配置" : "尚未配置 AI 模型",
      description:
        "在模型设置中选择 OpenAI 或 DeepSeek，填写 API Key，验证通过后启用。",
    };
  if (canGenerate(current)) return null;
  if (!selectableModel(current.provider_id, current.model))
    return {
      title: "当前模型暂不可用",
      description: "请在模型设置中选择受支持的模型，验证通过后启用。",
    };
  if (current.verification?.state === "verifying")
    return {
      title: "正在验证模型",
      description: "可在模型设置查看验证进度。通过后启用即可发送。",
    };
  if (!verificationPassed(current))
    return {
      title: "当前模型需要验证",
      description: "请在模型设置中检查 API Key 和模型，验证通过后启用。",
    };
  return {
    title: "当前模型尚未启用",
    description: "请在模型设置中启用已通过验证的模型。",
  };
}
export function sessionMatches(
  session: AISession,
  provider: AIProvider | undefined,
  configMatches = true,
) {
  return (
    !!provider &&
    configMatches &&
    session.provider_id === provider.provider_id &&
    session.model === provider.model
  );
}
export function plainText(value: unknown, fallback = "待核验"): string {
  return typeof value === "string" && value
    ? value
    : typeof value === "number"
      ? String(value)
      : fallback;
}
export function itemText(item: AIItem) {
  return plainText(
    item.text ||
      item.title ||
      item.label ||
      item.reason ||
      item.message ||
      item.capability,
  );
}
export function aiSourceLabel(value: unknown) {
  const names: Record<string, string> = {
    published_market: "已发布盘面",
    fuyao: "扶摇",
    eastmoney: "东方财富",
    cninfo: "巨潮资讯",
    local_research_technical: "本地研究技术（日 / 周 / 月）",
    sector_index_history: "板块指数历史",
  };
  return typeof value === "string" ? names[value] || value : "待核验";
}
export function metricSummary(metric: AIItem) {
  const labels: Record<string, string> = {
    revenue_ratio: "收入占比",
    gross_margin_ratio: "毛利率",
    revenue_cny: "来源主营收入",
    cost_cny: "来源主营成本",
    profit_cny: "来源主营利润",
    total_market_cap_cny: "总市值",
    float_market_cap_cny: "流通市值",
    total_shares: "总股本",
    float_shares: "流通股本",
  };
  const name = plainText(
    metric.label || metric.metric_id?.split(":").at(-1),
    "来源指标",
  );
  const value =
    typeof metric.value === "number" && !Number.isFinite(metric.value)
      ? "未知"
      : plainText(metric.value, "未知");
  return `${labels[name] || name} · ${value} ${metric.unit === "ratio" ? "原始比率（1代表100%）" : plainText(metric.unit, "单位待核验")}`;
}
export function chatMetricValue(metric: AIItem): string {
  const value = metric.value;
  if (typeof value !== "number")
    return value == null || typeof value === "boolean"
      ? "未知"
      : `${plainText(value, "未知")} ${plainText(metric.unit, "")}`.trim();
  if (!Number.isFinite(value)) return "未知";
  if (metric.unit === "%") return `${value.toFixed(2)}%`;
  if (metric.unit === "ratio") return `${(value * 100).toFixed(2)}%`;
  if (metric.unit === "CNY" || metric.unit === "元") {
    if (Math.abs(value) >= 100000000)
      return `≈${(value / 100000000).toFixed(2)}亿元`;
    if (Math.abs(value) >= 10000) return `≈${(value / 10000).toFixed(2)}万元`;
    return `≈${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}元`;
  }
  const unit = metric.unit === "CNY/股" ? "元/股" : plainText(metric.unit, "");
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;
}
export function chatAnswerText(block: AIItem, context: AIItem): string {
  return plainText(block.text, "").replace(
    /\{\{metric:([^{}]*)\}\}/g,
    (_token, id: string) => {
      const metric = context.metrics?.find(
        (item: AIItem) => item.metric_id === id,
      );
      const allowed =
        context.allowed_ids?.metric_ids?.includes(id) &&
        block.metric_refs?.includes(id);
      const bound = metric?.evidence_ids?.some(
        (evidenceId: string) =>
          block.evidence_ids?.includes(evidenceId) &&
          context.allowed_ids?.evidence_ids?.includes(evidenceId),
      );
      return allowed && bound
        ? chatMetricValue(metric)
        : "未知（指标引用待核验）";
    },
  );
}
export function safeHttpUrl(value: unknown) {
  if (typeof value !== "string" || value.length > 4096) return null;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function nativeWebSources(message: AIItem): AIItem[] {
  const web = message.source_context?.web || message.web;
  if (
    !["web_answer", "agent_answer"].includes(message.response_type) ||
    web?.validation !== "native_citations_only" ||
    web?.allowed !== true ||
    web?.used !== true
  )
    return [];
  return (message.sources || []).filter(
    (source: AIItem) =>
      typeof source.source_id === "string" && safeHttpUrl(source.url),
  );
}
export function archivedNewsSources(message: AIItem) {
  const results = message.source_context?.tool_results;
  const sources = new Map<
    string,
    { url: string; title: string; source: string }
  >();
  if (!Array.isArray(results)) return [];
  for (const result of results) {
    if (
      result?.tool_name !== "read_news_events" ||
      !Array.isArray(result.evidence)
    )
      continue;
    for (const item of result.evidence) {
      const url = safeHttpUrl(item?.data?.url);
      if (!url?.startsWith("https://")) continue;
      const title = plainText(item?.title, "归档原文");
      const key = JSON.stringify([url, title]);
      sources.set(key, { url, title, source: plainText(item?.source, "") });
    }
  }
  return [...sources.values()];
}
export function nativeWebCitations(message: AIItem): AIItem[] {
  const sources = nativeWebSources(message);
  const length = Array.from(plainText(message.text, "")).length;
  return (message.citations || [])
    .flatMap((citation: AIItem) => {
      const url = safeHttpUrl(citation.url);
      const sourceIndex = sources.findIndex(
        (source) =>
          source.source_id === citation.source_id &&
          safeHttpUrl(source.url) === url,
      );
      if (
        !url ||
        sourceIndex < 0 ||
        !Number.isInteger(citation.start_index) ||
        !Number.isInteger(citation.end_index) ||
        citation.start_index < 0 ||
        citation.end_index <= citation.start_index ||
        citation.end_index > length
      )
        return [];
      return [{ ...citation, url, number: sourceIndex + 1 }];
    })
    .sort(
      (a: AIItem, b: AIItem) =>
        a.end_index - b.end_index || a.start_index - b.start_index,
    );
}
const modelMarkdownLink = () => /!?\[([^\]\n]{1,300})\]\(([^)\n]{1,4096})\)/g;
export function webAnswerParts(
  message: AIItem,
): { text?: string; citation?: AIItem }[] {
  const text = plainText(message.text, "");
  const chars = Array.from(text);
  const links = [...text.matchAll(modelMarkdownLink())].map((match) => ({
    start: Array.from(text.slice(0, match.index!)).length,
    end: Array.from(text.slice(0, match.index! + match[0].length)).length,
  }));
  // Offset checks always use the original codepoint sequence, before styling.
  const citations = nativeWebCitations(message)
    .map((citation) => {
      const link = links.find(
        (item) =>
          citation.end_index > item.start && citation.end_index < item.end,
      );
      return { ...citation, displayEnd: link?.end ?? citation.end_index };
    })
    .sort((a, b) => a.displayEnd - b.displayEnd);
  const parts: { text?: string; citation?: AIItem }[] = [];
  let position = 0;
  for (const citation of citations) {
    parts.push({
      text: chars
        .slice(position, citation.displayEnd)
        .join("")
        .replace(modelMarkdownLink(), "$1"),
    });
    parts.push({ citation });
    position = citation.displayEnd;
  }
  parts.push({
    text: chars.slice(position).join("").replace(modelMarkdownLink(), "$1"),
  });
  return parts;
}
export const modelChoices: Record<string, { id: string; name: string }[]> = {
  deepseek: [
    { id: "deepseek-flash", name: "Flash" },
    { id: "deepseek-v4-pro", name: "Pro" },
  ],
  openai: [
    { id: "gpt-6.1-sol", name: "GPT-6.1 Sol" },
    { id: "gpt-6-astra", name: "GPT-6 Astra" },
    { id: "gpt-6.1-terra", name: "GPT-6.1 Terra" },
    { id: "gpt-6-sol", name: "GPT-6 Sol" },
    { id: "gpt-6-luna", name: "GPT-6 Luna" },
    { id: "gpt-5.6-sol", name: "GPT-5.6 Sol" },
    { id: "gpt-5.6-terra", name: "GPT-5.6 Terra" },
    { id: "gpt-5.6-luna", name: "GPT-5.6 Luna" },
  ],
};

export function providerModelChoices(provider: AIProvider) {
  const choices = [...(modelChoices[provider.provider_id] || [])];
  // Account model discovery includes unrelated products and model snapshots.
  // OpenAI keeps the requested common models regardless of that directory.
  if (provider.provider_id === "openai") return choices;
  for (const item of [
    ...(provider.models || []),
    ...(provider.discovered_models || []),
  ]) {
    const id = typeof item === "string" ? item : item.id;
    if (id && !choices.some((choice) => choice.id === id))
      choices.push({ id, name: id });
  }
  return choices;
}

export function selectableModel(providerId: string, model: string) {
  return (
    !!model.trim() &&
    (providerId !== "openai" ||
      modelChoices.openai.some((choice) => choice.id === model))
  );
}
