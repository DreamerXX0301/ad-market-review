import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./instrumentSearchLayout.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { instrumentSearchLayout: layout } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const viewport = { width: 1000, height: 800 };
const normal = layout(
  { left: 220, top: 130, bottom: 167, width: 500 },
  viewport,
);
assert.deepEqual(normal, { left: 220, width: 500, maxHeight: 420, top: 173 });
assert.equal(
  normal.top > 167,
  true,
  "list uses viewport coordinates beyond the panel's bottom",
);
const above = layout(
  { left: 720, top: 740, bottom: 777, width: 300 },
  viewport,
);
assert.deepEqual(above, { left: 692, width: 300, maxHeight: 420, bottom: 66 });
assert.equal(
  "top" in above,
  false,
  "short lists remain attached by their bottom edge when flipped",
);
const mobile = layout(
  { left: 20, top: 150, bottom: 187, width: 230 },
  { width: 260, height: 400 },
);
assert.equal(mobile.width, 244);
assert.equal(mobile.left, 8);
assert.equal(mobile.maxHeight, 199);
assert.equal(mobile.top, 193);
for (const anchor of [
  { left: 0, top: 810, bottom: 840, width: 200 },
  { left: 0, top: -45, bottom: -8, width: 200 },
  { left: NaN, top: 30, bottom: 67, width: 200 },
  { left: 30, top: 30, bottom: 67, width: 0 },
])
  assert.equal(layout(anchor, viewport), null);
assert.equal(
  layout({ left: 0, top: 0, bottom: 10, width: 200 }, { width: 8, height: 8 }),
  null,
);
console.log(
  "instrument search: portalled viewport placement, flip, edge clamp and invalid/offscreen anchors passed",
);
