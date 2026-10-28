import assert from "node:assert/strict";
import { build } from "vite";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const result = await build({
  configFile: false,
  root: new URL("../../../", import.meta.url).pathname,
  logLevel: "silent",
  build: {
    write: false,
    ssr: new URL("./NoonLadderTopics.tsx", import.meta.url).pathname,
    minify: false,
  },
});
const entry = result.output.find((item) => item.type === "chunk" && item.isEntry);
const code = entry.code.replace(/from "([^".][^"]*)"/g,
  (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`);
const { default: Topics } = await import("data:text/javascript;base64," + Buffer.from(code).toString("base64"));
const row = {topic_code:"881001.TI",name:"合成行业",kind:"industry",members_observed_date:"2026-10-21",count:2,max_board_count:5,
  items:[{instrument_id:"600001.SH",name:"合成五板股",board_count:5,change_pct:10},{instrument_id:"600002.SH",name:"合成首板股",board_count:1,change_pct:20}]};
const fixture = {industries:[row],topics:Array.from({length:10},(_,index)=>({...row,topic_code:`88500${index}.TI`,name:`合成概念${index}`,kind:"concept"})),
  unclassified_count:1,scope:"合成测试范围"};
const render = (value, props={}) => renderToStaticMarkup(createElement(Topics,{value,onStock(){},chartDate:"2026-10-27",...props}));
const before = JSON.stringify(fixture);
const savedFetch = globalThis.fetch;
globalThis.fetch=()=>{throw Error("topic groups must never fetch");};
try {
  assert.equal(render(null), "");
  assert.equal(render(undefined), "");
  const html = render(fixture);
  assert.ok(html.includes('aria-label="题材内涨停样本分布"'));
  assert.ok(html.includes("行业") && html.includes("概念") && html.includes("合成行业"));
  assert.ok(html.includes("2只") && html.includes("最高5板"));
  assert.ok(html.includes("成员名单日 2026-10-21") && html.includes("来源代码 881001.TI"));
  assert.ok(html.includes("数量不可相加") && html.includes("另有1只未取得可核验归属"));
  assert.ok(html.includes("合成概念6") && !html.includes("合成概念7") && !html.includes("合成概念9"), "wide panels display seven directions only");
  assert.ok(!html.includes("其余") && !html.includes("noon-ladder-topics-more"), "no remainder control is rendered");
  const compact=render(fixture,{limit:5});
  assert.ok(compact.includes("合成概念4") && !compact.includes("合成概念5"), "compact side-by-side cards display five directions");
  assert.match(html, /<button[^>]*title="查看截至2026-10-27的已发布K线"[^>]*>合成五板股<\/button>/);
  assert.ok(html.includes("5板") && html.includes("1板") && html.includes('class="up"'));
  assert.ok(render(fixture,{chartDate:undefined}).includes('disabled=""'), "missing chart history disables chart access");
  const down=render(fixture,{sampleLabel:"跌停"});
  assert.ok(down.includes('aria-label="题材内跌停样本分布"') && down.includes("当前跌停样本股票") && down.includes("本次已核验跌停样本"));
  assert.ok(!down.includes("涨停样本"), "reusing the component must not relabel a down-limit pool as up-limit");
  const changeFixture={industries:[{...row,valid_count:2,mean_change_pct:-1.87}],topics:[],sample_count:2};
  assert.ok(!render(changeFixture).includes("平均涨跌幅"), "up-limit groups do not repeat the mean change metric");
  assert.ok(render(changeFixture,{sampleLabel:"昨日炸板",showSampleChange:true}).includes("平均涨跌幅-1.87%"));
  assert.ok(render({industries:[{...row,valid_count:2,mean_change_pct:0}]},{sampleLabel:"跌停",showSampleChange:true}).includes("平均涨跌幅0.00%"));
  assert.ok(render({industries:[{...row,valid_count:0,mean_change_pct:null}]},{sampleLabel:"跌停",showSampleChange:true}).includes("平均涨跌幅—"));
  const boardFiltered=render(changeFixture,{sampleLabel:"昨日炸板",showSampleChange:true,boardFilter:"star"});
  assert.ok(boardFiltered.includes("该板别暂无展示样本") && boardFiltered.includes("平均涨跌幅-1.87%") && !boardFiltered.includes("合成五板股"));
  assert.ok(!boardFiltered.includes("红盘率"));
  const empty=render({industries:[],topics:[],unclassified_count:5});
  assert.ok(empty.includes("暂无已核验的行业/概念分组") && !empty.includes("noon-ladder-topic-chip"));
  const unknown=render({industries:[{...row,count:null}],topics:[]});
  assert.ok(!unknown.includes("合成行业"), "unknown counts never appear as zero-count chips");
  assert.equal(JSON.stringify(fixture),before);
} finally { globalThis.fetch=savedFetch; }
console.log("noon ladder topics: compact dated groups, bounded disclosure, full members, chart cutoff and unknown handling passed");
