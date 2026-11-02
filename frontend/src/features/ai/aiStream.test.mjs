import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const code = ts.transpileModule(
  await fs.readFile(new URL("./aiStream.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { createSSEDecoder, emptyStream, applyStreamPacket, readTaskEvents } =
  await import(
    "data:text/javascript;base64," + Buffer.from(code).toString("base64")
  );
const frame = (seq, type, values = {}) => ({
  id: String(seq),
  event: type,
  data: { task_id: "t1", session_id: "s1", seq, type, ...values },
});
const packets = [];
const parser = createSSEDecoder((p) => packets.push(p));
const encoded =
  ": ping\r\n\r\nid: 1\r\nevent: delta\r\ndata: " +
  JSON.stringify(frame(1, "delta", { text: "你好😀" }).data) +
  "\r\n\r\n";
for (const ch of encoded) parser.push(ch);
assert.equal(packets.length, 1);
assert.equal(packets[0].data.text, "你好😀");
let state = emptyStream("t1", "s1");
state = applyStreamPacket(state, frame(1, "delta", { text: "首字" }));
assert.equal(state.text, "首字");
const first = state;
assert.equal(
  applyStreamPacket(state, frame(1, "delta", { text: "重复" })),
  first,
);
for (const change of [
  { task_id: "other" },
  { session_id: "other" },
  { seq: -1 },
])
  assert.equal(
    applyStreamPacket(state, {
      ...frame(2, "delta", { text: "串会话" }),
      data: { ...frame(2, "delta", { text: "串会话" }).data, ...change },
    }),
    first,
  );
state = applyStreamPacket(
  state,
  frame(2, "tool", {
    name: "get_auction",
    state: "running",
    label: "正在查询竞价…",
    call_id: "c",
    args: { secret: "DO_NOT_STORE" },
  }),
);
assert.equal(state.status, "正在查询竞价…");
assert.ok(!JSON.stringify(state).includes("DO_NOT_STORE"));
state = applyStreamPacket(state, frame(3, "delta", { text: "继续" }));
assert.equal(state.text, "首字继续");
state = applyStreamPacket(state, frame(3, "done", { state: "completed" }));
assert.equal(state.finished, true);
assert.equal(state.text, "首字继续");
assert.equal(
  applyStreamPacket(state, frame(4, "delta", { text: "晚到的错误片段" })),
  state,
);
let noEvents = applyStreamPacket(emptyStream("t1", "s1"), {
  id: "1",
  event: "done",
  data: {
    task_id: "t1",
    session_id: "s1",
    seq: 0,
    type: "done",
    state: "cancelled",
    final: true,
  },
});
assert.equal(noEvents.finished, true);
assert.equal(noEvents.terminalState, "cancelled");
const cancelled = applyStreamPacket(
  applyStreamPacket(
    emptyStream("t1", "s1"),
    frame(1, "delta", { text: "未完成段落" }),
  ),
  frame(2, "done", { state: "cancelled" }),
);
assert.equal(cancelled.text, "未完成段落");
assert.equal(cancelled.status, "已停止");
const savedFetch = globalThis.fetch,
  requests = [];
const bytes = new TextEncoder().encode(
  "id: 4\nevent: delta\ndata: " +
    JSON.stringify(frame(4, "delta", { text: "汉字😀" }).data) +
    "\n\nid: 5\nevent: done\ndata: " +
    JSON.stringify(frame(5, "done", { state: "completed" }).data) +
    "\n\n",
);
globalThis.fetch = async (url, options) => {
  requests.push({ url, options });
  return new Response(
    new ReadableStream({
      start(c) {
        for (let i = 0; i < bytes.length; i += 3)
          c.enqueue(bytes.slice(i, i + 3));
        c.close();
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
};
const got = [];
await readTaskEvents("t1", 3, new AbortController().signal, (p) => {
  got.push(p);
  return p.event !== "done";
});
assert.equal(requests[0].options.method, "GET");
assert.ok(requests[0].url.endsWith("after=3"));
assert.equal(got[0].data.text, "汉字😀");
assert.equal(got.length, 2);
const incomplete = [];
const partial = createSSEDecoder((p) => incomplete.push(p));
partial.push('id: 9\nevent: delta\ndata: {"text":"未闭合');
assert.equal(incomplete.length, 0);
globalThis.fetch = async () =>
  new Response(
    'event: error\ndata: {"task_id":"t1","message":"本次回答已不可读取"}\n\n',
    { headers: { "content-type": "text/event-stream" } },
  );
await assert.rejects(
  readTaskEvents("t1", 4, new AbortController().signal, () => {}),
  (e) => e.retryable === false,
);
globalThis.fetch = savedFetch;
console.log(
  "AI SSE UTF-8 framing, scope binding, seq dedup, terminal replay, partial cancellation and GET-only delivery passed",
);
