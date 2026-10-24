import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (source) => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const compile = async (path) => ts.transpileModule(await fs.readFile(new URL(path, import.meta.url), "utf8"), {
  fileName: path,
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const ladder = encode(await compile("./ladderPresentation.ts"));
const helperUrl = encode((await compile("./boardPresentation.ts")).replaceAll('from "./ladderPresentation"', "from " + JSON.stringify(ladder)));
const { boardTags, previousBoardHeight } = await import(helperUrl);
const component = encode((await compile("./BoardTags.tsx")).replaceAll('from "react/jsx-runtime"', "from " + JSON.stringify(import.meta.resolve("react/jsx-runtime"))).replaceAll('from "./boardPresentation"', "from " + JSON.stringify(helperUrl)).replace(/^import ["'][^"']+\.css["'];?$/gm, ""));
const { default: BoardTags } = await import(component);
const day = "2026-01-05", previous = "2026-01-02", snapshot = "synthetic-generation";
const row = {
  instrument_id: "600001.SH", date: day, snapshot_id: snapshot,
  board_count: 1, previous_board_date: previous,
  board_presentation: {
    version: "dated-stock-board-presentation-v1", date: day, snapshot_id: snapshot, instrument_id: "600001.SH",
    height: { value: 1, label: "首板", state: "confirmed", source_date: day, reason: "合成来源确认高度" },
    previous_height: { value: 2, label: "二板", state: "confirmed", source_date: previous, reason: "前一交易日来源高度" },
    types: [{ code: "earth_to_sky", label: "地天板", state: "confirmed", source_date: day, reason: "合成同日原价证据" }],
    feedback: { code: "promoted", label: "晋级", state: "confirmed", source_date: day, cohort_date: previous, reason: "前日样本今日继续确认涨停" },
    volume: { state: "contracted", label: "缩量", ratio: 0.8, previous_date: previous, reason: "严格相邻日可比成交量" },
  },
};
const before = structuredClone(row);
assert.deepEqual(boardTags(row, day, snapshot).map((tag) => [tag.dimension, tag.label]), [["height", "首板"], ["type", "地天板"], ["volume", "缩量"]]);
assert.deepEqual(boardTags(row, day, snapshot, { height: false, previousHeight: true, types: false, volume: false }).map((tag) => tag.label), ["昨二板"]);
assert.equal(previousBoardHeight({ ...row, previous_board_count: 99 }, day, snapshot), 2);
for (const update of [{ value: 2.5 }, { state: "unknown" }, { source_date: "2025-12-31" }, { source_date: day }]) {
  const invalid = structuredClone(row); Object.assign(invalid.board_presentation.previous_height, update); invalid.previous_board_count = 99;
  assert.equal(previousBoardHeight(invalid, day, snapshot), null);
  assert.deepEqual(boardTags(invalid, day, snapshot, { height: false, previousHeight: true, types: false, volume: false }), [], "a displayed unknown height cannot remain a large table-sort height");
}
assert.deepEqual(boardTags({ instrument_id: row.instrument_id, previous_board_count: 3 }, day, snapshot, { height: false, previousHeight: true, types: false, volume: false }).map((tag) => tag.label), ["昨3板"]);
assert.deepEqual(boardTags(row, day, snapshot, { height: false, types: false, volume: false, feedback: true }).map((tag) => tag.label), ["晋级"]);
for (const mutate of [
  (value) => { value.board_presentation.date = previous; },
  (value) => { value.board_presentation.version = "future"; },
  (value) => { value.board_presentation.snapshot_id = "other"; },
  (value) => { value.board_presentation.instrument_id = "600002.SH"; },
  (value) => { value.date = previous; },
  (value) => { value.evidence_date = previous; },
  (value) => { value.snapshot_id = "other"; },
]) {
  const invalid = structuredClone(row); mutate(invalid);
  assert.deepEqual(boardTags(invalid, day, snapshot), [], "same-day/generation/instrument binding is mandatory");
}
assert.deepEqual(boardTags(row, previous, snapshot), []);
assert.deepEqual(boardTags(row, day, undefined), []);
assert.deepEqual(boardTags({ instrument_id: "600001.SH", date: day, board_count: 3, change_pct: 10, volume: 10, previous_volume: 20 }, day, snapshot), [], "raw counts, returns and volume cannot create a board shape or first board");
const missing = structuredClone(row);
missing.board_presentation.height.state = "unknown";
missing.board_presentation.types[0].state = "unknown";
missing.board_presentation.volume.state = "unknown";
missing.board_presentation.feedback.state = "not_applicable";
assert.deepEqual(boardTags(missing, day, snapshot, { feedback: true }), [], "unknown facts never become zero/first board/flat/contracted");
for (const value of [0, -1, 1.5, true, null]) {
  const invalid = structuredClone(row); invalid.board_presentation.height.value = value;
  assert.ok(!boardTags(invalid, day, snapshot).some((tag) => tag.dimension === "height"));
}
for (const mutate of [
  (value) => { value.board_presentation.volume.ratio = 2; },
  (value) => { value.board_presentation.volume.ratio = 0; },
  (value) => { value.board_presentation.volume.previous_date = day; },
  (value) => { value.board_presentation.volume.state = "toString"; },
]) {
  const invalid = structuredClone(row); mutate(invalid);
  assert.ok(!boardTags(invalid, day, snapshot).some((tag) => tag.dimension === "volume"));
}
for (const code of ["positive", "limit_down", "large_loss", "negative", "flat"]) {
  const feedback = structuredClone(row); feedback.board_presentation.feedback.code = code;
  const tags = boardTags(feedback, day, snapshot, { height: false, types: false, volume: false, feedback: true });
  assert.equal(tags.length, 1);
  assert.ok(!tags[0].label.includes("承接"), "positive price alone must not claim sustained buying support");
}
for (const update of [{ code: "toString" }, { cohort_date: day }, { source_date: previous }, { state: "unknown" }]) {
  const invalid = structuredClone(row); Object.assign(invalid.board_presentation.feedback, update);
  assert.ok(!boardTags(invalid, day, snapshot, { feedback: true }).some((tag) => tag.dimension === "feedback"));
}
const otherTypes = structuredClone(row);
otherTypes.board_presentation.types.push({ code: "first_board", label: "不能混入的高度", state: "confirmed", source_date: day });
otherTypes.board_presentation.types.push({ ...otherTypes.board_presentation.types[0] });
assert.equal(boardTags(otherTypes, day, snapshot).filter((tag) => tag.dimension === "type").length, 1, "height is not a type and same-day type duplicates collapse");
const broken = structuredClone(row);
broken.board_presentation.types = [{ code: "broken_pool_source", label: "炸板来源样本", state: "confirmed", source_date: day, reason: "同日来源及原始收盘核验，不还原完整路径" }];
assert.ok(boardTags(broken, day, snapshot).some((tag) => tag.label === "炸板来源样本"), "a confirmed broken-pool source label is distinct from an open-count observation");
broken.board_presentation.types[0].source_date = previous;
assert.ok(!boardTags(broken, day, snapshot).some((tag) => tag.label === "炸板来源样本"));
const markup = renderToStaticMarkup(createElement(BoardTags, { row, date: day, snapshotId: snapshot, feedback: true }));
for (const dimension of ["height", "type", "volume", "feedback"]) assert.ok(markup.includes('data-board-dimension="' + dimension + '"'));
assert.ok(markup.includes("前日样本今日继续确认涨停"));
assert.ok(!markup.includes("换手板") && !markup.includes("烂板"));
const crowded = structuredClone(row);
for (const [code, label] of [["broken_pool_source", "炸板来源样本"], ["open_count", "开板4次"], ["repeated_break", "反复开板"], ["non_one_price_limit_up", "非一字涨停"]]) crowded.board_presentation.types.push({ code, label, state: "confirmed", source_date: day });
const compact = renderToStaticMarkup(createElement(BoardTags, { row: crowded, date: day, snapshotId: snapshot }));
assert.ok(compact.includes('class="board-tags-more"') && compact.includes("另2项"));
const primary = compact.split('<details class="board-tags-more">')[0];
assert.ok(primary.includes("炸板来源样本") && primary.includes("缩量") && primary.includes("首板"), "height, volume and the actual broken-pool fact stay visible while secondary types fold");
assert.equal(renderToStaticMarkup(createElement(BoardTags, { row, date: previous, snapshotId: snapshot })), "");
assert.deepEqual(row, before);
console.log("shared board tags: independent height/type/volume/feedback, dated generation guards, unknown evidence and no inferred turnover/support passed");
