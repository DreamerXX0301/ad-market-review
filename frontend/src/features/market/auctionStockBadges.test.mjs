/** Synthetic, local-only event/board metadata and actual tile rendering. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const probe = encode(
  `import{jsx as a,jsxs as b}from ${JSON.stringify(runtime)};export{Fragment}from ${JSON.stringify(runtime)};export const buttons=[];function record(t,p){if(t==='button')buttons.push(p);}export function jsx(t,p,k){record(t,p);return a(t,p,k);}export function jsxs(t,p,k){record(t,p);return b(t,p,k);}`,
);
const cache = new Map();
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const job = (async () => {
    let source = ts.transpileModule(await fs.readFile(url, "utf8"), {
      fileName: url.pathname,
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }).outputText;
    source = source.replace(/^import ["'][^"']+\.css["'];?$/gm, "");
    for (const name of [
      ...new Set(
        [...source.matchAll(/from\s+["']([^"']+)["']/g)].map(
          (match) => match[1],
        ),
      ),
    ]) {
      let target = name === "react/jsx-runtime" ? probe : null;
      if (!target && name.startsWith(".")) {
        for (const extension of [".ts", ".tsx"]) {
          const candidate = new URL(name + extension, url);
          try {
            await fs.access(candidate);
            target = await moduleUrl(candidate);
            break;
          } catch (error) {
            if (error.code !== "ENOENT") throw error;
          }
        }
        if (!target) throw Error("Missing " + name);
      }
      target ||= import.meta.resolve(name);
      source = source
        .replaceAll('from "' + name + '"', "from " + JSON.stringify(target))
        .replaceAll("from '" + name + "'", "from " + JSON.stringify(target));
    }
    return encode(source);
  })();
  cache.set(url.href, job);
  return job;
}
const { auctionStockEvent: event, auctionStockBoard: board } = await import(
  await moduleUrl(
    new URL("./auctionStockBadgePresentation.ts", import.meta.url),
  )
);
const { default: Badges } = await import(
  await moduleUrl(new URL("./AuctionStockBadges.tsx", import.meta.url))
);
const { default: Tiles } = await import(
  await moduleUrl(new URL("./AuctionStockTiles.tsx", import.meta.url))
);
const { buttons } = await import(probe);
const upReason = "严格前日已核验涨停",
  breakReason = "严格前日已核验炸板";
for (const row of [
  {},
  { previous_state: "unknown" },
  { previous_state: "conflict", previous_board_count: 3 },
  { auction_pct: 20 },
  { reasons: ["昨日炸板"] },
  { reasons: ["并非" + breakReason] },
  { previous_state: "unknown", reasons: [upReason] },
  { previous_state: "limit_up", reasons: [breakReason] },
  { previous_state: "limit_break", reasons: [upReason] },
  { reasons: [upReason, breakReason] },
  { previous_board_count: 4, reasons: [breakReason] },
])
  assert.equal(event(row), null);
assert.equal(event({ previous_state: "limit_break" }).label, "昨日炸板");
assert.equal(event({ reasons: [breakReason] }).label, "昨日炸板");
assert.equal(
  event({ previous_state: "limit_break", previous_board_count: 8 }).kind,
  "limit_break",
  "broken boards never become height badges",
);
assert.equal(
  event({ previous_state: "limit_up" }).label,
  "昨日涨停",
  "missing exact height is not a first board",
);
assert.equal(event({ reasons: [upReason] }).label, "昨日涨停");
assert.equal(
  event({ previous_board_count: 8, reasons: [upReason] }).kind,
  "height",
);
for (const [key, label] of Object.entries({
  main: "主板",
  chinext: "创业板",
  star: "科创板",
  beijing: "北交所",
})) {
  const result = board({
    board: key,
    board_label: "不可信名称",
    board_scope: "来源指定分类范围",
    board_reason: "身份来源说明",
  });
  assert.equal(result.key, key);
  assert.equal(result.label, label);
  assert.equal(result.title, "来源指定分类范围；身份来源说明");
}
for (const row of [
  { instrument_id: "688001.SH" },
  { board: "unknown", board_label: "主板" },
  { board: "MAIN" },
  { board: "constructor" },
  { board: null },
  { board: 1 },
])
  assert.equal(
    board(row).label,
    "板块待核验",
    "no code/prefix/label inference",
  );
const render = (row, showEvent = true) =>
  renderToStaticMarkup(createElement(Badges, { row, showEvent }));
let html = render({
  previous_board_count: 8,
  reasons: [upReason],
  board: "star",
  board_scope: "当前分类，非历史身份",
});
assert.match(html, /昨日8板/);
assert.match(html, /科创板/);
assert.match(html, /title="当前分类，非历史身份"/);
assert.doesNotMatch(
  html,
  /昨日涨停/,
  "precise height replaces the generic event label",
);
html = render(
  { previous_board_count: 8, reasons: [upReason], board: "star" },
  false,
);
assert.match(html, /科创板/);
assert.doesNotMatch(
  html,
  /昨日8板|昨日涨停|昨日炸板/,
  "table identity can render only the board",
);
html = render({
  previous_state: "conflict",
  previous_board_count: 8,
  reasons: [upReason, breakReason],
  board: "unknown",
});
assert.match(html, /板块待核验/);
assert.doesNotMatch(
  html,
  /昨日8板|昨日涨停|昨日炸板/,
  "conflict is never promoted to an event",
);
html = render({ previous_state: "limit_break", board: "main" });
assert.match(html, /昨日炸板/);
assert.match(html, /data-previous-event="limit_break"/);
const selected = [];
const quotes = [
  {
    instrument_id: "600001.SH",
    name: "合成涨停卡片",
    state: "observed",
    auction_pct: 2,
    previous_board_count: 8,
    reasons: [upReason],
    board: "main",
    board_scope: "真实来源范围",
  },
  {
    instrument_id: "300001.SZ",
    name: "合成炸板卡片",
    state: "observed",
    auction_pct: 3,
    previous_state: "limit_break",
    reasons: [breakReason],
    board: "chinext",
    board_scope: "真实来源范围",
  },
  {
    instrument_id: "688001.SH",
    name: "合成缺报卡片",
    state: "unknown",
    auction_pct: 99,
    previous_state: "unknown",
    board: "unknown",
  },
];
const before = JSON.stringify(quotes);
buttons.length = 0;
html = renderToStaticMarkup(
  createElement(Tiles, {
    ids: quotes.map((row) => row.instrument_id),
    quotes,
    chartDate: "2026-10-28",
    onStock: (id) => selected.push(id),
  }),
);
assert.equal(JSON.stringify(quotes), before, "tiles preserve frozen evidence");
assert.ok(
  html.indexOf("合成涨停卡片") < html.indexOf("合成炸板卡片"),
  "expanded stocks default to yesterday height before auction percentage",
);
assert.ok(
  html.indexOf("合成涨停卡片") < html.indexOf("合成缺报卡片"),
  "unknown quote remains last",
);
assert.match(html, /昨日8板/);
assert.match(html, /昨日炸板/);
assert.match(html, /创业板/);
assert.match(html, /主板/);
assert.match(html, /板块待核验/);
assert.match(html, /真实来源范围/);
assert.doesNotMatch(
  html,
  /99\.00%/,
  "unknown quote never renders a numeric return",
);
assert.equal(
  (html.match(/data-copy-stock-code="true"/g) || []).length,
  3,
  "actual six-digit copy controls stay present",
);
assert.match(html, /复制六位代码 300001/);
buttons.find((button) => button.children === "合成炸板卡片").onClick();
assert.deepEqual(selected, ["300001.SZ"]);
const css = await fs.readFile(
  new URL("./AuctionStockBadges.css", import.meta.url),
  "utf8",
);
assert.match(css, /flex-wrap:\s*wrap/);
console.log(
  "Auction stock badges: strict prior event/conflicts/exact heights/explicit listing boards/unknown, compact SSR tiles, return ordering, K navigation and existing copy controls passed",
);
