import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const compile = async (path) =>
  ts.transpileModule(
    await fs.readFile(new URL(path, import.meta.url), "utf8"),
    {
      fileName: path,
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
const ladder = encode(await compile("./ladderPresentation.ts"));
const helper = encode(
  (await compile("./boardPresentation.ts")).replaceAll(
    'from "./ladderPresentation"',
    `from ${JSON.stringify(ladder)}`,
  ),
);
const { boardTags, previousBoardHeight } = await import(helper);
const component = encode(
  (await compile("./BoardTags.tsx"))
    .replaceAll(
      'from "react/jsx-runtime"',
      `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
    )
    .replaceAll('from "./boardPresentation"', `from ${JSON.stringify(helper)}`)
    .replace(/^import ["'][^"']+\.css["'];?$/gm, ""),
);
const { default: BoardTags } = await import(component);
const date = "2026-10-23",
  snapshotId = "synthetic-board-text-generation";
const row = {
  instrument_id: "600001.SH",
  data_date: date,
  date,
  snapshot_id: snapshotId,
  previous_board_date: "2026-10-22",
  board_presentation: {
    version: "dated-stock-board-presentation-v1",
    instrument_id: "600001.SH",
    date,
    snapshot_id: snapshotId,
    height: { value: 2, label: "2连板", state: "confirmed", source_date: date },
    previous_height: {
      value: 1,
      label: "首板",
      state: "confirmed",
      source_date: "2026-10-22",
    },
    board_text: {
      label: "5天4板",
      state: "reported",
      source: "fuyao",
      source_date: date,
      window_sessions: 5,
      limit_up_count: 4,
      reason: "合成同日原价收盘核验来源文字",
    },
    types: [
      {
        code: "earth_to_sky",
        label: "地天板",
        state: "confirmed",
        source_date: date,
      },
    ],
  },
};
const original = structuredClone(row);
const tags = boardTags(row, date, snapshotId);
assert.deepEqual(
  tags.slice(0, 2).map((tag) => tag.label),
  ["2连板", "5天4板"],
);
const reported = tags.find((tag) => tag.key === "board_text");
assert.match(reported.detail, /2026-10-23.*扶摇/);
assert.match(reported.detail, /与连续连板高度分别观察/);
assert.equal(
  previousBoardHeight(row, date, snapshotId),
  1,
  "source text cannot change the confirmed sorting height",
);
for (const options of [
  { height: false, previousHeight: true, types: false, volume: false },
  { height: false, types: false, volume: false, feedback: true },
])
  assert.ok(
    !boardTags(row, date, snapshotId, options).some(
      (tag) => tag.key === "board_text",
    ),
    "separate previous-height and feedback columns do not repeat today's source text",
  );
assert.ok(
  !boardTags(row, date, snapshotId, { boardText: false }).some(
    (tag) => tag.key === "board_text",
  ),
);
for (const update of [
  { state: "unknown" },
  { state: "conflict" },
  { source: "other" },
  { source_date: "2026-10-22" },
  { label: "" },
]) {
  const bad = structuredClone(row);
  Object.assign(bad.board_presentation.board_text, update);
  assert.ok(
    !boardTags(bad, date, snapshotId).some((tag) => tag.key === "board_text"),
  );
}
const absent = structuredClone(row);
delete absent.board_presentation.board_text;
assert.ok(
  !boardTags(absent, date, snapshotId).some((tag) => tag.key === "board_text"),
  "a count never invents N days / M limit-ups",
);
for (const [height, label] of [
  [1, "首板"],
  [2, "2连板"],
]) {
  const duplicate = structuredClone(row);
  duplicate.board_presentation.height = {
    value: height,
    label,
    state: "confirmed",
    source_date: date,
  };
  duplicate.board_presentation.board_text.label = label;
  const values = boardTags(duplicate, date, snapshotId);
  assert.equal(values.filter((tag) => tag.label === label).length, 1);
  assert.match(
    values.find((tag) => tag.label === label).detail,
    /扶摇来源板况/,
  );
  assert.ok(
    !boardTags(duplicate, date, snapshotId, { height: false }).some(
      (tag) => tag.label === label,
    ),
  );
}
for (const update of [
  { date: "2026-10-22" },
  { snapshot_id: "other" },
  { instrument_id: "600002.SH" },
]) {
  const invalid = structuredClone(row);
  Object.assign(invalid.board_presentation, update);
  assert.deepEqual(boardTags(invalid, date, snapshotId), []);
}
const html = renderToStaticMarkup(
  createElement(BoardTags, { row, date, snapshotId }),
);
assert.match(html, /data-board-tag="board_text"/);
assert.match(html, /5天4板/);
assert.deepEqual(row, original);

// The actual shared stock summary renders the same source text and height,
// while header-only price shapes stay outside this summary's tag row.
const api = encode(
  `export const useApi=()=>({data:${JSON.stringify(row)},loading:false,error:"",reload(){throw Error("render cannot fetch")}});`,
);
const metrics = encode(await compile("../../components/stockExtraMetrics.ts"));
const summaryModule = encode(
  (await compile("../../components/StockLimitSummary.tsx"))
    .replaceAll(
      'from "react"',
      `from ${JSON.stringify(import.meta.resolve("react"))}`,
    )
    .replaceAll(
      'from "react/jsx-runtime"',
      `from ${JSON.stringify(import.meta.resolve("react/jsx-runtime"))}`,
    )
    .replaceAll(
      'from "lucide-react"',
      `from ${JSON.stringify(import.meta.resolve("lucide-react"))}`,
    )
    .replaceAll('from "../lib/api"', `from ${JSON.stringify(api)}`)
    .replaceAll('from "./stockExtraMetrics"', `from ${JSON.stringify(metrics)}`)
    .replaceAll(
      'from "../features/market/BoardTags"',
      `from ${JSON.stringify(component)}`,
    )
    .replace(/^import ["'][^"']+\.css["'];?$/gm, ""),
);
const { default: Summary } = await import(summaryModule);
const summary = renderToStaticMarkup(
  createElement(Summary, { instrumentId: row.instrument_id, asOf: date }),
);
assert.match(summary, /5天4板/);
assert.match(summary, /2连板/);
assert.ok(
  !summary.includes("地天板"),
  "the stock header's shape badges are not repeated in the summary",
);
const headerOwnedSummary = renderToStaticMarkup(
  createElement(Summary, {
    instrumentId: row.instrument_id,
    asOf: date,
    showBoardTags: false,
  }),
);
assert.ok(
  !headerOwnedSummary.includes("5天4板") &&
    !headerOwnedSummary.includes("2连板") &&
    !headerOwnedSummary.includes("地天板"),
  "entry chart summary leaves board badges to its fixed identity row",
);
assert.match(headerOwnedSummary, /今日涨幅/);
assert.match(headerOwnedSummary, /近30交易日涨幅/);
assert.match(headerOwnedSummary, /年内涨停次数/);
assert.match(headerOwnedSummary, /data-summary-loading="false"/);
console.log(
  "Reported board text: source/date/generation guards, distinct N-day counts, deduplication, unchanged height order and actual stock summary rendering passed",
);
