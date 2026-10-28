import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./noonReadGuidePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { NOON_READING_SECTIONS, NOON_READING_TABS, noonReadingSection } =
  await import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );

assert.equal(
  new Set(NOON_READING_SECTIONS.map((section) => section.id)).size,
  6,
);
assert.deepEqual(
  NOON_READING_SECTIONS.filter((section) => section.step).map(
    ({ step, tab }) => [step, tab],
  ),
  ["01", "02", "03", "04", "05"].map((step) => [step, "overview"]),
);
assert.equal(NOON_READING_TABS["noon-review-history"], "previous");
assert.equal(NOON_READING_TABS["noon-data-gaps"], undefined);
assert.equal(noonReadingSection("noon-data-gaps"), null);
for (const section of NOON_READING_SECTIONS) {
  assert.equal(noonReadingSection(section.id).tab, section.tab);
  assert.ok(section.question.endsWith("？"));
}
for (const missing of [
  null,
  undefined,
  {},
  "unknown",
  "noon-overview-start?date=2026-10-28",
]) {
  assert.equal(
    noonReadingSection(missing),
    null,
    "invalid targets cannot silently select a different tab",
  );
}

const { build } = await import("vite");
const { createRequire } = await import("node:module");
const { pathToFileURL } = await import("node:url");
const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const require = createRequire(import.meta.url);
const result = await build({
  configFile: false,
  root: new URL("../../../", import.meta.url).pathname,
  logLevel: "silent",
  build: {
    write: false,
    ssr: new URL("./NoonReadGuide.tsx", import.meta.url).pathname,
    minify: false,
  },
});
const output = result.output.find(
  (item) => item.type === "chunk" && item.isEntry,
);
const code = output.code.replace(
  /from "([^".][^"]*)"/g,
  (_, id) => `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`,
);
const { default: Guide } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const savedFetch = globalThis.fetch;
let calls = 0;
globalThis.fetch = async () => {
  calls += 1;
  throw new Error("reading guide must not fetch");
};
try {
  for (const target of ["noon-prior-feedback", "noon-review-history"]) {
    const html = renderToStaticMarkup(
      createElement(Guide, { activeSection: target, onNavigate() {} }),
    );
    assert.ok(html.includes('aria-label="午评阅读导航"'));
    assert.equal(
      (html.match(/aria-current="location"/g) || []).length,
      1,
      "exactly one location is current across overview and reference tabs",
    );
    assert.ok(
      html.includes(`aria-controls="${target}" aria-current="location"`),
    );
    assert.ok(html.includes(noonReadingSection(target).question));
    assert.ok(html.includes("不会自动采集行情"));
    assert.equal(
      (html.match(/<button /g) || []).length,
      6,
      "all destinations remain keyboard-reachable actual buttons",
    );
    assert.ok(!html.includes('aria-controls="noon-data-gaps"'));
    assert.ok(!html.includes("来源与缺口"));
  }
  assert.equal(calls, 0);
} finally {
  globalThis.fetch = savedFetch;
}
console.log(
  "noon reading guide: workflow order, tab targets, current location and read-only SSR passed",
);
