import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const compiled = ts.transpileModule(
  await fs.readFile(new URL("./chartAnnotations.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  annotationScope,
  lineMatchesScope,
  lineLabel,
  savedLineSettings,
  newLineSettings,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const raw = {
  price_basis: "none",
  anchor_date: null,
  price: 12.5,
  label: "支撑位",
};
assert.deepEqual(annotationScope({ price_basis: "none" }), {
  price_basis: "none",
  anchor_date: null,
});
assert.equal(annotationScope(null), null);
assert.equal(annotationScope({ price_basis: "forward_event_affine" }), null);
for (const period of ["D", "W", "M"]) {
  assert.equal(
    lineMatchesScope(raw, annotationScope({ price_basis: "none", period })),
    true,
  );
  assert.equal(
    lineMatchesScope(
      {
        ...raw,
        price_basis: "forward_event_affine",
        anchor_date: "2026-10-20",
      },
      annotationScope({
        price_basis: "forward_event_affine",
        as_of: "2026-10-21",
        period,
      }),
    ),
    false,
  );
}
const scope = annotationScope({
  price_basis: "forward_event_affine",
  as_of: "2026-10-21",
  adjustment: { anchor: "2026-10-20" },
});
assert.equal(
  lineMatchesScope(
    { ...raw, price_basis: "forward_event_affine", anchor_date: "2026-10-20" },
    scope,
  ),
  true,
);
assert.equal(lineMatchesScope(raw, scope), false);
assert.match(lineLabel(raw), /支撑位.*12.5/);
assert.deepEqual(
  savedLineSettings(raw),
  { line_type: "reference", alert_modes: [], auto_watchlist: false },
  "a legacy label containing 支撑 never enables alerts",
);
assert.deepEqual(newLineSettings(), {
  line_type: "support",
  alert_modes: ["support_reclaim"],
  auto_watchlist: true,
});
assert.deepEqual(
  savedLineSettings({
    ...raw,
    line_type: "support",
    alert_modes: ["support_breakdown", "pressure_touch", "support_breakdown"],
    auto_watchlist: true,
  }),
  {
    line_type: "support",
    alert_modes: ["support_breakdown"],
    auto_watchlist: true,
  },
);
assert.deepEqual(
  savedLineSettings({
    ...raw,
    line_type: "reference",
    alert_modes: ["support_reclaim"],
    auto_watchlist: true,
  }),
  { line_type: "reference", alert_modes: [], auto_watchlist: false },
);
assert.match(lineLabel({ ...raw, line_type: "resistance" }), /压力线/);
const pressure = newLineSettings("resistance");
assert.deepEqual(
  savedLineSettings(pressure).alert_modes,
  ["pressure_close_breakout", "pressure_touch"],
  "POST/PATCH draft uses backend pressure mode keys",
);
pressure.alert_modes.pop();
assert.equal(
  newLineSettings("resistance").alert_modes.length,
  2,
  "editing a draft cannot mutate the shared allowed modes",
);
console.log(
  "chart marks: persistent price scope, adjustment anchor isolation and shared periods passed",
);
