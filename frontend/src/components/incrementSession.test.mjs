import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const values = new Map(), events = [];
let writable = true;
globalThis.localStorage = {
  getItem: key => values.get(key) || null,
  setItem: (key, value) => { if (!writable) throw new Error("quota"); values.set(key, value); },
  removeItem: key => { if (!writable) throw new Error("quota"); values.delete(key); },
};
globalThis.window = { dispatchEvent: event => events.push(event) };
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
const source = await fs.readFile(new URL("./incrementSession.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const { readIncrementSession: read, beginIncrementSession: begin, acceptIncrementSession: accept, rejectIncrementSession: reject, acknowledgeIncrementSession: ack, INCREMENT_SESSION_KEY: key } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
assert.equal(read(), null);
const first = begin("first");
assert.throws(() => begin("second"), /已有盘后增量/, "a second tab must not replace an unacknowledged session");
accept(first, "job-first");
assert.equal(read().taskId, "job-first");
assert.equal(ack("other"), false);
assert.equal(read().requestKey, "first");
assert.equal(ack("first"), true);
const second = begin("second");
accept(first, "stale-job"); reject(first, "stale-error"); ack("first");
assert.equal(read().requestKey, "second", "late old responses and old confirmations cannot overwrite or clear the new session");
assert.equal(read().taskId, undefined);
// The backend may deduplicate two requests onto an existing task. Adopt its
// canonical request key instead of rejecting a valid receipt as a mismatch.
accept(second, "job-canonical", "canonical-key");
assert.equal(read().requestKey, "canonical-key");
assert.equal(ack("canonical-key"), true);
for (const invalid of [{ requestKey: "", createdAt: 1 }, { requestKey: "valid", createdAt: -1 }, { requestKey: "valid", createdAt: 1, taskId: {} }, { requestKey: "valid", createdAt: 1, rejection: {} }]) {
  values.set(key, JSON.stringify(invalid)); assert.equal(read(), null);
}
values.delete(key); writable = false;
const restricted = begin("restricted"); accept(restricted, "job-restricted");
assert.equal(read().taskId, "job-restricted", "storage quotas keep the active tab guarded in memory");
assert.equal(ack("restricted"), true); assert.equal(read(), null);
assert.ok(events.every(event => event.type === "workbench-increment-session"));
delete globalThis.localStorage; delete globalThis.window; delete globalThis.CustomEvent;
console.log("increment sessions: cross-tab guards, canonical deduplication, stale replies, strict persistence and storage failure passed");
