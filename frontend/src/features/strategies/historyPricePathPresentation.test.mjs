import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

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
  for (const match of [
    ...source.matchAll(/(?:from |import )(["'])([^"']+)\1/g),
  ]) {
    const name = match[2];
    if (name.endsWith(".css")) {
      source = source.replace(match[0], "void " + JSON.stringify(name));
      continue;
    }
    let target;
    if (name.endsWith("TopicSelect"))
      target =
        "data:text/javascript,export default function TopicSelect(){return null}";
    else if (name.startsWith(".")) {
      const local = new URL(name + ".ts", path);
      try {
        await fs.access(local);
      } catch {
        local.pathname += "x";
      }
      target = await moduleUrl(local);
    } else target = import.meta.resolve(name);
    source = source.replace(
      match[0],
      match[0].replace(match[1] + name + match[1], JSON.stringify(target)),
    );
  }
  const url =
    "data:text/javascript;base64," + Buffer.from(source).toString("base64");
  modules.set(path.href, url);
  return url;
}
const editor = await import(
  await moduleUrl(new URL("./HistoryPricePathEditor.tsx", import.meta.url))
);
const tree = await import(
  await moduleUrl(new URL("./RuleTreeEditor.tsx", import.meta.url))
);
const evidence = await import(
  await moduleUrl(new URL("./RuleEvidence.tsx", import.meta.url))
);
const unchanged = () => {
  throw new Error("render must not edit or save a strategy");
};
const event = {
  window: { from: 1, to: 30 },
  match: "latest",
  where: { all: [{ field: "volume_ratio", op: ">=", value: 3, period: "D" }] },
  then: {
    any: [
      { field: "macd_cross", op: "==", value: true, period: "D" },
      { field: "kd_cross", op: "==", value: true, period: "D" },
    ],
  },
};
const before = JSON.stringify(event);
const oldHtml = renderToStaticMarkup(
  createElement(editor.default, { event, onChange: unchanged }),
);
assert.match(oldHtml, /期间收盘守位/);
assert.match(oldHtml, /事件价收复或触及/);
assert.doesNotMatch(oldHtml, /checked=""|type="number"/);
assert.equal(JSON.stringify(event), before);
assert.doesNotMatch(
  renderToStaticMarkup(
    createElement(editor.default, {
      event,
      onChange: unchanged,
      allowRebound: false,
    }),
  ),
  /守位后今日回升 ≥2%/,
);

const configured = {
  ...event,
  price_path: {
    guard: {
      max_drawdown_pct: 5,
      require_pullback: true,
      near_floor: { sessions: 1, band_pct: 1 },
    },
    reclaim: { event_field: "close", mode: "first_close_reclaim" },
  },
};
const configuredBefore = JSON.stringify(configured);
const html = renderToStaticMarkup(
  createElement(editor.default, { event: configured, onChange: unchanged }),
);
assert.match(html, /事件收盘最大回撤百分比/);
assert.match(html, /value="5"/);
assert.match(html, /10元、5% → 9\.50元/);
assert.match(html, /昨日/);
assert.match(html, /近N个交易日/);
assert.match(html, /价格首次/);
assert.match(html, /value="first_close_reclaim" selected/);
assert.match(html, /前2个交易日或更早/);
assert.match(html, /盘中最低跌破不会单独判失败/);
assert.equal(
  (
    html.match(
      /<option value="(?:close_at_or_above|close_reclaim|first_close_reclaim|high_at_or_above|touch)"/g,
    ) || []
  ).length,
  5,
);
assert.equal(JSON.stringify(configured), configuredBefore);
const empty = renderToStaticMarkup(
  createElement(editor.default, {
    event: {
      ...configured,
      price_path: {
        guard: {
          max_drawdown_pct: "",
          near_floor: { sessions: "", band_pct: "" },
        },
      },
    },
    onChange: unchanged,
  }),
);
assert.match(empty, /role="alert"/);
assert.match(empty, /允许回撤请输入/);
assert.match(empty, /value=""/);

for (const period of ["D", "W", "M"]) {
  const treeHtml = renderToStaticMarkup(
    createElement(tree.default, {
      value: { any: [{ history_event: configured }] },
      period,
      onChange: unchanged,
      fields: ["volume_ratio", "change_pct", "macd_cross", "kd_cross"].map(
        (field) => ({
          field,
          type: field.endsWith("cross") ? "boolean" : "number",
          operators: [">=", ">", "=="],
          historical_event_supported: true,
        }),
      ),
    }),
  );
  assert.match(treeHtml, /事件后的价格过程/);
  assert.match(treeHtml, /可选 · 日K/);
  assert.match(treeHtml, /价格过程（日K）/);
  assert.match(treeHtml, /MACD金叉/);
  assert.match(treeHtml, /KD金叉/);
  assert.match(treeHtml, /以下任一满足/);
}

const proof = {
  type: "history_price_path",
  result: false,
  guard: {
    result: false,
    holding_result: false,
    floor: 9.50418,
    event_close: 10.0044,
    max_drawdown_pct: 5,
    start: "2026-10-13",
    end: "2026-10-22",
    expected_sessions: 8,
    comparable_sessions: 8,
    min_close: 9.5,
    min_close_date: "2026-10-16",
    first_breach_date: "2026-10-16",
    first_breach_close: 9.5,
    pullback: { result: true, date: "2026-10-15", close: 9.55 },
    near_floor: {
      result: true,
      sessions: 1,
      band_pct: 1,
      date: "2026-10-21",
      close: 9.55,
      upper: 9.5992218,
    },
  },
  reclaim: {
    result: false,
    mode: "first_close_reclaim",
    event_field: "close",
    reference_price: 10.0044,
    previous_date: "2026-10-21",
    previous_close: 9.8,
    current_date: "2026-10-22",
    current_close: 10.1,
    current_high: 10.2,
    current_low: 9.9,
    first_prior_reclaim_date: "2026-10-17",
    reason: "此前已经发生价格收复",
  },
};
const proofHtml = renderToStaticMarkup(
  createElement(evidence.HistoryPricePathEvidence, { value: proof }),
);
assert.match(proofHtml, /title="9\.50418"/);
assert.match(proofHtml, /9\.50418/);
assert.match(proofHtml, /未舍入值判断/);
assert.match(proofHtml, /首次收盘失守/);
assert.match(proofHtml, /2026-10-16/);
assert.match(proofHtml, /此前已发生收复/);
assert.match(proofHtml, /2026-10-17/);
assert.match(proofHtml, /技术条件不满足不会重置首次/);
const decimalHtml = renderToStaticMarkup(
  createElement(evidence.HistoryPricePathEvidence, {
    value: {
      result: true,
      guard: {
        result: true,
        holding_result: true,
        floor: 9.50418,
        floor_decimal: "9.504180000000000000000000000000000001",
        near_floor: {
          result: true,
          sessions: 1,
          band_pct: 1,
          upper: 9.5992218,
          upper_decimal: "9.59922180000000000000000000000000000101",
        },
      },
    },
  }),
);
assert.match(decimalHtml, /title="9\.504180000000000000000000000000000001"/);
assert.match(decimalHtml, /title="9\.59922180000000000000000000000000000101"/);
assert.match(
  decimalHtml,
  />9\.599222<\/span>/,
  "display remains bounded while title retains exact decimal",
);
for (const [holding_result, expected] of [
  [true, "满足"],
  [null, "待核验"],
]) {
  const separatedHtml = renderToStaticMarkup(
    createElement(evidence.HistoryPricePathEvidence, {
      value: {
        result: false,
        guard: {
          result: false,
          holding_result,
          pullback: { result: false, reason: "事件后尚未出现实际回落" },
        },
      },
    }),
  );
  const holdingHeading = separatedHtml.match(/<h5>([\s\S]*?)<\/h5>/)?.[1] || "";
  assert.match(holdingHeading, /期间收盘守位/);
  assert(
    holdingHeading.includes(`>${expected}</span>`),
    `holding must remain ${expected} independently of overall guard failure`,
  );
  assert.match(separatedHtml, /事件后至昨日实际回落：未满足/);
  if (holding_result === null)
    assert.doesNotMatch(holdingHeading, />未满足<\/span>/);
}
const legacyHoldingHtml = renderToStaticMarkup(
  createElement(evidence.HistoryPricePathEvidence, {
    value: { result: true, guard: { result: true } },
  }),
);
assert.match(
  legacyHoldingHtml.match(/<h5>([\s\S]*?)<\/h5>/)?.[1] || "",
  />满足<\/span>/,
);
const unknownHtml = renderToStaticMarkup(
  createElement(evidence.HistoryPricePathEvidence, {
    value: {
      result: null,
      guard: {
        result: null,
        holding_result: null,
        floor: null,
        blocking_date: "2026-10-15",
        reason: "公司行动待核验",
      },
      reclaim: {
        result: null,
        mode: "touch",
        event_field: "high",
        reason: "kdj_j 缺失",
      },
    },
  }),
);
assert.match(unknownHtml, /待核验/);
assert.match(unknownHtml, /公司行动待核验/);
assert.match(unknownHtml, /2026-10-15/);
assert.match(unknownHtml, /整日跳空/);
assert.doesNotMatch(unknownHtml, /kdj_j|>0\.00</);
const fullHtml = renderToStaticMarkup(
  createElement(evidence.default, {
    value: [
      {
        type: "history_event",
        result: false,
        window: event.window,
        selected_event: {
          date: "2026-10-12",
          offset: 8,
          close: 10.0044,
          high: 10.1,
          low: 9.1,
        },
        price_path_checks: proof,
        checks: [
          { field: "change_pct", op: ">=", value: 2, actual: 3, result: true },
        ],
        evidence: { as_of: "2026-10-22" },
      },
    ],
  }),
);
assert.match(fullHtml, /查看事件依据/);
assert.match(fullHtml, /价格过程未满足/);
assert.match(fullHtml, /当前条件/);
assert.match(fullHtml, /周期涨跌幅/);
assert.match(fullHtml, /整体条件未满足/);
assert.doesNotMatch(fullHtml, /当前条件未满足/);
assert.match(unknownHtml, /已核验可比部分最低收盘/);
console.log(
  "history price path presentation: disabled legacy editors, five modes, daily process in D/W/M, exact-price evidence, unknowns and shared result expansion passed",
);
