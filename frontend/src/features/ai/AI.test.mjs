import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (code) =>
  "data:text/javascript;base64," + Buffer.from(code).toString("base64");
const compile = async (path) =>
  ts.transpileModule(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
const helperUrl = encode(await compile("./aiState.ts"));
const attachmentUrl = encode(await compile("./aiAttachments.ts"));
const {
  aiModelSetupGuide,
  aiReason,
  canGenerate,
  sessionMatches,
  terminalTask,
  chatMetricValue,
  chatAnswerText,
  nativeWebCitations,
  webAnswerParts,
  safeHttpUrl,
  chatSessionTitle,
} = await import(helperUrl);
const provider = {
  provider_id: "openai",
  model: "gpt-6.1-sol",
  configured: true,
  enabled: true,
  verification: { state: "passed" },
};
assert.equal(canGenerate(provider), true);
assert.equal(
  chatSessionTitle({
    kind: "stock",
    instrument_id: "600519.SH",
    title: "请解读这份冻结证据。",
  }),
  "个股研究 · 600519.SH",
);
assert.equal(
  chatSessionTitle({
    kind: "market",
    as_of: "2026-10-30",
    title: "请解读这份冻结证据。",
  }),
  "行情复盘 · 2026-10-30",
);
assert.equal(
  chatSessionTitle({ kind: "stock", title: "今天有什么变化？" }),
  "今天有什么变化？",
);
for (const change of [
  { configured: false },
  { enabled: false },
  { verification: { state: "stale" } },
])
  assert.equal(canGenerate({ ...provider, ...change }), false);
const unconfiguredProvider = { ...provider, configured: false, enabled: false };
assert.equal(aiModelSetupGuide([]).title, "尚未配置 AI 模型");
assert.match(
  aiModelSetupGuide([unconfiguredProvider]).description,
  /API Key.*验证.*启用/,
);
assert.equal(aiModelSetupGuide([unconfiguredProvider, provider]), null);
assert.equal(
  aiModelSetupGuide([provider], unconfiguredProvider).title,
  "当前模型尚未配置",
);
for (const [change, title] of [
  [{ verification: { state: "verifying" } }, "正在验证模型"],
  [{ verification: { state: "stale" } }, "当前模型需要验证"],
  [{ enabled: false }, "当前模型尚未启用"],
  [{ model: "unsupported" }, "当前模型暂不可用"],
])
  assert.equal(aiModelSetupGuide([{ ...provider, ...change }]).title, title);
assert.equal(
  sessionMatches(
    { provider_id: "openai", model: "gpt-6.1-sol", context_digest: "old" },
    provider,
  ),
  true,
);
assert.equal(
  sessionMatches(
    { provider_id: "deepseek", model: "deepseek-flash" },
    provider,
  ),
  false,
);
assert.equal(
  sessionMatches({ provider_id: "openai", model: "other" }, provider),
  false,
);
for (const state of [
  "completed",
  "cancelled",
  "failed",
  "interrupted_manual_retry",
])
  assert.equal(terminalTask(state), true);
for (const [code, pattern] of Object.entries({
  authentication_failed: /401/,
  quota_exhausted: /额度|余额/,
  rate_limited: /429/,
  timeout: /超时/,
  configuration_changed: /新建|新对话/,
}))
  assert.match(aiReason(code), pattern);
assert.ok(!aiReason(undefined, 409).includes("新建"));
assert.equal(chatMetricValue({ value: 0, unit: "%" }), "0.00%");
assert.equal(chatMetricValue({ value: null, unit: "CNY" }), "未知");
assert.equal(chatMetricValue({ value: -1.3195716, unit: "%" }), "-1.32%");
assert.equal(chatMetricValue({ value: 0.856909, unit: "ratio" }), "85.69%");
assert.equal(chatMetricValue({ value: 123456789, unit: "CNY" }), "≈1.23亿元");
const context = {
  as_of: "2026-10-30",
  claims: [{ claim_id: "c1", text: "原事实", evidence_ids: ["e1"] }],
  evidence: [
    {
      evidence_id: "e1",
      source: "synthetic-only",
      title: "合成测试来源",
      data: { secret: "NEVER_RENDER_RAW_JSON" },
    },
  ],
  metrics: [
    {
      metric_id: "m1",
      label: "变化",
      value: -1.3195716,
      unit: "%",
      evidence_ids: ["e1"],
    },
  ],
  allowed_ids: { evidence_ids: ["e1"], metric_ids: ["m1"] },
};
const block = {
  heading: "直接回答",
  text: "变化为{{metric:m1}}。<script>alert(1)</script>",
  evidence_ids: ["e1"],
  metric_refs: ["m1"],
};
assert.ok(chatAnswerText(block, context).includes("-1.32%"));
for (const change of [{ metric_refs: [] }, { evidence_ids: ["other"] }])
  assert.ok(
    !chatAnswerText({ ...block, ...change }, context).includes("-1.32%"),
  );
const runtime = import.meta.resolve("react/jsx-runtime");
let answerCode = await compile("./AIAnswerText.tsx");
for (const [name, url] of Object.entries({
  "react/jsx-runtime": runtime,
  "./aiState": helperUrl,
}))
  answerCode = answerCode.replaceAll(
    'from "' + name + '"',
    "from " + JSON.stringify(url),
  );
const answerUrl = encode(answerCode);
const { default: AIAnswerText } = await import(answerUrl);
let content = await compile("./AIContent.tsx");
for (const [name, url] of Object.entries({
  "react/jsx-runtime": runtime,
  "../../lib/format": encode("export const dateTime=value=>String(value);"),
  "./aiState": helperUrl,
  "./AIAnswerText": answerUrl,
  "./aiAttachments": attachmentUrl,
}))
  content = content.replaceAll(
    'from "' + name + '"',
    "from " + JSON.stringify(url),
  );
const { AIMessageView } = await import(encode(content));
const attachmentMessage = renderToStaticMarkup(createElement(AIMessageView, {
  context, message: {
    message_id: "m-attachment", role: "user", text: "研究这份财报", state: "completed", created_at_ms: 1,
    attachments: [
      {attachment_id:"a1",name:"公司财报.pdf",media_type:"application/pdf",size:1024,kind:"text",text_only:true,page_count:2,content_url:"/api/v1/ai/sessions/s1/messages/m1/attachments/a1"},
      {attachment_id:"a2",name:"走势图.png",media_type:"image/png",size:300,kind:"image",content_url:"/api/v1/ai/sessions/s1/messages/m1/attachments/a2"},
      {attachment_id:"a3",name:"<script>alert(1)</script>",media_type:"image/png",size:300,kind:"image",content_url:"javascript:alert(1)"},
    ],
  },
}));
assert.match(attachmentMessage, /已读取正文/);
assert.match(attachmentMessage, /2 页/);
assert.match(attachmentMessage, /loading="lazy"/);
assert.ok(!attachmentMessage.includes('javascript:') && !attachmentMessage.includes('<script>'));
assert.match(attachmentMessage, /研究这份财报/);
const message = {
  message_id: "m-test",
  role: "assistant",
  text: "",
  state: "completed",
  created_at_ms: 1000,
  interpretation: {
    summary: "保留原文 <img src=x onerror=alert(1)>",
    selected_claim_ids: ["c1"],
    hypotheses: [
      {
        text: "原有解释不改写",
        supporting_evidence_ids: ["e1"],
        counter_evidence_ids: [],
        metric_refs: ["m1"],
        open_question: "原有问题",
      },
    ],
    questions: [{ text: "原有待确认事项", evidence_ids: ["e1"] }],
  },
};
const before = structuredClone(context);
const legacy = renderToStaticMarkup(
  createElement(AIMessageView, { message, context }),
);
for (const text of [
  "保留原文",
  "原有解释不改写",
  "原有问题",
  "来源",
  "合成测试来源",
])
  assert.ok(legacy.includes(text));
assert.ok(
  !legacy.includes("<img") &&
    !legacy.includes("NEVER_RENDER_RAW_JSON") &&
    !legacy.includes("旧版回答格式"),
);
assert.match(legacy, /<details class="ai-message-more">/);
assert.match(legacy, /<details class="ai-answer-sources">/);
assert.deepEqual(context, before);
const modern = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: {
      ...message,
      interpretation: {
        answer: [block],
        follow_up_questions: ["还能确认什么？"],
      },
    },
    context,
    onAsk: () => {},
  }),
);
assert.ok(
  modern.includes("-1.32%") &&
    !modern.includes("{{metric:") &&
    !modern.includes("<script>"),
);
assert.ok(modern.includes("还能确认什么？"));
const source = {
  source_id: "s1",
  url: "https://www.cninfo.com.cn/new/test",
  title: "合成原生来源",
  published_at: null,
  accessed_at_ms: 2000,
};
const web = {
  allowed: true,
  used: true,
  search_call_count: 1,
  queried_at_ms: 2000,
  requested_as_of: "2026-10-30",
  validation: "native_citations_only",
};
const agent = {
  ...message,
  interpretation: null,
  response_type: "agent_answer",
  text: "甲😀乙公告。https://not-native.example/ 不自动链接。<img onerror=alert(1)>",
  source_context: {
    web,
    tool_results: [{ state: "available", data: { raw: "NOT_VISIBLE" } }],
  },
  tool_traces: [
    {
      name: "internal_query",
      args: { secret: "NEVER_SHOW_ARGS" },
      label: "已读取本地资料",
      state: "completed",
    },
  ],
  citations: [
    {
      start_index: 1,
      end_index: 3,
      url: source.url,
      source_id: "s1",
      title: source.title,
    },
  ],
  sources: [source],
};
assert.equal(nativeWebCitations(agent).length, 1);
const html = renderToStaticMarkup(
  createElement(AIMessageView, { message: agent, context }),
);
assert.match(html, /甲😀乙<sup/);
assert.ok(
  html.includes('href="https://www.cninfo.com.cn/new/test"') &&
    !html.includes('href="https://not-native.example/"'),
);
assert.ok(html.includes('rel="noopener noreferrer"') && !html.includes("<img"));
for (const phrase of [
  "NEVER_SHOW_ARGS",
  "NOT_VISIBLE",
  "已读取本地资料",
  "协议",
  "冻结",
  "资料范围",
  "已检索网页",
])
  assert.ok(!html.includes(phrase), phrase);
const localAgent = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: {
      ...agent,
      source_context: { tool_results: [] },
      sources: [],
      citations: [],
    },
    context,
  }),
);
assert.ok(
  !localAgent.includes("ai-answer-sources"),
  "tool traces alone are not factual sources",
);
const streaming = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: {
      ...agent,
      state: "streaming",
      text: "<img src=x> **尚未闭合 [",
      partial: true,
    },
    context,
  }),
);
assert.ok(
  !streaming.includes("<img") &&
    !streaming.includes("href=") &&
    !streaming.includes("ai-answer-sources"),
);
assert.ok(streaming.includes("ai-stream-cursor"));
const cancelled = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: { ...agent, state: "cancelled", partial: true },
    context,
  }),
);
assert.ok(
  cancelled.includes("回答已停止") && cancelled.includes("已生成的片段"),
);
const timedOut = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: {
      ...agent,
      state: "failed",
      reason_code: "timeout",
      partial: true,
      text: "中断前的市场情绪片段",
      citations: [],
    },
    context,
  }),
);
assert.match(timedOut, /回答未完成/);
assert.match(timedOut, /本次回答超时/);
assert.match(timedOut, /已生成的片段仅供核对/);
assert.match(timedOut, /中断前的市场情绪片段/);
assert.doesNotMatch(timedOut, /请查看本地任务状态/);
assert.equal(safeHttpUrl("javascript:alert(1)"), null);
assert.equal(safeHttpUrl("data:text/html,test"), null);
const md = {
  ...agent,
  text: "😀[原生资料标签](https://model-written.example/test)的说明。",
  citations: [{ ...agent.citations[0], start_index: 1, end_index: 16 }],
};
const orig = structuredClone(md);
const parts = webAnswerParts(md);
assert.equal(
  parts
    .filter((p) => p.text != null)
    .map((p) => p.text)
    .join(""),
  "😀原生资料标签的说明。",
);
assert.deepEqual(md, orig);
for (const changed of [
  { ...agent, source_context: { web: { ...web, used: false } } },
  { ...agent, citations: [{ ...agent.citations[0], end_index: 9999 }] },
  { ...agent, citations: [{ ...agent.citations[0], source_id: "other" }] },
])
  assert.equal(nativeWebCitations(changed).length, 0);
const locationUrl = encode(await compile("./aiChatLocation.ts"));
const { readChatLocation, chatLocation } = await import(locationUrl);
const hash = chatLocation(
  "#market?date=2026-10-30",
  { kind: "market", asOf: "2026-10-30" },
  "task-chat",
);
assert.equal(
  readChatLocation(hash, { kind: "market", asOf: "2026-10-30" }),
  "task-chat",
);
assert.equal(
  readChatLocation(hash, { kind: "market", asOf: "2026-10-29" }),
  "",
);
assert.ok(
  chatLocation(hash, { kind: "market" }, "").includes("date=2026-10-30"),
);
assert.equal(
  readChatLocation("#research?ai_chat=%2Fbad", { kind: "stock" }),
  "",
);
const originalFetch = globalThis.fetch;
globalThis.fetch = () => {
  throw Error("render must not fetch");
};
renderToStaticMarkup(createElement(AIMessageView, { message: agent, context }));
const archiveAnswer = renderToStaticMarkup(
  createElement(AIMessageView, {
    context,
    message: {
      ...agent,
      sources: [],
      citations: [],
      source_context: {
        tool_results: [
          {
            tool_name: "read_news_events",
            evidence: [
              {
                title: "一条已留存消息",
                source: "财联社电报",
                data: { url: "https://www.cls.cn/detail/123" },
              },
              {
                title: "无效消息",
                data: { url: "javascript:alert(1)" },
              },
            ],
          },
          {
            tool_name: "read_market_context",
            evidence: [{ data: { url: "https://example.org/ignored" } }],
          },
        ],
      },
    },
  }),
);
assert.match(archiveAnswer, /本机归档原文/);
assert.match(archiveAnswer, /href="https:\/\/www\.cls\.cn\/detail\/123"/);
assert.match(archiveAnswer, /一条已留存消息/);
assert.doesNotMatch(archiveAnswer, /无效消息|example.org\/ignored/);
let entry = await compile("./AIEntry.tsx");
for (const [name, url] of Object.entries({
  react: import.meta.resolve("react"),
  "react/jsx-runtime": runtime,
  "lucide-react": import.meta.resolve("lucide-react"),
  "../../components/UI": encode("export const Loading=()=>null;"),
  "./aiChatLocation": locationUrl,
  "./aiNavigation": encode(await compile("./aiNavigation.ts")),
}))
  entry = entry.replaceAll(
    'from "' + name + '"',
    "from " + JSON.stringify(url),
  );
entry = entry.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
const { default: AIEntry } = await import(encode(entry));
const shortcut = renderToStaticMarkup(
  createElement(AIEntry, { kind: "market", asOf: "2026-10-30" }),
);
assert.match(shortcut, /href="#ai\?ai_kind=market&amp;ai_day=2026-10-30"/);
assert.ok(!shortcut.includes("textarea") && !shortcut.includes("ai-workspace"));
const stageShortcut = renderToStaticMarkup(
  createElement(AIEntry, {
    kind: "market",
    asOf: "2026-10-30",
    marketStage: "1430",
  }),
);
assert.match(stageShortcut, /AI 解读当前时点/);
assert.match(
  stageShortcut,
  /href="#ai\?ai_kind=market&amp;ai_day=2026-10-30&amp;ai_stage=1430"/,
);
const newsShortcut = renderToStaticMarkup(
  createElement(AIEntry, {
    kind: "market",
    asOf: "2026-11-10",
    newsContext: { kind: "news", source: "财联社电报", id: "123" },
  }),
);
assert.match(newsShortcut, /AI 解读这条消息/);
assert.match(newsShortcut, /ai_day=2026-11-10&amp;ai_news=news.*ai_item=123/);
const noApi = encode(
  'export function useApi(){throw Error("hook not expected")} export function usePageActive(){return false}',
);
const tabSyncUrl = encode(await compile("./aiTabSync.ts"));
let client = await compile("./aiApi.ts");
for (const [name, url] of Object.entries({
  react: import.meta.resolve("react"),
  "../../lib/api": noApi,
  "./aiState": helperUrl,
  "./aiTabSync": tabSyncUrl,
}))
  client = client.replaceAll(
    'from "' + name + '"',
    "from " + JSON.stringify(url),
  );
const { aiWrite } = await import(encode(client));
let calls = 0;
globalThis.fetch = async () => {
  calls++;
  return new Response(
    JSON.stringify({
      detail: {
        reason_code: "quota_exhausted",
        message: "DO_NOT_SHOW_PROVIDER_BODY",
      },
    }),
    { status: 429 },
  );
};
await assert.rejects(aiWrite("/sessions", { text: "test" }), /额度|余额/);
assert.equal(calls, 1);
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      detail: {
        origin: "local",
        reason_code: "business_unavailable",
        message: "交易日历暂未核验，请稍后再试",
      },
    }),
    { status: 409 },
  );
await assert.rejects(aiWrite("/sessions", {}), /交易日历暂未核验/);
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      detail: { reason_code: "unknown", message: "DO_NOT_SHOW_PROVIDER_BODY" },
    }),
    { status: 500 },
  );
await assert.rejects(
  aiWrite("/sessions", {}),
  (e) =>
    !e.message.includes("DO_NOT_SHOW") &&
    !e.message.includes("[object Object]"),
);
globalThis.fetch = async () =>
  new Response(
    JSON.stringify({
      detail: {
        origin: "local",
        reason_code: "session_busy",
        message: "本轮问题仍在处理",
      },
    }),
    { status: 409 },
  );
await assert.rejects(
  aiWrite("/sessions/active/messages", { text: "同会话草稿" }),
  (error) =>
    error.status === 409 &&
    error.reason === "session_busy" &&
    /等待/.test(error.message),
);
assert.equal(
  sessionMatches(
    { provider_id: "openai", model: "gpt-6.1-sol" },
    provider,
    false,
  ),
  false,
);
const { publishAIChange, subscribeAIChanges } = await import(tabSyncUrl);
const oldWindow = globalThis.window;
const oldDocument = globalThis.document;
globalThis.window = Object.assign(new EventTarget(), { BroadcastChannel });
globalThis.document = Object.assign(new EventTarget(), {
  visibilityState: "visible",
});
const notifications = [];
let received;
const nextNotice = new Promise((resolve) => {
  received = resolve;
});
const originalInterval = globalThis.setInterval;
let idlePolls = 0;
globalThis.setInterval = (...args) => {
  idlePolls++;
  return originalInterval(...args);
};
const unsubscribe = subscribeAIChanges((change) => {
  notifications.push(change);
  received();
});
try {
  globalThis.document.visibilityState = "hidden";
  globalThis.document.dispatchEvent(new Event("visibilitychange"));
  await assert.rejects(
    aiWrite("/sessions/active/messages", { text: "未受理" }),
  );
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({ task_id: "accepted", session_id: "active" }),
      {
        status: 202,
      },
    );
  await aiWrite("/sessions/active/messages", { text: "只发请求、不广播草稿" });
  const timeout = AbortSignal.timeout(2000);
  await Promise.race([
    nextNotice,
    new Promise((_, reject) =>
      timeout.addEventListener("abort", () => reject(timeout.reason), {
        once: true,
      }),
    ),
  ]);
  assert.deepEqual(notifications, [{ scope: "session", id: "active" }]);
  globalThis.document.visibilityState = "visible";
  globalThis.document.dispatchEvent(new Event("visibilitychange"));
  globalThis.window.dispatchEvent(new Event("focus"));
  assert.equal(idlePolls, 0);
  assert.equal(notifications.length, 1);
} finally {
  unsubscribe();
  globalThis.setInterval = originalInterval;
  globalThis.window = oldWindow;
  globalThis.document = oldDocument;
  globalThis.fetch = originalFetch;
}

const fallbackWrites = [];
globalThis.window = Object.assign(new EventTarget(), {
  localStorage: {
    setItem: (key, value) => fallbackWrites.push({ key, newValue: value }),
    removeItem: (key) => fallbackWrites.push({ key, newValue: null }),
  },
});
const fallbackNotices = [];
const stopFallback = subscribeAIChanges((change) =>
  fallbackNotices.push(change),
);
try {
  publishAIChange({ scope: "session", id: "active", usageChanged: true });
  assert.equal(fallbackWrites.length, 2);
  for (const fields of fallbackWrites)
    window.dispatchEvent(Object.assign(new Event("storage"), fields));
  assert.deepEqual(fallbackNotices, [
    { scope: "session", id: "active", usageChanged: true },
  ]);
} finally {
  stopFallback();
  globalThis.window = oldWindow;
}
console.log(
  "AI minimal chat, legacy reading, native citations, partial safety, numeric references and safe errors passed",
);

const markdown =
  "**结论😀**\n\n### 午间表现\n\n| 指标 | 情况 |\n|---|---:|\n| 合成数值 | **1** |\n\n- **项目一**\n- [模型链接](https://never-native.example/)\n\n2. 第二项\n9. 第九项\n\n<img src=x onerror=alert(1)>";
const endAt = Array.from(
  markdown.slice(0, markdown.indexOf("**1**") + 5),
).length;
const marked = {
  ...agent,
  text: markdown,
  citations: [
    { ...agent.citations[0], start_index: endAt - 5, end_index: endAt },
  ],
};
const formatted = renderToStaticMarkup(
  createElement(AIAnswerText, { message: marked }),
);
for (const piece of [
  "<strong>结论😀</strong>",
  "<h4",
  "<table",
  "<thead",
  "<tbody",
  "<ul",
  "<ol",
  'value="9"',
])
  assert.ok(formatted.includes(piece), piece);
assert.ok(
  formatted.includes("<strong>1</strong><sup"),
  "native citation stays with the table cell after Markdown formatting",
);
assert.equal((formatted.match(/href=/g) || []).length, 1);
assert.ok(
  !formatted.includes('href="https://never-native.example/') &&
    !formatted.includes("<img"),
);
assert.ok(!formatted.includes("**结论") && !formatted.includes("### 午间"));
console.log(
  "Safe Markdown paragraphs/bold/headings/lists/tables and native citation placement passed",
);

const renderStreaming = (text) =>
  renderToStaticMarkup(
    createElement(AIMessageView, {
      message: { ...marked, text, state: "streaming" },
      context,
    }),
  );
const liveOutline = "### 竞价与午评\n\n- **竞价**：合成样本\n- 午间：合成观察";
const liveOutlineHtml = renderStreaming(liveOutline);
for (const fragment of [
  'class="ai-stream-text"',
  'aria-busy="true"',
  'class="ai-safe-markdown"',
  '<h4 class="ai-markdown-heading"',
  "<strong>竞价</strong>",
  "<ul>",
  "ai-stream-cursor",
])
  assert.ok(liveOutlineHtml.includes(fragment), fragment);
assert.ok(!liveOutlineHtml.includes("### 竞价与午评"));
assert.ok(!liveOutlineHtml.includes("**竞价**"));

// Each prefix can arrive on its own SSE chunk, including unfinished delimiters.
// Rendering must stay safe and keep the partial words until closure arrives.
const prefixSample =
  "### 午间表现\n\n**合成结论😀**\n\n| 指标 | 情况 |\n|---|---:|\n| 合成数值 | **1** |\n\n- [仅文字](https://never-native.example/)\n\n<img src=x onerror=alert(1)>";
for (let length = 1; length <= prefixSample.length; length++) {
  const renderedPrefix = renderStreaming(prefixSample.slice(0, length));
  assert.ok(renderedPrefix.includes("ai-stream-cursor"));
  assert.ok(!renderedPrefix.includes("<img"));
  assert.ok(!renderedPrefix.includes("href="));
  assert.ok(!renderedPrefix.includes("ai-answer-sources"));
}
assert.ok(renderStreaming("**合成尚未闭合").includes("合成尚未闭合"));
assert.ok(
  renderStreaming("**合成尚未闭合**").includes("<strong>合成尚未闭合</strong>"),
);
const growingTable = renderStreaming(
  "| 指标 | 情况 |\n|---|---:|\n| 合成数值 | **1",
);
assert.ok(growingTable.includes("<table") && growingTable.includes("合成数值"));
const liveTableHtml = renderStreaming(markdown);
assert.ok(
  liveTableHtml.includes("<table") &&
    liveTableHtml.includes("<strong>1</strong>"),
);
assert.equal((liveTableHtml.match(/href=/g) || []).length, 0);
const completedAfterStream = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: { ...marked, state: "completed" },
    context,
  }),
);
assert.ok(completedAfterStream.includes("<strong>1</strong><sup"));
assert.ok(!completedAfterStream.includes("ai-stream-cursor"));
console.log(
  "Streaming Markdown prefixes/headings/lists/growing tables remain safe; citations appear only after completion passed",
);

const marketMarkdown = [
  "**上证 -1.09%**，上涨866家，下跌4,600只，封板24家、5连板、炸板≥3家，缩量 669.90 亿元。",
  "",
  "| 项目 | 数值 |",
  "|---|---:|",
  "| 涨跌幅 | ＋2.15％ |",
  "| 上涨/下跌/平盘 | 866 / 4,600 / 106（有效5,572家） |",
  "| 普通数量 | 1 / 2 / 3 |",
  "",
  "- 平盘 +0.00%，涨停0家，区间10-20%，代码002578.SZ，金额4,767亿元。",
  "- `+4.00%`",
  "```",
  "炸板2家，-6.00%",
  "```",
].join("\n");
for (const html of [
  renderToStaticMarkup(createElement(AIAnswerText, { text: marketMarkdown })),
  renderStreaming(marketMarkdown),
]) {
  for (const [tone, part] of [
    ["down", "-1.09%"],
    ["down", "下跌4,600只"],
    ["down", "缩量 669.90 亿元"],
    ["up", "上涨866家"],
    ["up", "封板24家"],
    ["up", "5连板"],
    ["up", "＋2.15％"],
    ["up", "866"],
    ["down", "4,600"],
    ["is-board-warning", "炸板≥3家"],
  ]) {
    assert.ok(
      html.includes(`<span class="ai-market-value ${tone}">${part}</span>`),
      part,
    );
  }
  assert.equal((html.match(/class="ai-market-value /g) || []).length, 10);
  assert.ok(html.includes("<code>+4.00%</code>"));
  assert.ok(html.includes("<code>炸板2家，-6.00%</code>"));
}
assert.ok(!renderStreaming("上证 -1.09").includes("ai-market-value"));
const userWithMetrics = renderToStaticMarkup(
  createElement(AIMessageView, {
    message: {
      ...marked,
      role: "user",
      text: "涨停24家，-1.09%",
      state: "completed",
    },
    context,
  }),
);
assert.ok(!userWithMetrics.includes("ai-market-value"));
console.log(
  "AI market colors apply to streamed and saved Markdown while preserving plain and code text",
);
