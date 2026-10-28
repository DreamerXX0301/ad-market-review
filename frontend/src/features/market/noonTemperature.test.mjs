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
    ssr: new URL("./NoonTemperature.tsx", import.meta.url).pathname,
    minify: false,
  },
});
const entry = result.output.find(
  (item) => item.type === "chunk" && item.isEntry,
);
const code = entry.code.replace(
  /from "([^".][^"]*)"/g,
  (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`,
);
const { default: Temperature } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const fixture = {
  version: "noon-temperature-v1",
  state: "available",
  score: 0,
  label: "偏冷",
  components: [
    {
      key: "breadth",
      label: "涨跌广度",
      weight: 25,
      value: 0,
      evidence: "合成测试证据",
    },
  ],
  coverage: {
    available_components: 5,
    total_components: 5,
    weight_covered: 100,
  },
};
const render = (temperature) =>
  renderToStaticMarkup(createElement(Temperature, { temperature }));
const savedFetch = globalThis.fetch;
globalThis.fetch = () => {
  throw Error("temperature display must never fetch");
};
try {
  const zero = render(fixture);
  assert.ok(
    zero.includes('aria-valuenow="0"'),
    "a real zero score cannot disappear as missing data",
  );
  assert.ok(zero.includes("0.0"));
  assert.ok(zero.includes("noon-temperature-cool"));
  assert.ok(zero.includes("权重25%") && zero.includes("合成测试证据"));
  assert.ok(zero.includes("不是官方指标、收盘确认或策略信号"));
  const hot = render({ ...fixture, score: 100, label: "偏热" });
  assert.ok(
    hot.includes('aria-valuenow="100"') &&
      hot.includes("noon-temperature-warm"),
  );
  for (const bad of [
    undefined,
    null,
    { ...fixture, version: "unknown" },
    { ...fixture, score: NaN },
    { ...fixture, score: null },
    { ...fixture, state: "unavailable", score: 50 },
  ]) {
    const html = render(bad);
    assert.ok(html.includes("暂不评分"));
    assert.ok(
      !html.includes('role="meter"'),
      "unknown scores must not draw a false meter position",
    );
    assert.ok(!html.includes("aria-valuenow="));
  }
  const partial = render({
    ...fixture,
    state: "unavailable",
    score: null,
    reason: "缺少昨日样本",
    components: [
      {
        key: "prior",
        label: "昨日强势样本",
        value: null,
        weight: 20,
        reason: "昨日未采集",
      },
    ],
  });
  assert.ok(partial.includes("缺少昨日样本") && partial.includes("昨日未采集"));
  assert.ok(partial.includes("缺失不补零，不重新分配权重"));
} finally {
  globalThis.fetch = savedFetch;
}
console.log(
  "noon temperature display: true zero, bounded scores, missing inputs, rule provenance and no I/O passed",
);
