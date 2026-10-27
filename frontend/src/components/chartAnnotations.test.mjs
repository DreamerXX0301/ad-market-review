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
  defaultLineColor,
  selectableAnnotationColors,
  annotationColors,
  lineMatchesScope,
  lineLabel,
  savedLineSettings,
  newLineSettings,
  projectChartLine,
  quietLineSettings,
  levelOriginLabel,
  levelPreviewMatchesChart,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const raw = {
  annotation_id: "raw-line",
  instrument_id: "601900.SH",
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
for (const type of ["support", "resistance", "reference"]) {
  assert.deepEqual(
    quietLineSettings(type),
    {
      line_type: type,
      alert_modes: [],
      auto_watchlist: false,
    },
    "automatic and index levels never arm stock rules",
  );
}
assert.equal(levelOriginLabel(raw), "手动划线");
assert.match(
  levelOriginLabel({
    origin: {
      algorithm: "confirmed-pivot-cluster-v1",
      period: "W",
      as_of: "2026-10-23",
      manual_override: true,
      evidence: [{ date: "2026-10-02", confirmed_on: "2026-10-16", price: 12 }],
    },
  }),
  /已手动修改.*完整周K.*2026-10-23.*1处历史分型/,
);
const levelChart = {
  instrument: { instrument_id: "601900.SH" },
  snapshot_id: "market-a",
  as_of: "2026-10-23",
  period: "D",
  price_basis: "none",
};
const levelPreview = {
  instrument_id: "601900.SH",
  parameters: {
    snapshot_id: "market-a",
    as_of: "2026-10-23",
    period: "D",
    price_basis: "none",
  },
};
assert.equal(
  levelPreviewMatchesChart(levelPreview, levelChart, "601900.SH"),
  true,
);
for (const changes of [
  { period: "W" },
  { as_of: "2026-10-22" },
  { snapshot_id: "market-b" },
  { instrument: { instrument_id: "000001.SH" } },
  { price_basis: "forward", adjustment: { anchor: "2026-10-23" } },
]) {
  assert.equal(
    levelPreviewMatchesChart(
      levelPreview,
      { ...levelChart, ...changes },
      "601900.SH",
    ),
    false,
  );
}
assert.equal(
  levelPreviewMatchesChart(levelPreview, levelChart, "000001.SH"),
  false,
);
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
const saved = {
  ...raw,
  annotation_id: "saved",
  instrument_id: "601900.SH",
  color: "green",
  price_basis: "forward_event_affine",
  anchor_date: "2026-10-21",
  line_type: "support",
  revision: 2,
  alert_modes: ["support_reclaim"],
  auto_watchlist: true,
};
const chart = {
  instrument: { instrument_id: saved.instrument_id },
  as_of: "2026-10-23",
  snapshot_id: "current-market",
  price_basis: "forward_event_affine",
  bars: [
    {
      date: "2026-10-23",
      open: 20,
      close: 21,
      low: 19,
      high: 22,
      adjustment_anchor: "2026-10-23",
      price_basis: "forward_event_affine",
    },
  ],
  adjustment: {
    usable: true,
    model: "event_affine",
    anchor: "2026-10-23",
    events: [],
    events_count: 0,
    quarantined: [],
  },
  annotation_projection_evidence: {
    version: "chart-annotation-projection-v1",
    available: true,
    start_date: "2021-10-20",
    end_date: "2026-10-23",
    snapshot_id: "current-market",
    source: "published_corporate_actions",
  },
};
const original = structuredClone(saved),
  chartBefore = structuredClone(chart);
for (const period of ["D", "W", "M"]) {
  const result = projectChartLine(saved, { ...chart, period });
  assert.equal(result.price, saved.price);
  assert.equal(result.converted, true);
  assert.match(result.reason, /无新增公司行动/);
  assert.equal(result.line, saved);
  assert.equal(
    projectChartLine(raw, { ...chart, price_basis: "none", period }).price,
    raw.price,
  );
}
const event = {
  date: "2026-10-22",
  cash: 0.5,
  bonus: 0.1,
  rights: 0.2,
  rights_price: 4,
};
const adjusted = {
  ...chart,
  adjustment: { ...chart.adjustment, events: [event], events_count: 1 },
};
const projected = projectChartLine(saved, adjusted);
assert.equal(projected.price, (12.5 - 0.5 + 4 * 0.2) / 1.3);
assert.match(lineLabel(saved, projected.price), /9.8462/);
assert.equal(saved.price, 12.5);
assert.deepEqual(saved, original);
assert.deepEqual(chart, chartBefore);
for (const invalid of [
  { ...chart, snapshot_id: "other" },
  { ...chart, annotation_projection_evidence: undefined },
  {
    ...chart,
    annotation_projection_evidence: {
      ...chart.annotation_projection_evidence,
      available: false,
    },
  },
  {
    ...chart,
    annotation_projection_evidence: {
      ...chart.annotation_projection_evidence,
      end_date: "2026-10-22",
    },
  },
  {
    ...chart,
    annotation_projection_evidence: {
      ...chart.annotation_projection_evidence,
      start_date: "2026-10-22",
    },
  },
  { ...chart, adjustment: { ...chart.adjustment, quarantined: [{}] } },
  { ...chart, adjustment: { ...chart.adjustment, anchor: "2026-10-22" } },
  { ...chart, adjustment: { ...chart.adjustment, events: [event] } },
  {
    ...chart,
    adjustment: { ...chart.adjustment, events: [null], events_count: 1 },
  },
  {
    ...chart,
    adjustment: {
      ...chart.adjustment,
      events: [event, event],
      events_count: 2,
    },
  },
  {
    ...chart,
    adjustment: {
      ...chart.adjustment,
      events: [{ ...event, cash: null }],
      events_count: 1,
    },
  },
  {
    ...chart,
    adjustment: {
      ...chart.adjustment,
      events: [{ ...event, date: "2026-10-24" }],
      events_count: 1,
    },
  },
  { ...chart, bars: [{ ...chart.bars[0], adjustment_anchor: "2026-10-21" }] },
  { ...chart, bars: [{ ...chart.bars[0], price_basis: "none" }] },
  { ...chart, bars: [] },
  { ...chart, bars: [null] },
]) {
  const result = projectChartLine(saved, invalid);
  assert.equal(result.price, null, JSON.stringify(invalid));
  assert.ok(result.reason);
}
assert.equal(
  projectChartLine({ ...saved, anchor_date: "2026-10-24" }, chart).price,
  null,
);
assert.equal(
  projectChartLine({ ...saved, anchor_date: "2026-02-30" }, chart).price,
  null,
);
assert.equal(
  projectChartLine(saved, { ...chart, price_basis: "none" }).price,
  null,
);
assert.equal(projectChartLine(raw, chart).price, null);
assert.equal(
  projectChartLine({ ...saved, anchor_date: chart.as_of }, chart).price,
  saved.price,
);
console.log(
  "saved anchor projections: explicit same-generation interval, real affine actions, no-action proof, D/W/M and immutable original marks passed",
);

assert.deepEqual(["support", "resistance", "reference"].map(defaultLineColor), [
  "green",
  "red",
  "violet",
]);
assert.deepEqual(selectableAnnotationColors, ["green", "red", "violet"]);
assert.ok(
  annotationColors.blue && annotationColors.amber,
  "historical saved colors still render",
);
