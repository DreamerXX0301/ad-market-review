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
  auction: {
    enabled: false,
    revision: 0,
    mode: "off",
    fixed_time: "09:25:30",
    window_end: "09:30:00",
    can_enable: true,
    legacy_configuration: false,
    legacy_instrument_count: 0,
  },
};
assert.equal(p.automaticReviewSettings(base), base);
assert.equal(
  p.automaticStatusText("唯一worker未运行，API/worker需启动"),
  "采集服务未运行，服务需启动",
);
assert.equal(p.automaticStatusText("工作进程在线"), "采集服务在线");
assert.equal(p.automaticStatusText(null, "状态未知"), "状态未知");
for (const time of ["17:00", "18:00", "18:30"]) {
  const saved = { ...base, after_close: { enabled: false, time } };
  assert.equal(
    p.automaticReviewSettings(saved).after_close.time,
    time,
    "saved schedule is never replaced by UI defaults",
  );
}

for (const value of [
  null,
  {},
  [],
  { ...base, timezone: "UTC" },
  { ...base, revision: -1 },
  { ...base, revision: 1.1 },
  { ...base, revision: Number.MAX_SAFE_INTEGER + 1 },
  { ...base, noon: { enabled: 0, time: "12:05" } },
  { ...base, after_close: { enabled: false, time: "16:59" } },
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
for (const t of ["17:00", "17:59", "18:00", "18:30", "23:59"])
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
for (const auction of [
  undefined,
  null,
  [],
  {},
  { ...base.auction, enabled: "false" },
  { ...base.auction, revision: -1 },
  { ...base.auction, fixed_time: "09:25:00" },
  { ...base.auction, can_enable: undefined },
  { ...base.auction, legacy_instrument_count: -1 },
])
  assert.equal(
    p.automaticReviewSettings({ ...base, auction }),
    null,
    "incomplete auction blocks the entire combined form",
  );
const changed = {
  ...base,
  noon: { enabled: true, time: "11:35" },
  after_close: { enabled: true, time: "17:00" },
  auction: { ...base.auction, enabled: true },
};
assert.equal(
  p.automaticSettingsChanged(base, {
    ...base,
    auction: { ...base.auction, enabled: true },
  }),
  true,
);
assert.equal(
  p.automaticSettingsStale(base, {
    ...base,
    auction: { ...base.auction, revision: 1 },
  }),
  true,
);
assert.equal(p.automaticSettingsStale(base, { ...base, revision: 1 }), true);
assert.deepEqual(p.automaticSettingsRequest(changed), {
  expected_revision: 0,
  expected_auction_revision: 0,
  noon: changed.noon,
  after_close: changed.after_close,
  auction: { enabled: true },
});
assert.equal(
  Object.hasOwn(p.automaticSettingsRequest(changed).auction, "fixed_time"),
  false,
);
assert.equal(
  Object.hasOwn(p.automaticSettingsRequest(changed).auction, "instrument_ids"),
  false,
);
const response = {
  ...changed,
  revision: 1,
  auction: { ...changed.auction, revision: 1, mode: "daily" },
};
assert.equal(p.automaticSavedResponse(base, changed, response), response);
for (const bad of [
  { ...response, auction: { ...response.auction, enabled: false } },
  { ...response, noon: { ...response.noon, time: "12:05" } },
  { ...response, revision: 0 },
  { ...response, auction: { ...response.auction, revision: 0 } },
  { ...response, auction: { ...response.auction, mode: "once" } },
  { ...response, auction: { ...response.auction, legacy_configuration: true } },
])
  assert.equal(
    p.automaticSavedResponse(base, changed, bad),
    null,
    "response must confirm all submitted values and both changed revisions",
  );
assert.equal(
  p.automaticSavedResponse(base, base, base),
  base,
  "no-op responses keep revisions unchanged",
);
console.log(
  "Automatic settings: strict saved shape/booleans/revision, Shanghai daily windows, zero revision and explicit dirty changes passed",
);
