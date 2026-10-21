import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

async function moduleUrl(path) {
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
    const target = name.endsWith("/UI")
      ? "data:text/javascript,export function Badge({children}){return children}"
      : name.startsWith(".")
        ? await moduleUrl(new URL(name + ".ts", path))
        : import.meta.resolve(name);
    source = source.replace(match[0], "from " + JSON.stringify(target));
  }
  return (
    "data:text/javascript;base64," + Buffer.from(source).toString("base64")
  );
}
const { YearLimitCountCell, RecentLimitCell } = await import(
  await moduleUrl(new URL("./MemberHistoryCells.tsx", import.meta.url))
);
const render = (component, row) =>
  renderToStaticMarkup(createElement(component, { row }));
const unknownZero = {
  limit_up_ytd: null,
  limit_up_ytd_observed: 0,
  limit_up_ytd_coverage: { covered_days: 1, expected_days: 191 },
};
const before = JSON.stringify(unknownZero);
const unknown = render(YearLimitCountCell, unknownZero);
assert.match(unknown, /待核验/);
assert.match(unknown, /暂无已确认记录/);
assert.doesNotMatch(unknown, /已确认≥0次/);
assert.match(
  render(YearLimitCountCell, { ...unknownZero, limit_up_ytd: 0 }),
  /0 次/,
);
assert.match(
  render(YearLimitCountCell, { ...unknownZero, limit_up_ytd_observed: 3 }),
  /已确认≥3次/,
);
assert.equal(JSON.stringify(unknownZero), before);
const latest = render(RecentLimitCell, {
  last_limit_up_date: "2026-09-07",
  last_limit_up_complete: false,
  sessions_since_limit_up: 30,
});
assert.match(latest, /2026-09-07/);
assert.match(latest, /最近已确认/);
assert.match(latest, /30 个交易日/);
assert.match(
  render(RecentLimitCell, { last_limit_up_date: null }),
  /未有可确认日期/,
);
console.log(
  "member history cells: incomplete zero stays unknown, exact zero and positive bounds remain distinct, dated latest proof retains partial label",
);
