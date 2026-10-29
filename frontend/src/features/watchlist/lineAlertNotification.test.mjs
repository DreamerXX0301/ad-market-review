import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
const encoded = (value) =>
  "data:text/javascript;base64," + Buffer.from(value).toString("base64");
const helperSource = await fs.readFile(
  new URL("./lineAlertNotification.ts", import.meta.url),
  "utf8",
);
const helperUrl = encoded(
  ts.transpileModule(helperSource, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
    },
  }).outputText,
);
const { currentLineAlertSummary } = await import(helperUrl);
assert.equal(currentLineAlertSummary(null, "2026-10-28"), null);
assert.equal(currentLineAlertSummary({ business_date: null, total: 0 }), null);
assert.equal(
  currentLineAlertSummary(
    { business_date: "2026-10-27", total: 5 },
    "2026-10-28",
  ),
  null,
);
assert.equal(
  currentLineAlertSummary({ business_date: "2026-10-28", total: -1 }),
  null,
);
assert.equal(
  currentLineAlertSummary({ business_date: "2026-10-28", total: NaN }),
  null,
);
assert.equal(
  currentLineAlertSummary(
    { business_date: "2026-10-28", total: 0 },
    "2026-10-28",
  )?.total,
  0,
);
assert.equal(
  currentLineAlertSummary(
    { business_date: "2026-10-28", total: 3 },
    "2026-10-27",
  )?.total,
  3,
);
const component = await fs.readFile(
  new URL("./LineAlertNotice.tsx", import.meta.url),
  "utf8",
);
let source = ts
  .transpileModule(component, {
    fileName: "notice.tsx",
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  .outputText.replace(/import "[^"]+\.css";/g, "");
for (const [name, url] of Object.entries({
  react: import.meta.resolve("react"),
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
  "lucide-react": import.meta.resolve("lucide-react"),
  "../../lib/api": encoded(
    "export const useApi=()=>({data:{business_date:'2026-10-28',total:123,items:[]},reload:()=>{throw Error('render cannot fetch')},loading:false,error:''});export const list=(v)=>v?.items||[];",
  ),
  "../../lib/workbenchEvents": encoded(
    "export const LINE_ALERT_CHECK_FINISHED='finished';",
  ),
  "./lineAlertNotification": helperUrl,
  "./lineAlertPresentation": encoded(
    "export const lineAlertView=()=>({tone:'triggered',title:'回踩收回'});",
  ),
}))
  source = source.replaceAll(
    `from ${JSON.stringify(name)}`,
    `from ${JSON.stringify(url)}`,
  );
const { default: Notice } = await import(encoded(source));
const html = renderToStaticMarkup(
  createElement(Notice, {
    businessDate: "2026-10-28",
    onNavigate: () => {
      throw Error("render cannot navigate");
    },
  }),
);
assert.match(html, /今日划线提醒 123 条/);
assert.match(html, /99\+/);
assert.match(html, /aria-live="polite"/);
assert.match(html, /aria-expanded="false"/);
assert.doesNotMatch(
  component,
  /AudioContext|playLineAlertSound|Volume2|VolumeX|setInterval|Notification\./,
);
assert.doesNotMatch(component, /开启声音|关闭声音|试听|localStorage/);
assert.match(component, /LINE_ALERT_CHECK_FINISHED/);
assert.match(component, /watchlist\?alerts=today/);
console.log(
  "Line notice: visible bounded today badge, direct list navigation, no sound/storage/background polling passed",
);
