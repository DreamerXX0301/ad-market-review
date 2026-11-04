import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { build } from "vite";

const require = createRequire(import.meta.url);
const result = await build({
  configFile: false,
  root: new URL("../../../", import.meta.url).pathname,
  logLevel: "silent",
  build: {
    write: false,
    ssr: new URL("./IntradayObservation.tsx", import.meta.url).pathname,
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
const { IntradaySnapshotFeedback, Rotation, timelineSlots } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const render = (snapshot) =>
  renderToStaticMarkup(createElement(IntradaySnapshotFeedback, { snapshot }));

assert.equal(render({}), "", "legacy snapshots have no fabricated feedback");
const sample = render({
  market_pulse: {
    indices: [
      {
        instrument_id: "000001.SH",
        name: "上证指数",
        latest_price: 3000,
        change_pct: 0,
      },
    ],
    breadth: { up: 0, down: 1, flat: 0, valid: 1, observed: 2 },
  },
  ladder: {
    state: "observed",
    sealed_count: 2,
    height_state: "partial",
    first_board_count: 1,
    highest_board: 1,
  },
  relay_feedback: {
    prior_session: {
      date: "2026-11-02",
      state: "partial",
      total: 1,
      priced: 1,
      sealed_count: 0,
      sealed_state: "observed",
      promotion_state: "unknown",
      promotion_count: null,
    },
    same_day: { state: "unknown" },
  },
});
assert.match(sample, /上证指数/);
assert.match(sample, /可比 1 \/ 2 只/);
assert.match(sample, /封板 2 · 首板 ≥1 · 连板 — · 最高 ≥1 板/);
assert.match(sample, /样本 ≥1 · 本轮封板 0 · 晋级 —/);
assert.match(sample, /前一交易日 2026-11-02 收盘涨停样本/);
assert.match(sample, /等待同日两份快照/);
assert.equal((sample.match(/<details class=/g) || []).length, 3);
assert.ok(!sample.includes("<details open"));

const broken = render({
  ladder: {
    state: "observed",
    sealed_count: 0,
    broken_board: {
      state: "partial",
      count: 1,
      conflict_count: 1,
      items: [
        {
          instrument_id: "000001.SZ",
          name: "示例股",
          change_pct: 6,
          themes: [
            { name: "示例概念", kind: "concept", member_date: "2026-11-02" },
          ],
        },
      ],
    },
  },
  relay_feedback: {
    prior_session: { state: "unknown" },
    prior_broken_session: { state: "unknown" },
    same_day: { state: "partial", absent_count: 0, absent_state: "observed" },
  },
});
assert.match(broken, /炸板 ≥1/);
assert.match(broken, /1 只池间状态冲突，待核验/);
assert.match(
  broken,
  /data-kind="concept" title="名单留存日 2026-11-02">示例概念/,
);
assert.match(broken, /前日收盘炸板样本待核验/);
assert.match(broken, /本轮未入池 0/);
assert.match(broken, /aria-label="复制六位代码 000001"/);

const stages = timelineSlots({
  slots: [{ key: "1000", label: "开盘首轮", start: "10:00" }],
  snapshots: [
    {
      slot: "opening",
      label: "旧快照",
      collected_end_at: "2026-11-04T09:48:10+08:00",
    },
  ],
});
assert.deepEqual(
  stages.map(({ key }) => key),
  [
    "auction",
    "1000",
    "1030",
    "1100",
    "noon",
    "1330",
    "1400",
    "1430",
    "close",
    "opening",
  ],
);
assert.equal(stages.at(-1).legacy, true);

const theme = {
  instrument_id: "884229.TI",
  name: "半导体设备",
  kind: "industry",
  stocks: [],
};
const rotation = (activeStage) =>
  renderToStaticMarkup(
    createElement(Rotation, {
      stages,
      activeStage,
      samples: new Map([
        [
          "noon",
          { sectors: [{ ...theme, change_pct: 3.84, limit_up_count: 0 }] },
        ],
        [
          "close",
          { sectors: [{ ...theme, change_pct: 15.67, limit_up_count: 10 }] },
        ],
      ]),
      onSelect: () => {},
    }),
  );
assert.match(rotation("noon"), /aria-label="11:30 半导体设备 查看个股"/);
assert.match(rotation("noon"), /封板 0 只/);
assert.doesNotMatch(rotation("noon"), /15:00|15\.67%/);
assert.match(rotation("close"), /aria-label="15:00 半导体设备 查看个股"/);
assert.match(rotation("close"), /封板 10 只/);
