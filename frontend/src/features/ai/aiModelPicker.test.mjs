import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const encode = (code) =>
  "data:text/javascript;base64," + Buffer.from(code).toString("base64");
const compile = async (file) =>
  ts.transpileModule(
    await fs.readFile(new URL(file, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    },
  ).outputText;
const stateUrl = encode(await compile("./aiState.ts"));
const { providerModelChoices, selectableModel, canGenerate } = await import(
  stateUrl
);
const expected = [
  "gpt-6.1-sol",
  "gpt-6-astra",
  "gpt-6.1-terra",
  "gpt-6-sol",
  "gpt-6-luna",
  "gpt-5.6-sol",
  "gpt-5.6-terra",
  "gpt-5.6-luna",
];
const provider = {
  provider_id: "openai",
  model: "gpt-6.1-sol",
  configured: true,
  enabled: true,
  config_revision: 5,
  verification: { state: "passed" },
  models: [{ id: "gpt-4o" }, { id: "arbitrary-directory-model" }],
  discovered_models: ["text-embedding-fixture", "gpt-99-future"],
};
const before = structuredClone(provider);
assert.deepEqual(
  providerModelChoices(provider).map((item) => item.id),
  expected,
);
for (const model of expected)
  assert.equal(selectableModel("openai", model), true);
for (const model of [
  "",
  "gpt-4o",
  "gpt-6.1-sol-2026-10-01",
  "arbitrary-directory-model",
])
  assert.equal(selectableModel("openai", model), false);
assert.deepEqual(provider, before);
assert.equal(canGenerate(provider), true);
assert.equal(canGenerate({ ...provider, model: "gpt-4o" }), false);

let pickerCode = await compile("./AIModelPicker.tsx");
for (const [name, url] of Object.entries({
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
  "./aiState": stateUrl,
}))
  pickerCode = pickerCode.replaceAll(
    'from "' + name + '"',
    "from " + JSON.stringify(url),
  );
const { default: AIModelPicker } = await import(encode(pickerCode));
const render = (value, model = provider) =>
  renderToStaticMarkup(
    createElement(AIModelPicker, { provider: model, value, onChange() {} }),
  );
const optionValues = (html) =>
  [...html.matchAll(/<option[^>]*value="([^"]*)"/g)].map((match) => match[1]);
const html = render("gpt-6.1-sol");
assert.deepEqual(optionValues(html), expected);
assert.equal((html.match(/<select/g) || []).length, 1);
assert.equal((html.match(/<input/g) || []).length, 0);
assert.ok(!html.includes("填写其他模型") && !html.includes("实际模型 ID"));
assert.ok(
  !html.includes("arbitrary-directory-model") &&
    !html.includes("text-embedding-fixture"),
);

const historical = render("gpt-4o");
assert.deepEqual(optionValues(historical).filter(Boolean), expected);
assert.ok(
  historical.includes('value=""') &&
    historical.includes("当前保存的模型 gpt-4o"),
);
assert.ok(!historical.includes('value="gpt-4o"'));
assert.ok(render('<img src=x onerror="alert(1)">').includes("&lt;img"));
assert.ok(!render('<img src=x onerror="alert(1)">').includes("<img"));

const deepseek = {
  ...provider,
  provider_id: "deepseek",
  model: "deepseek-flash",
  models: [],
  discovered_models: ["deepseek-other-fixture"],
};
assert.equal(selectableModel("deepseek", "deepseek-other-fixture"), true);
const deepseekHtml = render("deepseek-other-fixture", deepseek);
assert.ok(
  deepseekHtml.includes('value="custom"') &&
    deepseekHtml.includes("实际模型 ID"),
);
assert.ok(deepseekHtml.includes('value="deepseek-other-fixture"'));
console.log(
  "AI model picker: 8 curated GPT choices, safe legacy display, DeepSeek compatibility passed",
);
