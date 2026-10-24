import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const runtime = import.meta.resolve("react/jsx-runtime");
const reactProbe =
  encode(`import * as React from ${JSON.stringify(import.meta.resolve("react"))};
export * from ${JSON.stringify(import.meta.resolve("react"))};
let scope='historical';export const changes=[];
export function setScope(value){scope=value;changes.length=0;}
export function useState(initial){const [value]=React.useState(initial==='historical'?scope:initial);return [value,(next)=>changes.push(next)];}`);
const apiProbe = encode(`let response=null;export const requests=[];
export function setResponse(value){response=value;requests.length=0;}
export function useApi(path){requests.push(path);return {data:response,loading:false,error:'',reload(){throw Error('must not reload automatically');}};}
export const list=(value)=>Array.isArray(value?.items)?value.items:[];`);
const jsxProbe =
  encode(`import {jsx as originalJsx,jsxs as originalJsxs} from ${JSON.stringify(runtime)};
export {Fragment} from ${JSON.stringify(runtime)};export const selectors=[];
function observe(type,props){if(type==='select'&&props['aria-label']==='选择成员口径')selectors.push(props);}
export function jsx(type,props,key){observe(type,props);return originalJsx(type,props,key);}
export function jsxs(type,props,key){observe(type,props);return originalJsxs(type,props,key);}`);
const tableProbe =
  encode(`import {jsx} from ${JSON.stringify(runtime)};export const received=[];
export function SortableMarketTable(props){received.push(props);return jsx('div',{children:props.rows.length?props.rows.map(row=>row.name).join(','):props.emptyTitle});}`);
const replacements = {
  react: reactProbe,
  "react/jsx-runtime": jsxProbe,
  "../../lib/api": apiProbe,
  "../../components/UI": encode(`import {jsx} from ${JSON.stringify(runtime)};
export const Badge=({children})=>jsx('span',{children});
export const Warning=({children})=>jsx('p',{children});
export const Modal=({children})=>jsx('main',{children});
export const ErrorBox=({message})=>jsx('p',{children:message});
export const Loading=()=>jsx('p',{children:'读取中'});
export const Warnings=()=>null;`),
  "./MarketDataCoverage": encode(
    "export default function Coverage(){return null;}",
  ),
  "../watchlist/WatchlistButton": encode(
    "export default function Watch(){return null;}",
  ),
  "./MarketTable": tableProbe,
  "./MarketMemberMetrics": encode(
    "export const MemberBoardCell=()=>null,MemberReturnCell=()=>null,MemberYearLimitCell=()=>null,MemberRecentLimitCell=()=>null;",
  ),
};
const modules = new Map();
async function moduleUrl(path) {
  if (modules.has(path.href)) return modules.get(path.href);
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
  const url = encode(source.replace(/^import ["'][^"']+\.css["'];?$/gm, ""));
  modules.set(path.href, url);
  return url;
}
const { latestMemberTopicCode, topicMemberScopeRequest, memberQuoteDate } =
  await import(
    await moduleUrl(new URL("./topicMemberScope.ts", import.meta.url))
  );
const topic = {
  topic_id: "fuyao-886040.TI",
  topic_code: "886040.TI",
  name: "合成成员概念",
  members_available: false,
  membership_match_type: "unavailable",
};
assert.equal(latestMemberTopicCode(topic), "886040.TI");
assert.equal(
  latestMemberTopicCode({ topic_id: "fuyao-886040.ti" }),
  "886040.TI",
);
assert.equal(
  latestMemberTopicCode({ topic_code: "BK1000", name: topic.name }),
  null,
);
assert.equal(latestMemberTopicCode({ name: topic.name }), null);
assert.equal(
  latestMemberTopicCode({
    topic_code: "BK1000",
    membership_topic_id: "fuyao-881001.TI",
  }),
  "881001.TI",
);
assert.equal(
  topicMemberScopeRequest(topic, "2026-10-20", "historical"),
  "/market/topic-members?date=2026-10-20&topic_id=fuyao-886040.TI",
);
assert.equal(
  topicMemberScopeRequest(topic, "2026-10-20", "latest"),
  "/sectors/886040.TI/members?membership_scope=latest",
);
assert.equal(
  topicMemberScopeRequest({ name: topic.name }, "2026-10-20", "latest"),
  null,
);
for (const value of [
  null,
  {},
  { quote_as_of: "2026-10-23" },
  { membership_scope: "latest", quote_as_of: "2026-02-30" },
])
  assert.equal(memberQuoteDate(value, "2026-10-20", "latest"), null);

const { default: Dialog } = await import(
  await moduleUrl(new URL("./TopicMembersDialog.tsx", import.meta.url))
);
const { setScope, changes } = await import(reactProbe);
const { setResponse, requests } = await import(apiProbe);
const { selectors } = await import(jsxProbe);
const { received } = await import(tableProbe);
const selected = [];
const props = {
  topic,
  date: "2026-10-20",
  onClose() {},
  onStock: (...args) => selected.push(args),
};
setResponse({
  date: props.date,
  items: [],
  total: 0,
  unavailable_reason: "该盘面日没有此分类的成员快照，不能用当前成员替代",
  members_complete: false,
});
const missing = renderToStaticMarkup(createElement(Dialog, props));
assert.deepEqual(requests, [
  "/market/topic-members?date=2026-10-20&topic_id=fuyao-886040.TI",
]);
assert.match(missing, /已知成员<\/small><strong>未知<\/strong>/);
assert.doesNotMatch(missing, /已知成员<\/small><strong>0 只/);
assert.match(missing, /当前最新成员/);
assert.match(missing, /该盘面日没有此分类的成员快照/);
assert.equal(
  selectors.at(-1).value,
  "historical",
  "missing history must not auto-switch to current constituents",
);
selectors.at(-1).onChange({ target: { value: "latest" } });
assert.deepEqual(changes, ["latest", "", "all"]);

setScope("latest");
setResponse({
  membership_scope: "latest",
  membership_as_of: "2026-10-21",
  quote_as_of: "2026-10-23",
  total: 18,
  members_complete: true,
  items: Array.from({ length: 18 }, (_, i) => ({
    instrument_id: `6000${String(i).padStart(2, "0")}.SH`,
    name: `合成成员${i}`,
  })),
});
const current = renderToStaticMarkup(createElement(Dialog, props));
assert.deepEqual(requests, [
  "/sectors/886040.TI/members?membership_scope=latest",
]);
assert.match(current, /成员采集日<\/small><strong>2026-10-21/);
assert.match(current, /行情日<\/small><strong>2026-10-23/);
assert.match(
  current,
  /不代表<!-- --> <!-- -->2026-10-20<!-- --> 的历史归属|不代表[\s\S]*2026-10-20[\s\S]*的历史归属/,
);
assert.match(current, /18 只/);
assert.match(current, /点击股票查看 (?:<!-- -->)?2026-10-23/);
received.at(-1).onStockNavigate("600000.SH", received.at(-1).rows);
assert.equal(
  selected.at(-1)[1].asOf,
  "2026-10-23",
  "current members must open current quote-date charts, not the old view date",
);
assert.equal(selected.at(-1)[1].items.length, 18);
const { stockSelectionForOpen } = await import(
  await moduleUrl(
    new URL("../../components/stockNavigation.ts", import.meta.url),
  )
);
assert.equal(
  stockSelectionForOpen(selected.at(-1)[0], selected.at(-1)[1], props.date)
    .asOf,
  "2026-10-23",
  "the shared stock opener must retain the explicitly selected current quote date",
);

setResponse({
  membership_scope: "latest",
  membership_as_of: "2026-10-24",
  quote_as_of: "2026-10-23",
  total: 1,
  members_complete: true,
  items: [{ instrument_id: "600000.SH", name: "非交易日采集成员" }],
});
const weekend = renderToStaticMarkup(createElement(Dialog, props));
assert.match(weekend, /成员采集日<\/small><strong>2026-10-24/);
assert.match(weekend, /行情日<\/small><strong>2026-10-23/);
received.at(-1).onStockNavigate("600000.SH", received.at(-1).rows);
assert.equal(
  selected.at(-1)[1].asOf,
  "2026-10-23",
  "a later collection date cannot become the stock quote date",
);

renderToStaticMarkup(
  createElement(Dialog, {
    ...props,
    topic: {
      topic_code: "BK1000",
      name: "合成跨源行业",
      membership_topic_id: "fuyao-881001.TI",
      membership_match_type: "cross_provider_name_proxy",
    },
  }),
);
assert.equal(
  requests.at(-1),
  "/sectors/881001.TI/members?membership_scope=latest",
);
assert.match(
  renderToStaticMarkup(
    createElement(Dialog, {
      ...props,
      topic: {
        topic_code: "BK1000",
        name: "合成跨源行业",
        membership_topic_id: "fuyao-881001.TI",
        membership_match_type: "cross_provider_name_proxy",
      },
    }),
  ),
  /不能视为东财资金分类的原始成分股/,
);
setResponse({
  membership_scope: "latest",
  total: 1,
  items: [{ instrument_id: "600000.SH", name: "合成日期缺失成员" }],
});
renderToStaticMarkup(createElement(Dialog, props));
assert.equal(received.at(-1).onStock, undefined);
assert.equal(
  received.at(-1).onStockNavigate,
  undefined,
  "unknown current quote dates cannot open the historical view date by accident",
);

setScope("historical");
setResponse({
  date: props.date,
  items: [],
  unavailable_reason: "历史成员未知",
});
renderToStaticMarkup(
  createElement(Dialog, {
    ...props,
    topic: { topic_code: "BK1000", name: "合成跨源行业" },
  }),
);
assert.match(
  renderToStaticMarkup(
    createElement(Dialog, {
      ...props,
      topic: { topic_code: "BK1000", name: "合成跨源行业" },
    }),
  ),
  /value="latest" disabled=""/,
);
assert.ok(
  requests.every((path) => path?.startsWith("/market/topic-members?")),
  "no unsupported code or source name can trigger a latest lookup",
);
const market = await fs.readFile(
  new URL("./Market.tsx", import.meta.url),
  "utf8",
);
assert.match(market, /disabled=\{!topicMembersRequest\(row, date\)\}/);
console.log(
  "Topic members: history stays unknown and selectable; explicit latest selection keeps source and quote dates separate, guards provider identity and opens the right stock date",
);
