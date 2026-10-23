/** Exercise the restoration effect with controlled React hooks and delayed GETs. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const states = [], refs = [], callbacks = [], effects = [], requested = [];
let cursor = 0, scheduled = [];
const same = (a, b) => a?.length === b?.length && a.every((value, i) => Object.is(value, b[i]));
globalThis.__savedRunHooks = {
  useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial; return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
  useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; },
  useCallback(callback, deps) { const index = cursor++; if (!callbacks[index] || !same(callbacks[index].deps, deps)) callbacks[index] = { callback, deps }; return callbacks[index].callback; },
  useEffect(effect, deps) { const index = cursor++; if (!effects[index] || !same(effects[index].deps, deps)) scheduled.push(() => { effects[index]?.cleanup?.(); effects[index] = { deps, cleanup: effect() }; }); },
};
globalThis.__savedRunApi = (path, options) => new Promise((resolve, reject) => requested.push({ path, options, resolve, reject }));
const encode = (source) => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const react = encode("export const {useState,useEffect,useRef,useCallback}=globalThis.__savedRunHooks;");
const api = encode("export const api=globalThis.__savedRunApi;");
let compiled = ts.transpileModule(await fs.readFile(new URL("./useSavedStrategyRun.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
compiled = compiled.replaceAll('"react"', JSON.stringify(react)).replaceAll('"../../lib/api"', JSON.stringify(api));
const { default: useSavedRun } = await import(encode(compiled));
const a = { strategy_id: "a", latest_run: { run_id: "run-a" } }, b = { strategy_id: "b", latest_run: { run_id: "run-b" } };
function render(strategy = a, target = "stock") { cursor = 0; const result = useSavedRun(target, strategy); const pending = scheduled; scheduled = []; pending.forEach(effect => effect()); return result; }
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
render();
assert.equal(requested[0].path, "/strategy-runs/run-a");
assert.equal(render().restoring, true);
requested[0].resolve({ strategy_id: "a", run_id: "run-a", business_date: "2026-10-20", strategy_version: 7, items: [{ instrument_id: "test-fixture" }] }); await flush();
const restored = render();
assert.equal(restored.run.run_id, "run-a");
assert.equal(restored.restoring, false);
assert.equal(render(b).run, null, "other strategy results cannot flash under the new name");
assert.equal(requested[1].path, "/strategy-runs/run-b");
assert.equal(render().run.run_id, "run-a");
assert.equal(requested[1].options.signal.aborted, true);
requested[1].resolve({ strategy_id: "b", run_id: "run-b" }); await flush();
assert.equal(render().run.run_id, "run-a", "late result from a departed strategy must be ignored");
render().setRun({ strategy_id: "a", run_id: "run-a-new", business_date: "2026-10-21", items: [] });
assert.equal(render().run.run_id, "run-a-new");
const updated = { ...a, latest_run: { run_id: "run-a-latest" } };
render(updated);
assert.equal(render(updated).run.run_id, "run-a-new", "keep last successful data while newer persisted result loads");
requested[2].reject(Error("fixture unavailable")); await flush();
const failed = render(updated);
assert.match(failed.error, /fixture unavailable/);
assert.equal(failed.run.run_id, "run-a-new");
failed.retry(); render(updated);
requested[3].resolve({ strategy_id: "a", run_id: "run-a-latest", items: [] }); await flush();
assert.equal(render(updated).run.run_id, "run-a-latest");
const sector = { strategy_id: "sector", latest_run: { run_id: "sector-saved" } };
render(sector, "sector");
assert.equal(requested[4].path, "/sector-strategy-runs/sector-saved");
assert.equal(requested.every(request => !request.options.method), true, "restoration only reads saved runs");
effects.forEach(effect => effect?.cleanup?.());
console.log("Saved run restoration: GET recovery, strategy isolation, stale response cancellation, retained success, retry and sector endpoint passed");
