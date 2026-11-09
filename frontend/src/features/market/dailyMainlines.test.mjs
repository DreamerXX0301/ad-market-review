import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
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
const helpers = encode(await compile("./dailyMainlinePresentation.ts"));
const ladder = encode(await compile("./ladderPresentation.ts"));
const board = encode(
  (await compile("./boardPresentation.ts")).replaceAll(
    'from "./ladderPresentation"',
    "from " + JSON.stringify(ladder),
  ),
);
const boardComponent = encode(
  (await compile("./BoardTags.tsx"))
    .replaceAll('from "react/jsx-runtime"', "from " + JSON.stringify(runtime))
    .replaceAll('from "./boardPresentation"', "from " + JSON.stringify(board))
    .replace(/^import ["'][^"']+\.css["'];?$/gm, ""),
);
const ui = encode(`import {jsx} from ${JSON.stringify(runtime)};
export const Empty=({title,description})=>jsx('div',{className:'test-empty',children:[title,description]});
export const ErrorBox=({message,retry})=>jsx('div',{role:'alert',children:[message,retry?jsx('button',{onClick:retry,children:'重试'},'retry'):null]});
export const Loading=()=>jsx('div',{role:'status',children:'加载中'});`);
// A new runtime API dependency must fail rather than silently revive manual reads.
const noApi = encode(
  `throw Error('readonly mainline rendering must not import or call API hooks');`,
);
let compiled = await compile("./DailyMainlines.tsx");
for (const [name, replacement] of Object.entries({
  "react/jsx-runtime": runtime,
  react: import.meta.resolve("react"),
  "lucide-react": import.meta.resolve("lucide-react"),
  "../../lib/api": noApi,
  "../../components/UI": ui,
  "./dailyMainlinePresentation": helpers,
  "./BoardTags": boardComponent,
}))
  compiled = compiled.replaceAll(
    JSON.stringify(name),
    JSON.stringify(replacement),
  );
const { default: DailyMainlines } = await import(
  encode(compiled.replace('import "./DailyMainlines.css";', ""))
);
const elementByClass = (markup, className) => {
  const opening = new RegExp(
    `<([a-z][a-z0-9]*)\\b[^>]*\\bclass="(?:[^"]*\\s)?${className}(?:\\s[^"]*)?"[^>]*>`,
    "i",
  ).exec(markup);
  if (!opening) return "";
  const tags = new RegExp(`<(/?)${opening[1]}\\b[^>]*>`, "gi");
  tags.lastIndex = opening.index + opening[0].length;
  let depth = 1;
  for (let tag; (tag = tags.exec(markup));) {
    depth += tag[1] ? -1 : 1;
    if (!depth) return markup.slice(opening.index, tags.lastIndex);
  }
  throw Error(`Unclosed rendered element: ${className}`);
};
const openingTag = (markup) => markup.slice(0, markup.indexOf(">") + 1);
const date = "2026-01-05",
  snapshotId = "synthetic-generation";
const stock = (id, name, reason, height = 1) => ({
  instrument_id: id,
  name,
  reason,
  date,
  snapshot_id: snapshotId,
  limit_up_state: true,
  close: 10,
  volume: 100,
  price_basis: "none",
  board_count: 999,
  topics: [{ name: "目录概念不得复制成主线" }],
  board_presentation: {
    version: "dated-stock-board-presentation-v1",
    date,
    snapshot_id: snapshotId,
    instrument_id: id,
    height: {
      value: height,
      state: height == null ? "unknown" : "confirmed",
      label: height == null ? null : `${height}板`,
      source_date: date,
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
});
const rows = [
  stock("600001.SH", "合成海洋王", "专业照明+AI照明", 6),
  stock("600002.SH", "合成大业", "业绩增长+人形机器人+机器人电池", 6),
  stock("600003.SH", "合成具身", "具身智能+机械设备", 3),
  stock("600004.SH", "合成机器人甲", "机器人+汽车", null),
  stock("600005.SH", "合成机器人乙", "工业机器人+装备", 2),
  stock("600006.SH", "合成零售甲", "商贸零售+消费", 2),
  stock("600007.SH", "合成零售乙", "零售促销+消费", 1),
  stock("600008.SH", "合成业绩甲", "业绩增长+订单", 2),
  stock("600009.SH", "合成业绩乙", "业绩预增+出口", 1),
  stock(
    "600010.SH",
    "合成长原文",
    "完整原文保留<&>" + "这是未经独立证实的来源叙述".repeat(15),
    null,
  ),
  stock("600011.SH", "合成线索甲", "独立词甲"),
  stock("600012.SH", "合成线索乙", "独立词乙"),
  stock("600013.SH", "合成线索丙", "独立词丙"),
  stock("600014.SH", "合成线索丁", "独立词丁"),
  stock("600015.SH", "合成线索戊", "独立词戊"),
  stock("600016.SH", "合成缺原因甲", null),
  stock("600017.SH", "合成缺原因乙", "未知"),
];
const member = (index, tags = []) => ({
  instrument_id: rows[index].instrument_id,
  source_reason: rows[index].reason,
  matched_tag: tags[0] || null,
  matched_tags: tags,
  source: null,
  source_ref: {
    date,
    snapshot_id: snapshotId,
    endpoint: "/api/v1/market/ladder",
    field_path: `items[instrument_id="${rows[index].instrument_id}"].reason`,
  },
  closing_pool_refs: [
    {
      source: "合成证明",
      path: "private.source.field.path",
      raw_sha256: "b".repeat(64),
      purpose: "closing_event_only",
    },
  ],
});
const group = (key, name, kind, members) => ({
  key,
  name,
  kind,
  grouping_rule: "synthetic-rule-v2",
  state: "candidate",
  labels: [...new Set(members.flatMap((value) => value.matched_tags))],
  count: members.length,
  max_known_height: 999,
  high_stock_ids: ["600004.SH"],
  members,
});
const systemFocus = {
  algorithm_version: "source-reason-directions-v2",
  source_kind: "local_source_synthesis",
  state: "candidate",
  date,
  snapshot_id: snapshotId,
  groups: [
    group("robot", "机器人相关", "direction", [
      member(1, ["人形机器人", "机器人电池"]),
      member(2, ["具身智能"]),
      member(3, ["机器人"]),
      member(4, ["工业机器人"]),
    ]),
  ],
  other_groups: [
    group("retail", "零售", "direction", [
      member(5, ["商贸零售"]),
      member(6, ["零售促销"]),
    ]),
    group("earnings", "业绩线索", "catalyst", [
      member(7, ["业绩增长"]),
      member(8, ["业绩预增"]),
    ]),
  ],
  individual_cues: [
    member(0, ["专业照明"]),
    member(9),
    member(10, ["独立词甲"]),
    member(11, ["独立词乙"]),
    member(12, ["独立词丙"]),
    member(13, ["独立词丁"]),
    member(14, ["独立词戊"]),
  ],
  missing_reason: [member(15), member(16)],
  coverage: {
    scope: "local_confirmed_sample_only",
    confirmed_sample_count: 17,
    focus_count: 4,
    other_count: 4,
    individual_count: 7,
    missing_reason_count: 2,
    rejected_row_count: 0,
  },
  reason: null,
  notice: "合成技术元数据不得展示",
};
const initial = {
  date,
  snapshotId,
  rows,
  systemFocus,
  onStock: () => {},
  onBrowseView: () => {},
  onReload: () => {},
};
const render = (props = {}) =>
  renderToStaticMarkup(createElement(DailyMainlines, { ...initial, ...props }));
const before = structuredClone({ rows, systemFocus });
let html = render();
assert.match(html, /当日主线/);
assert.match(html, /根据当日已发布来源自动归类/);
assert.match(html, />连板梯队</);
assert.match(html, />概念关联</);
assert.match(html, /刷新/);
assert.doesNotMatch(
  html,
  /<input|<select|<textarea|<form|补录|更正|审计|待整理|未归因|人工录入/,
);
assert.doesNotMatch(
  html,
  /目录概念不得复制成主线|private\.source|bbbbbbbbbbbbbbbb|synthetic-rule-v2|source-reason-directions-v2|items\[instrument_id|合成技术元数据/,
);
const summary = elementByClass(html, "daily-mainline-summary");
assert.match(summary, /确认样本 <b>17<\/b> 只/);
assert.match(summary, /重点方向 <b>1<\/b> 个/);
assert.match(summary, /有来源原因 <b>15<\/b> 只/);
const high = elementByClass(html, "daily-mainline-global-high");
assert.match(high, /已核验最高 6板/);
assert.match(high, /合成海洋王/);
assert.match(high, /合成大业/);
assert.match(high, /专业照明\+AI照明/);
assert.doesNotMatch(high, /合成机器人甲|999板|龙头观察/);
assert.match(high, /data-board-tag="type:earth_to_sky"/);
const focus = elementByClass(html, "daily-mainline-focus-groups");
assert.match(focus, /机器人相关/);
assert.match(focus, /4 只/);
assert.match(focus, /本方向已核验最高 6板/);
assert.match(focus, /<strong>零售<\/strong>/);
assert.doesNotMatch(focus, /合成海洋王|业绩线索|核心|后排|999板/);
const representatives = elementByClass(focus, "daily-mainline-representatives");
assert.match(representatives, /合成大业/);
assert.match(representatives, /合成具身/);
assert.doesNotMatch(representatives, /合成机器人甲|合成机器人乙/);
assert.match(representatives, /data-board-tag="type:earth_to_sky"/);
const moreMembers = elementByClass(focus, "daily-mainline-more-members");
assert.ok(moreMembers);
assert.match(openingTag(moreMembers), /\bopen(?:=|\s|>)/);
assert.match(moreMembers, /展开其余 2 只/);
assert.match(moreMembers, /合成机器人甲/);
const other = elementByClass(html, "daily-mainline-other");
assert.match(other, /<h3>事件线索<\/h3>/);
assert.doesNotMatch(other, /零售/);
assert.match(other, /业绩线索/);
assert.match(other, /事件线索/);
assert.doesNotMatch(other, /data-board-tag|核心|龙头/);
const cues = elementByClass(html, "daily-mainline-individual");
assert.match(cues, /7 只/);
const moreCues = elementByClass(cues, "daily-mainline-more-cues");
assert.match(openingTag(moreCues), /\bopen(?:=|\s|>)/);
assert.match(moreCues, /展开其余 2 只个股线索/);
assert.match(moreCues, /合成线索丁|合成线索戊/);
assert.doesNotMatch(cues.replace(moreCues, ""), /合成线索丁|合成线索戊/);
assert.match(cues, /完整原文保留&lt;&amp;&gt;/);
assert.match(cues, /<details><summary>查看原始原因<\/summary>/);
assert.ok(
  cues.includes(
    rows[9].reason
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;"),
  ),
  "the complete source reason remains available behind native disclosure",
);
assert.match(
  elementByClass(html, "daily-mainline-missing"),
  /^<p[^>]*>2 只来源暂未提供明确原因。<\/p>$/,
);
assert.doesNotMatch(html, /合成缺原因甲|合成缺原因乙/);
assert.deepEqual(
  { rows, systemFocus },
  before,
  "rendering never rewrites published inputs",
);

html = render({ query: "合成机器人甲" });
assert.equal(
  elementByClass(html, "daily-mainline-summary"),
  summary,
  "search does not recompute full-date statistics",
);
assert.match(
  elementByClass(html, "daily-mainline-focus-groups"),
  /4 只 · 匹配 1 只/,
);
assert.match(
  elementByClass(html, "daily-mainline-representatives"),
  /合成机器人甲/,
);
assert.doesNotMatch(
  elementByClass(html, "daily-mainline-representatives"),
  /合成大业|合成具身/,
);
assert.match(elementByClass(html, "daily-mainline-global-high"), /合成海洋王/);
html = render({ query: "零售" });
assert.match(
  elementByClass(html, "daily-mainline-focus"),
  /合成零售甲|合成零售乙/,
);
assert.doesNotMatch(
  elementByClass(html, "daily-mainline-focus-groups"),
  /机器人/,
);
html = render({ query: "合成线索戊" });
assert.match(
  elementByClass(html, "daily-mainline-individual"),
  /7 只 · 匹配 1 只/,
);
assert.match(elementByClass(html, "daily-mainline-individual"), /合成线索戊/);
assert.doesNotMatch(
  elementByClass(html, "daily-mainline-individual"),
  /合成线索丁|more-cues/,
);
html = render({ query: "完全没有匹配" });
assert.match(html, /没有匹配的来源线索/);
assert.equal(elementByClass(html, "daily-mainline-summary"), summary);

const relatedRows = structuredClone(rows);
relatedRows[0].reason += "+人形机器人+“羊”字辈";
relatedRows[1].reason += "+“羊”字辈";
const relatedFocus = structuredClone(systemFocus);
relatedFocus.algorithm_version = "source-reason-directions-v3";
relatedFocus.groups[0].members[0].source_reason = relatedRows[1].reason;
relatedFocus.individual_cues[0].source_reason = relatedRows[0].reason;
const relatedMember = (index, tags) => ({
  ...member(index, tags),
  source_reason: relatedRows[index].reason,
});
relatedFocus.related_groups = [
  group("robot", "机器人相关", "direction", [
    ...relatedFocus.groups[0].members,
    relatedMember(0, ["人形机器人"]),
  ]),
  group("sheep", "“羊”字辈", "direction", [
    relatedMember(0, ["“羊”字辈"]),
    relatedMember(1, ["“羊”字辈"]),
  ]),
];
html = render({ rows: relatedRows, systemFocus: relatedFocus });
const directionRows = elementByClass(html, "daily-mainline-direction-list");
assert.equal(directionRows.match(/<strong>机器人相关<\/strong>/g).length, 1);
assert.match(directionRows, /5 只 · 重点 · 来源关联/);
assert.match(directionRows, /<strong>“羊”字辈<\/strong>/);
assert.match(directionRows, /同一只股票可关联多个方向/);
assert.equal(elementByClass(html, "daily-mainline-summary"), summary);
html = render({
  rows: relatedRows,
  systemFocus: relatedFocus,
  query: "羊字",
});
assert.match(
  elementByClass(html, "daily-mainline-focus"),
  /命中标签 · “羊”字辈/,
);
assert.match(elementByClass(html, "daily-mainline-focus"), /合成海洋王/);

for (const mutation of [
  (value) => {
    value.date = "2026-01-02";
  },
  (value) => {
    value.snapshot_id = "old-generation";
  },
  (value) => {
    value.individual_cues[0].source_reason = "不是原始原因";
  },
  (value) => {
    value.missing_reason[0].matched_tag = "伪造";
  },
]) {
  const payload = structuredClone(systemFocus);
  mutation(payload);
  html = render({ systemFocus: payload });
  assert.match(html, /来源暂不可用/);
  assert.equal(elementByClass(html, "daily-mainline-focus-groups"), "");
  assert.doesNotMatch(html, /待整理|更正|补录/);
}
html = render({ systemFocus: undefined });
assert.match(html, /来源暂不可用/);
assert.equal(elementByClass(html, "daily-mainline-focus-groups"), "");
html = render({ date: "2026-01-02" });
assert.match(html, /来源暂不可用/);
assert.doesNotMatch(html, /合成海洋王|合成大业|机器人相关/);
html = render({ loading: true });
assert.match(html, /role="status"/);
assert.doesNotMatch(html, /合成海洋王|机器人相关|确认样本/);
html = render({ error: "合成读取失败" });
assert.match(html, /role="alert"/);
assert.match(html, /合成读取失败/);
assert.match(html, />重试</);
html = render({ onReload: undefined, onBrowseView: undefined });
assert.doesNotMatch(html, />刷新<|>重试<|class="daily-mainline-navigation"/);

// Known group heights and shared board tags must reject stale facts independently.
const staleRows = structuredClone(rows);
for (const row of staleRows)
  row.board_presentation.snapshot_id = "old-generation";
html = render({ rows: staleRows });
assert.match(html, /当前板高信息不足/);
assert.match(
  elementByClass(html, "daily-mainline-focus-groups"),
  /板高信息不足/,
);
assert.doesNotMatch(html, /999板|data-board-tag|>已核验最高 \d/);
const mixedRows = structuredClone(rows);
mixedRows[2].board_presentation.height.state = "unknown";
html = render({ rows: mixedRows });
assert.match(
  elementByClass(html, "daily-mainline-focus-groups"),
  /本方向已核验最高 6板/,
);
assert.doesNotMatch(html, /首板0|连板0/);
const noFocus = structuredClone(systemFocus);
noFocus.other_groups.unshift(noFocus.groups.shift());
noFocus.coverage.other_count += noFocus.coverage.focus_count;
noFocus.coverage.focus_count = 0;
html = render({ systemFocus: noFocus });
assert.match(html, /当日来源尚未形成集中的重点方向/);
assert.match(
  elementByClass(html, "daily-mainline-direction-list"),
  /机器人相关/,
);
assert.doesNotMatch(html, /补录|待整理|未归因/);
const emptyPayload = {
  ...systemFocus,
  groups: [],
  other_groups: [],
  individual_cues: [],
  missing_reason: [],
  coverage: {
    ...systemFocus.coverage,
    confirmed_sample_count: 0,
    focus_count: 0,
    other_count: 0,
    individual_count: 0,
    missing_reason_count: 0,
  },
};
html = render({ rows: [], systemFocus: emptyPayload });
assert.match(html, /确认样本 <b>0<\/b>/);
assert.doesNotMatch(html, /来源暂不可用|补录|待整理/);
console.log(
  "automatic mainlines SSR: readonly four-way layout, all-sample highs, native disclosure, honest counts, search and strict source scope passed",
);
