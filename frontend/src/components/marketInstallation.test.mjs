import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("./marketInstallation.ts", import.meta.url),
  "utf8",
);
const code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { marketInstallationState: state } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const empty = { data_date: null, market_dates: [] };
assert.equal(
  state(empty, false, ""),
  "empty",
  "successful fresh Docker bootstrap is a terminal empty state",
);
assert.equal(state({ ...empty, data_date: "" }, false, ""), "empty");
assert.equal(state(null, true, ""), "loading");
assert.equal(
  state(empty, true, ""),
  "loading",
  "refreshing a cached empty response does not hide loading",
);
assert.equal(
  state(empty, false, "API unreachable"),
  "error",
  "network failures must not look like a fresh empty installation",
);
assert.equal(state(null, false, "API unreachable"), "error");
assert.equal(state(null, false, ""), "unavailable");
assert.equal(
  state({}, false, ""),
  "unavailable",
  "malformed success is not confirmed no-data",
);
assert.equal(
  state({ data_date: null, market_dates: ["invalid"] }, false, ""),
  "unavailable",
);
assert.equal(
  state({ data_date: "invalid", market_dates: [] }, false, ""),
  "unavailable",
);
assert.equal(
  state(
    { data_date: "2026-10-20", market_dates: ["2026-10-19", "2026-10-20"] },
    false,
    "",
  ),
  "ready",
);
assert.equal(
  state({ data_date: null, market_dates: ["2026-10-20"] }, false, ""),
  "ready",
  "retained dates are not an empty installation",
);
console.log(
  "fresh install: successful empty, loading, failed, malformed and published bootstrap states passed",
);
