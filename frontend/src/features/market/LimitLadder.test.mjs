import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const observedRuntime =
  encode(`import {jsx as originalJsx,jsxs as originalJsxs} from ${JSON.stringify(runtime)};
export {Fragment} from ${JSON.stringify(runtime)};
export const stockButtons=[];
function observe(type,props){if(type==='button'&&props.className==='ladder-stock-identity')stockButtons.push(props);}
export function jsx(type,props,key){observe(type,props);return originalJsx(type,props,key);}
export function jsxs(type,props,key){observe(type,props);return originalJsxs(type,props,key);}`);
const ui = encode(`import {jsx} from ${JSON.stringify(runtime)};
export const Badge=({children})=>jsx('span',{children});
export const Empty=({title,description})=>jsx('div',{className:'empty-state',children:[jsx('strong',{children:title},'title'),jsx('p',{children:description},'description')]});
export const ErrorBox=({message})=>jsx('div',{role:'alert',children:message});
export const Loading=()=>jsx('span',{children:'读取中'});
export const Warning=({children})=>jsx('p',{children});
export const Warnings=()=>null;`);
const viewReact =
  encode(`import * as React from ${JSON.stringify(import.meta.resolve("react"))};
export * from ${JSON.stringify(import.meta.resolve("react"))};
let viewOverride=null;
export const changes=[];
export function setViewOverride(value){viewOverride=value;changes.length=0;}
export function useState(initial){const [value]=React.useState(initial==="height"&&viewOverride?viewOverride:initial);return [value,(next)=>changes.push(next)];}`);
const mainlineProbe = encode(`import {jsx} from ${JSON.stringify(runtime)};
export const received=[];
export default function Mainlines(props){received.push(props);return jsx('section',{className:'synthetic-mainline-probe',children:'合成重点视图入口'});}`);
const replacements = {
  "react/jsx-runtime": observedRuntime,
  react: viewReact,
  "lucide-react": import.meta.resolve("lucide-react"),
  "../../components/UI": ui,
  "./MarketDataCoverage": encode(
    "export default function Coverage(){return null;}",
  ),
  "./TopicMembersDialog": encode(
    "export default function Members(){return null;}",
  ),
  "./DailyMainlines": mainlineProbe,
  "./MarketTable": encode(
    `import {jsx} from ${JSON.stringify(runtime)};export function CurrencyCell({value}){return jsx('span',{children:value??'—'});}`,
  ),
  "../../components/CopyStockCode": encode(
    `import {jsx} from ${JSON.stringify(runtime)};export default function Copy({code,instrumentId}){return jsx('button',{'aria-label':'复制代码',children:code||instrumentId});}`,
  ),
};
const moduleCache = new Map();
async function moduleUrl(path) {
  if (moduleCache.has(path.href)) return moduleCache.get(path.href);
  let source = ts.transpileModule(await fs.readFile(path, "utf8"), {
    fileName: path.pathname,
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  for (const match of [...source.matchAll(/from (["'])([^"']+)\1/g)]) {
    const name = match[2];
    const target =
      replacements[name] ||
      (name.startsWith(".")
        ? await moduleUrl(
            await fs
              .access(new URL(name + ".ts", path))
              .then(() => new URL(name + ".ts", path))
              .catch(() => new URL(name + ".tsx", path)),
          )
        : import.meta.resolve(name));
    source = source.replace(match[0], "from " + JSON.stringify(target));
  }
  const result = encode(source.replace(/^import ["'][^"']+\.css["'];?$/gm, ""));
  moduleCache.set(path.href, result);
  return result;
}
const { default: LimitLadder } = await import(
  await moduleUrl(new URL("./LimitLadder.tsx", import.meta.url))
);
const { stockButtons } = await import(observedRuntime);
const selected = [];
const props = {
  date: "2026-10-21",
  loading: false,
  error: "",
  onReload() {
    throw Error("render must not reload data");
  },
  onStock(id) {
    selected.push(id);
  },
};
const stock = (code, name, firstLimit, extra = {}) => ({
  instrument_id: code + ".SH",
  code,
  name,
  first_limit_time: firstLimit,
  board_count: 2,
  ...extra,
});
const data = {
  items: [],
  rebound_items: [
    stock("600002", "反包晚封样本", "10:20:00"),
    stock("600001", "反包早封样本", "09:35:00"),
  ],
  failed_items: [
    stock("600003", "晋级失败样本", null, {
      previous_board_count: 3,
      previous_board_date: "2026-10-20",
    }),
  ],
  twenty_cm_items: [
    stock("600004", "20cm样本", "09:40:00", { twenty_cm_state: true }),
  ],
  classification_coverage: {
    rebound: {
      confirmed_count: 2,
      unknown_count: 1,
      complete: false,
      reason: "反包仍有一只待核验",
    },
    failed: {
      confirmed_count: 1,
      unknown_count: 0,
      complete: true,
      reason: "昨日队列按当前日期核验",
    },
    twenty_cm: {
      confirmed_count: 1,
      unknown_count: 0,
      complete: true,
      reason: "当日制度证据已核验",
    },
  },
};
const before = structuredClone(data);
const markup = renderToStaticMarkup(
  createElement(LimitLadder, { ...props, data }),
);
const sections = [
  ...markup.matchAll(
    /<section class="ladder-classification [^"]+" id="([^"]+)">([\s\S]*?)<\/section>/g,
  ),
];
assert.deepEqual(
  sections.map((match) => match[1]),
  [
    "limit-ladder-2026-10-21-rebound",
    "limit-ladder-2026-10-21-failed",
    "limit-ladder-2026-10-21-twenty_cm",
  ],
  "all three classifications render on the first view without selecting a tab",
);
for (const [index, title, name, count] of [
  [0, "反包板 · 事件候选", "反包早封样本", 2],
  [1, "晋级失败", "晋级失败样本", 1],
  [2, "20cm · 制度已核验", "20cm样本", 1],
]) {
  assert.ok(sections[index][2].includes(title));
  assert.ok(sections[index][2].includes(name));
  assert.ok(
    sections[index][2].includes(
      `<span class="ladder-section-count">${count}<small> 只已确认</small>`,
    ),
  );
  assert.match(
    sections[index][2],
    /<details class="ladder-classification-status"><summary>分类范围与缺口/,
  );
}
assert.ok(
  sections[0][2].indexOf("反包早封样本") <
    sections[0][2].indexOf("反包晚封样本"),
  "each classification retains the shared default sort",
);
assert.match(sections[0][2], /1只待核验/);
assert.match(sections[1][2], /昨日高度<\/dt><dd>3板/);
assert.match(sections[1][2], /2026-10-20/);
assert.doesNotMatch(markup, /ladder-anchor-nav|ladder-class-tabs-caption/);
assert.equal((markup.match(/class="ladder-stock-card"/g) || []).length, 4);
assert.deepEqual(
  data,
  before,
  "displaying categories does not change their source data",
);
for (const name of ["反包早封样本", "晋级失败样本", "20cm样本"]) {
  const button = stockButtons.find(
    (value) => value.children.props.children === name,
  );
  assert.ok(button, name + " remains an interactive stock card");
  button.onClick();
}
assert.deepEqual(selected, ["600001.SH", "600003.SH", "600004.SH"]);
const emptyMarkup = renderToStaticMarkup(
  createElement(LimitLadder, {
    ...props,
    date: "2026-10-19",
    data: {
      items: [],
      classification_coverage: {
        rebound: {
          examined_count: 3,
          confirmed_count: 0,
          unknown_count: 0,
          complete: true,
          reason: "完整队列没有反包",
        },
        failed: {
          examined_count: 3,
          confirmed_count: 0,
          unknown_count: 1,
          complete: false,
          reason: "晋级状态仍未知",
        },
      },
    },
  }),
);
assert.equal(
  (emptyMarkup.match(/class="ladder-classification /g) || []).length,
  3,
  "empty and unknown categories keep independent visible sections",
);
assert.match(emptyMarkup, /id="limit-ladder-2026-10-19-rebound"/);
assert.doesNotMatch(emptyMarkup, /limit-ladder-2026-10-21/);
assert.match(
  emptyMarkup,
  /<span class="ladder-section-count">0<small> 只已确认/,
);
assert.equal(
  (emptyMarkup.match(/ 数量待核验/g) || []).length,
  2,
  "unknown is not presented as a confirmed zero",
);
assert.match(emptyMarkup, /完整队列没有反包/);
assert.match(emptyMarkup, /晋级状态仍未知/);
const stateOwned = {
  topic_id: "state-owned",
  name: "国企改革",
  kind: "concept",
};
const media = { topic_id: "media", name: "文化传媒", kind: "industry" };
const publishing = { topic_id: "publishing", name: "出版", kind: "industry" };
const ai = { topic_id: "ai", name: "AI应用", kind: "concept" };
const structural = { topic_id: "margin", name: "融资融券", kind: "concept" };
const attributionData = {
  items: [
    stock("601999", "出版传媒", "09:40:00", {
      source_industry: "出版",
      reason: "出版发行+AI应用+数智基金",
      details_source: "eastmoney-public",
      topics: [stateOwned, media, publishing, ai, structural],
    }),
    stock("600005", "来源行业文本样本", "10:00:00", {
      source_industry: "机械制造",
      topics: [
        stateOwned,
        { topic_id: "equipment", name: "专用设备", kind: "industry" },
      ],
    }),
    stock("600006", "出版名称不猜行业", "10:10:00", {
      topics: [ai],
      reason: "—",
    }),
  ],
  theme_counts: [{ ...stateOwned, limit_up_count: 2 }],
};
const attributionBefore = structuredClone(attributionData);
const attributionMarkup = renderToStaticMarkup(
  createElement(LimitLadder, { ...props, data: attributionData }),
);
const attributionCards = [
  ...attributionMarkup.matchAll(
    /<article class="ladder-stock-card">([\s\S]*?)<\/article>/g,
  ),
].map((match) => match[1]);
const publishingCard = attributionCards.find((card) =>
  card.includes("出版传媒"),
);
assert.ok(publishingCard);
assert.match(publishingCard, /ladder-attribution-label">行业/);
assert.ok(
  publishingCard.indexOf("<span>出版</span>") <
    publishingCard.indexOf("<span>文化传媒</span>"),
  "same-day source industry is presented before broader industry memberships",
);
assert.ok(
  publishingCard.indexOf("<span>出版</span>") <
    publishingCard.indexOf("<span>国企改革</span>"),
  "a preferred concept display group never takes the industry's place",
);
assert.match(publishingCard, /ladder-attribution-label">概念关联/);
assert.match(publishingCard, /其余关联 2/);
assert.match(publishingCard, /<span>AI应用<\/span>/);
assert.match(publishingCard, /<span>融资融券<\/span>/);
assert.match(publishingCard, /ladder-attribution-label">来源原因/);
assert.match(publishingCard, /出版发行\+AI应用\+数智基金/);
assert.match(publishingCard, /不代表已验证的涨停驱动/);
const textIndustryCard = attributionCards.find((card) =>
  card.includes("来源行业文本样本"),
);
assert.match(textIndustryCard, /ladder-source-industry">机械制造/);
assert.ok(
  textIndustryCard.indexOf("机械制造") <
    textIndustryCard.indexOf("<span>专用设备</span>"),
);
const unknownIndustryCard = attributionCards.find((card) =>
  card.includes("出版名称不猜行业"),
);
assert.match(
  unknownIndustryCard,
  /ladder-attribution-label">行业<\/span><div class="ladder-attribution-values"><span class="ladder-no-topic">待核验/,
);
assert.match(unknownIndustryCard, /ladder-source-reason">待核验/);
assert.deepEqual(
  attributionData,
  attributionBefore,
  "rendering industry, concepts and source reason preserves source facts",
);
const markedRow = (code, name, kind, eventDay = props.date) =>
  stock(code, name, "09:40:00", {
    date: props.date,
    snapshot_id: "synthetic-generation",
    pattern_date: props.date,
    pattern_as_of: props.date,
    pattern_status: { [kind]: { state: true, status: "confirmed" } },
    pattern_events: [
      {
        kind,
        date: eventDay,
        bar_date: eventDay,
        basis: "verified-source-close-pool-and-raw-ohlc-v1",
        snapshot_id: "synthetic-generation",
        raw_price_basis: "none",
        raw_volume: 100,
        raw_ohlc: { open: 10, high: 11, low: 9, close: 11 },
        source: "synthetic-source",
      },
    ],
  });
const shapes = [
  markedRow("600101", "地天合成样本", "earth_to_sky"),
  markedRow("600102", "一字合成样本", "one_price_limit_up"),
  markedRow("600103", "旧日不标样本", "earth_to_sky", "2026-10-20"),
];
const shapeMarkup = renderToStaticMarkup(
  createElement(LimitLadder, {
    ...props,
    data: {
      snapshot_id: "synthetic-generation",
      items: shapes,
      rebound_items: [shapes[0]],
    },
  }),
);
assert.equal(
  (shapeMarkup.match(/data-board-tag="type:earth_to_sky"/g) || []).length,
  2,
  "height cards and rebound cards share the same dated shape badge",
);
assert.equal(
  (shapeMarkup.match(/data-board-tag="type:one_price_limit_up"/g) || []).length,
  1,
);
const earthCard = shapeMarkup
  .match(/<article class="ladder-stock-card">[\s\S]*?<\/article>/g)
  .find((card) => card.includes("地天合成样本"));
assert.match(earthCard, /aria-label="复制代码">600101<\/button>/);
assert.match(
  earthCard,
  /class="ladder-stock-badges"><div class="board-tags"[\s\S]*?地天板<\/span>/,
);
assert.match(shapeMarkup, /一字涨停<\/span>/);
assert.match(shapeMarkup, /不还原首次触板或开板回封路径/);
assert.match(shapeMarkup, /synthetic-source/);
const nextDayMarkup = renderToStaticMarkup(
  createElement(LimitLadder, {
    ...props,
    date: "2026-10-22",
    data: { snapshot_id: "synthetic-generation", items: shapes },
  }),
);
assert.doesNotMatch(nextDayMarkup, /data-board-tag="type:/);
const otherGenerationMarkup = renderToStaticMarkup(
  createElement(LimitLadder, {
    ...props,
    data: { snapshot_id: "another-generation", items: shapes },
  }),
);
assert.doesNotMatch(otherGenerationMarkup, /data-board-tag="type:/);
const { setViewOverride, changes } = await import(viewReact);
const { received } = await import(mainlineProbe);
setViewOverride("mainlines");
const focusMarkup = renderToStaticMarkup(
  createElement(LimitLadder, { ...props, data: attributionData }),
);
assert.match(focusMarkup, /synthetic-mainline-probe/);
assert.doesNotMatch(
  focusMarkup,
  /class="ladder-supplemental-sections"/,
  "the focus view must not repeat all rebound, failed and 20cm cards",
);
assert.doesNotMatch(
  focusMarkup,
  /class="ladder-theme-shortcuts"/,
  "raw directory filters belong to the existing ladder/theme views",
);
assert.match(
  focusMarkup,
  /class="ladder-coverage-details"/,
  "shared date and source coverage stays accessible",
);
assert.match(focusMarkup, /class="ladder-evidence"/);
assert.equal(received.at(-1).date, props.date);
assert.equal(received.at(-1).loading, props.loading);
assert.equal(received.at(-1).error, props.error);
assert.equal(received.at(-1).onReload, props.onReload);
assert.equal(
  received.at(-1).rows,
  attributionData.items,
  "focus view consumes the same confirmed pool",
);
received.at(-1).onBrowseView("theme");
assert.deepEqual(
  changes,
  ["theme", "", null],
  "directory navigation reuses the existing view and clears stale mainline text/topic filters",
);
changes.length = 0;
received.at(-1).onBrowseView("height");
assert.deepEqual(changes, ["height", "", null]);
setViewOverride("theme");
const themeMarkup = renderToStaticMarkup(
  createElement(LimitLadder, { ...props, data: attributionData }),
);
assert.match(themeMarkup, /class="ladder-theme-board"/);
assert.match(themeMarkup, /class="ladder-supplemental-sections"/);
assert.match(themeMarkup, /class="ladder-theme-shortcuts"/);
setViewOverride(null);
console.log(
  "limit ladder: existing height/theme classifications and focus-only navigation, counts/uncertainty/sorting, dated board tags and source boundaries passed",
);
