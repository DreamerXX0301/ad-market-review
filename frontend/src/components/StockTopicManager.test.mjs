import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

// Execute the real component handlers with a minimal deterministic hook host;
// provider mutations are captured, never sent to a running application.
let state = [], cursor = 0, requests = [], saved = 0, failure = false;
globalThis.__topicManagerHooks = {
  useState(initial) {
    const index = cursor++;
    if (!(index in state)) state[index] = initial;
    return [state[index], next => { state[index] = typeof next === "function" ? next(state[index]) : next; }];
  },
  useApi: () => ({ data: { items: [{ topic_id: "existing", name: "已归属", kind: "concept" }, { topic_id: "other", name: "可补充行业", kind: "industry" }] }, loading: false, error: "", reload: () => {} }),
  list: value => value.items,
  post: async (path, body) => { requests.push({ path, body }); if (failure) throw new Error("写入失败"); return {}; },
};
const source = (await fs.readFile(new URL("./StockTopicManager.tsx", import.meta.url), "utf8"))
  .replace('import { useState } from "react";', 'const { useState } = globalThis.__topicManagerHooks;')
  .replace('import { list, post, useApi } from "../lib/api";', 'const { list, post, useApi } = globalThis.__topicManagerHooks;');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText.replaceAll('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { default: Manager } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const props = { instrumentId: "600001.SH", currentIds: new Set(["existing"]), onSaved: () => saved++, onClose: () => {} };
function render() { cursor = 0; return Manager(props); }
function nodes(tree) { if (Array.isArray(tree)) return tree.flatMap(nodes); if (!tree || typeof tree !== "object") return []; return [tree, ...nodes(tree.props?.children)]; }
const byLabel = label => nodes(render()).find(node => node.props?.["aria-label"] === label);
const save = () => nodes(render()).find(node => node.type === "button" && typeof node.props.onClick === "function" && node.props.children !== "收起");
assert.ok(!nodes(byLabel("要补充的题材")).some(node => node.type === "option" && node.props.value === "existing"));
await save().props.onClick();
assert.equal(requests.length, 0, "unselected existing topic cannot write");
byLabel("要补充的题材").props.onChange({ target: { value: "other" } });
await save().props.onClick();
assert.deepEqual(requests[0], { path: "/topic-members/associate", body: { topic_ids: ["other"], instrument_ids: ["600001.SH"], reason: "用户在个股详情补充当前题材" } });
assert.equal(saved, 1);
state = []; requests = []; saved = 0;
byLabel("补充题材方式").props.onChange({ target: { value: "new" } });
byLabel("新题材名称").props.onChange({ target: { value: "  新观察题材  " } });
await save().props.onClick();
assert.deepEqual(requests[0], { path: "/topics", body: { name: "新观察题材", description: "", instrument_ids: ["600001.SH"] } });
assert.equal(saved, 1, "create-and-associate is one atomic request");
failure = true; saved = 0;
await save().props.onClick();
assert.equal(saved, 0, "failed writes cannot show saved state");
assert.ok(nodes(render()).some(node => node.props?.role === "alert" && node.props.children === "写入失败"));
delete globalThis.__topicManagerHooks;
console.log("stock topic manager: existing/current isolation, one additive association, atomic create, validation and failure handling passed");
