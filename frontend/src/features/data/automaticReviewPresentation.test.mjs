import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./automaticReviewPresentation.ts", import.meta.url),
  "utf8",
);
const code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const p = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const base = {
  timezone: "Asia/Shanghai",
  revision: 0,
  noon: { enabled: false, time: "12:05" },
  after_close: { enabled: false, time: "18:30" },
  last_checks: { noon: null, after_close: null },
};
assert.equal(p.automaticReviewSettings(base), base);
for (const value of [
  null,
  {},
  [],
  { ...base, timezone: "UTC" },
  { ...base, revision: -1 },
  { ...base, revision: 1.1 },
  { ...base, revision: Number.MAX_SAFE_INTEGER + 1 },
  { ...base, noon: { enabled: 0, time: "12:05" } },
  { ...base, after_close: { enabled: false, time: "17:59" } },
])
  assert.equal(
    p.automaticReviewSettings(value),
    null,
    "unknown/malformed is not saved-off defaults",
  );
for (const t of ["11:31", "12:05", "12:59"])
  assert.equal(p.automaticTimeError("noon", t), "");
for (const t of ["11:30", "13:00", "18:30"])
  assert.ok(p.automaticTimeError("noon", t));
for (const t of ["18:00", "18:30", "23:59"])
  assert.equal(p.automaticTimeError("after_close", t), "");
for (const kind of ["noon", "after_close"])
  for (const t of ["", "0:05", "24:00", "12:60", "12:05:00", " 12:05", "-1:00"])
    assert.ok(p.automaticTimeError(kind, t));
assert.equal(
  p.automaticSettingsChanged(base, { ...base, revision: 9 }),
  false,
  "revision alone is not an unsaved setting",
);
assert.equal(
  p.automaticSettingsChanged(base, {
    ...base,
    noon: { ...base.noon, enabled: true },
  }),
  true,
);
assert.equal(
  p.automaticSettingsChanged(base, {
    ...base,
    after_close: { ...base.after_close, time: "19:10" },
  }),
  true,
);
assert.equal(
  base.noon.enabled,
  false,
  "normalization never flips opt-in defaults",
);
console.log(
  "Automatic settings: strict saved shape/booleans/revision, Shanghai daily windows, zero revision and explicit dirty changes passed",
);
