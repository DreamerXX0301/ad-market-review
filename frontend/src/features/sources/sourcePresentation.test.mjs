import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./sourcePresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { capabilityState, capabilityCounts, sourceCapabilities } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
for (const status of ["available", "ready", "verified", "success", "allowed"])
  assert.equal(capabilityState({ status }), "verified");
for (const status of ["locked", "denied", "permission_denied"])
  assert.equal(capabilityState({ status, available: true }), "denied");
assert.equal(
  capabilityState({ status: "unsupported", available: true }),
  "unsupported",
);
for (const status of [
  "unknown",
  "unverified",
  "temporary_error",
  "future-status",
])
  assert.equal(capabilityState({ status, available: true }), "unknown");
assert.equal(capabilityState({ available: true }), "verified");
assert.equal(capabilityState({ account_access: "denied" }), "denied");
assert.equal(capabilityState({}), "unknown");
const capabilities = sourceCapabilities({
  capabilities: {
    daily: { status: "available" },
    index: false,
    financial: { status: "locked" },
    minute: { status: "unsupported" },
  },
});
assert.deepEqual(capabilityCounts(capabilities), {
  verified: 1,
  denied: 1,
  unknown: 1,
  unsupported: 1,
});
assert.equal(
  sourceCapabilities({
    capabilities: [{ key: "daily", status: "unverified" }],
  })[0].status,
  "unverified",
);
assert.deepEqual(sourceCapabilities({}), []);
console.log(
  "source capability presentation: verified/denied/unknown/unsupported remain distinct",
);
