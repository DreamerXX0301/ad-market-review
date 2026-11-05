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
const {
  BriefCard,
  feedbackThemeGroups,
  filterFeedbackTheme,
  IntradaySnapshotFeedback,
  IntradayBoardChanges,
  Rotation,
  rotationChangesForBoards,
  rotationCellChange,
  rotationStageBoards,
  StrengthView,
  mergeIntradayBoards,
  intradayReturnLabel,
  intradayClosingChange,
  showIntradayClosingComparison,
  AnchorDetails,
  intradayAnchorGroups,
  timelineSlots,
  intradaySelectedSlot,
  boardChangeThemeDistribution,
} = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const render = (snapshot) =>
  renderToStaticMarkup(createElement(IntradaySnapshotFeedback, { snapshot }));

const changesMarkup = renderToStaticMarkup(
  createElement(IntradayBoardChanges, {
    snapshot: {
      id: "current",
      board_changes: {
        baseline_slot: "1330",
        source_bindings: {
          previous: { collected_end_at: "2026-11-05T13:30:30+08:00" },
          current: { collected_end_at: "2026-11-05T14:00:30+08:00" },
        },
        groups: {
          new_sealed: { state: "observed", count: 0, items: [] },
          new_limit_down: {
            state: "unknown",
            count: null,
            items: [],
            reason_code: "no_saved_down_pool",
          },
          resealed: { state: "partial", count: 1, items: [] },
        },
      },
    },
  }),
);
assert.match(changesMarkup, /13:30 → 14:00/);
assert.match(changesMarkup, /新增封板<\/span><strong>0<small>只/);
assert.doesNotMatch(
  changesMarkup,
  /新增跌停/,
  "uncollected categories are hidden instead of invented as zero",
);
assert.match(changesMarkup, /炸板回封<\/span><strong>≥1<small>只/);
assert.match(changesMarkup, /本段没有该类变化/);
assert.match(changesMarkup, /新入封板池不等于首次涨停/);
assert.equal(
  renderToStaticMarkup(
    createElement(IntradayBoardChanges, {
      snapshot: {
        board_changes: {
          baseline_slot: "1400",
          groups: { new_sealed: { count: null, state: "unknown" } },
        },
      },
    }),
  ),
  "",
);
const themeChangesMarkup = renderToStaticMarkup(
  createElement(IntradayBoardChanges, {
    snapshot: {
      id: "themes",
      board_changes: {
        baseline_slot: "1400",
        groups: {
          new_sealed: {
            state: "observed",
            count: 2,
            items: [
              {
                instrument_id: "A",
                name: "合成甲",
                themes: [
                  { kind: "industry", name: "半导体" },
                  { kind: "concept", name: "芯片" },
                ],
              },
              {
                instrument_id: "B",
                name: "合成乙",
                themes: [
                  { kind: "industry", name: "半导体" },
                  { kind: "concept", name: "芯片" },
                ],
              },
            ],
          },
        },
      },
    },
  }),
);
assert.match(themeChangesMarkup, /新增封板主要方向 · 各取前三/);
assert.match(themeChangesMarkup, /已列 2 只股票的归属分布/);
assert.match(themeChangesMarkup, /同股可属多个概念，各方向家数不相加/);
assert.match(themeChangesMarkup, /半导体<\/span><b>2 只/);
assert.match(themeChangesMarkup, /芯片<\/span><b>2 只/);

const brief = renderToStaticMarkup(
  createElement(BriefCard, {
    brief: {
      headline: "10:30 盘面简报",
      body: "2026-11-05 上涨 1,200 只、下跌 300 只，涨幅 +2.34% → −0.56%，平盘 +0.00%。\n封板 25 只、炸板 至少 6 只，放量后缩量。",
      evidence: [
        { id: "e1", label: "区间成交", text: "<引用> -1,011.20 亿元" },
      ],
    },
  }),
);
assert.match(brief, /<h2>10:30 盘面简报<\/h2>/);
assert.match(
  brief,
  /2026-11-05 <span class="intraday-brief-value up">上涨 1,200 只<\/span>/,
);
assert.match(brief, /class="intraday-brief-value down">下跌 300 只/);
assert.match(
  brief,
  /class="intraday-brief-value up">\+2\.34%<\/span> → <span class="intraday-brief-value down">−0\.56%<\/span>，平盘 \+0\.00%。\n/,
);
assert.match(brief, /class="intraday-brief-value up">封板 25 只/);
assert.match(
  brief,
  /class="intraday-brief-value intraday-brief-warning">炸板 至少 6 只/,
);
assert.match(
  brief,
  /class="intraday-brief-value up">放量<\/span>后<span class="intraday-brief-value down">缩量/,
);
assert.match(
  brief,
  /&lt;引用&gt; <span class="intraday-brief-value down">-1,011\.20 亿元/,
);

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
assert.match(
  sample,
  /封板 <b>2<\/b>.*首板 <b>≥1<\/b>.*连板 <b>—<\/b>.*最高 <b>≥1 板<\/b>/,
);
assert.match(sample, /样本 <b>≥1<\/b>.*本轮封板 <b>0<\/b>.*晋级 <b>—<\/b>/);
assert.match(sample, /前一交易日 2026-11-02 收盘涨停样本/);
assert.match(sample, /等待同日两份快照/);
assert.equal(
  (sample.match(/<details class="intraday-feedback-card" open=""[^>]*>/g) || [])
    .length,
  3,
);

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
assert.match(broken, /炸板 <b>≥1<\/b>/);
assert.equal(
  (broken.match(/<details class="intraday-feedback-card" open=""[^>]*>/g) || [])
    .length,
  4,
);
assert.match(broken, /1 只池间状态冲突，待核验/);
assert.match(
  broken,
  /data-kind="concept" title="名单留存日 2026-11-02">示例概念/,
);
assert.match(broken, /前日收盘炸板样本待核验/);
assert.match(broken, /本轮未入池 <b>0<\/b>/);
assert.match(broken, /aria-label="复制六位代码 000001"/);

const groupedRows = [
  {
    instrument_id: "300001.SZ",
    name: "样本甲",
    change_pct: 8,
    themes: [
      { name: "芯片", kind: "concept" },
      { name: "电子", kind: "industry" },
    ],
  },
  {
    instrument_id: "300002.SZ",
    name: "样本乙",
    change_pct: 4,
    themes: [
      { name: "芯片", kind: "concept" },
      { name: "电子", kind: "industry" },
    ],
  },
  {
    instrument_id: "300003.SZ",
    name: "样本丙",
    change_pct: 9,
    themes: [{ name: "化工", kind: "industry" }],
  },
];
const themeSummary = render({
  ladder: {
    state: "observed",
    groups: [{ board_count: 1, count: 3, items: groupedRows }],
    broken_board: { state: "partial", count: 5, items: groupedRows },
  },
});
assert.match(
  themeSummary,
  /aria-label="封板题材筛选".*aria-pressed="false".*芯片<\/span><b>2 只<\/b>.*样本均涨幅 <b class="up">\+6\.00%<\/b>/,
);
assert.doesNotMatch(
  themeSummary.split('<div class="intraday-feedback-body">')[0],
  /intraday-feedback-leader/,
);
assert.match(
  themeSummary,
  /分母为已列出的 3 只.*电子<\/span><b>2 只 <small>66\.7%<\/small>/,
);
assert.match(themeSummary, /查看 3 只炸板个股/);

const feedbackRows = (count, from) =>
  Array.from({ length: count }, (_, index) => ({
    instrument_id: `${600001 + from + index}.SH`,
    name: `核验样本${from + index}`,
    change_pct: index === count - 1 ? 29.99 : 2,
    themes: [
      {
        kind: "concept",
        name:
          index < 12
            ? "芯片"
            : index < 22
              ? "芯片设备"
              : index < 30
                ? "光伏"
                : index < 34
                  ? "储能"
                  : `零散概念${index}`,
      },
      { kind: "industry", name: index < 35 ? "电子" : `行业${index}` },
      { kind: "industry", name: "芯片" },
    ],
  }));
const sealedStocks = feedbackRows(60, 0);
const brokenStocks = feedbackRows(40, 100);
const { featured, remaining } = feedbackThemeGroups(sealedStocks, "concept");
assert.deepEqual(
  featured.map(([name, items]) => [name, items.length]),
  [
    ["芯片", 12],
    ["芯片设备", 10],
    ["光伏", 8],
  ],
);
assert.equal(
  remaining.length,
  27,
  "groups outside the first three, including singletons, remain reachable",
);
const withAttributes = sealedStocks.map((row) => ({
  ...row,
  themes: [
    ...row.themes,
    { kind: "concept", name: "融资融券" },
    { kind: "concept", name: "深股通" },
  ],
}));
const categorized = feedbackThemeGroups(withAttributes, "concept");
assert.deepEqual(
  categorized.featured.map(([name]) => name),
  ["芯片", "芯片设备", "光伏"],
);
assert.deepEqual(
  categorized.attributes.map(([name]) => name).sort(),
  ["深股通", "融资融券"].sort(),
);
assert.equal(
  filterFeedbackTheme(withAttributes, "concept", "融资融券").length,
  60,
  "attribute remains selectable from its own section",
);
assert.deepEqual(
  filterFeedbackTheme(sealedStocks, "concept", "芯片"),
  sealedStocks.slice(0, 12),
);
assert.deepEqual(filterFeedbackTheme(sealedStocks, "concept", "不存在"), []);
assert.equal(filterFeedbackTheme(sealedStocks, "industry", "芯片").length, 60);
const denseFeedback = render({
  ladder: {
    state: "observed",
    sealed_count: 60,
    height_state: "observed",
    groups: [
      { board_count: 2, count: 8, items: sealedStocks.slice(0, 8) },
      { board_count: 1, count: 52, items: sealedStocks.slice(8) },
    ],
    broken_board: { state: "observed", count: 40, items: brokenStocks },
  },
});
const [sealCard, breakCard] = denseFeedback
  .split(/<details class="intraday-feedback-card" open=""[^>]*>/)
  .slice(1);
assert.match(sealCard, /盘中封板梯队.*封板 <b>60<\/b>.*核验样本59/);
assert.doesNotMatch(sealCard, /盘中炸板|核验样本100/);
const featuredMarkup = sealCard.split("intraday-feedback-leaders-more")[0];
assert.equal(
  (featuredMarkup.match(/class="intraday-feedback-leader"/g) || []).length,
  3,
);
assert.match(
  sealCard,
  /intraday-feedback-leaders-more"><summary>更多概念 · 27 个.*零散概念59/,
);
assert.match(
  breakCard,
  /盘中炸板.*炸板 <b>40<\/b>.*查看 40 只炸板个股.*核验样本139/,
);
assert.match(breakCard, /更多行业/);
assert.doesNotMatch(breakCard, /盘中封板梯队/);

const more = render({
  relay_feedback: {
    same_day: {
      state: "observed",
      entered_count: 6,
      entered: Array.from({ length: 6 }, (_, i) => ({
        instrument_id: `00000${i + 1}.SZ`,
        name: `留存个股${i + 1}`,
        change_pct: 10,
      })),
    },
  },
});
assert.match(more, /本轮新入池 <b>6<\/b>/);
assert.match(
  more,
  /留存个股4.*<details class="intraday-feedback-more"><summary>其余 2 只留存个股<\/summary>.*留存个股6/,
);

const board = (instrument_id, name, change_pct, limit_up_count) => ({
  instrument_id,
  name,
  kind: "industry",
  change_pct,
  limit_up_count,
});
const highCount = board("A", "涨停最多方向", 2, 9);
const highChange = board("B", "同家数涨幅领先方向", 5, 3);
const lowChange = board("C", "同家数涨幅较低方向", 3, 3);
const missingCount = board("D", "涨停家数待核验方向", 10, null);
const historyScope = { date: "2026-11-05", slot: "1000" };
const directions = renderToStaticMarkup(
  createElement(StrengthView, {
    historyScope,
    snapshot: {
      id: "snapshot",
      strength_coverage: {},
      strong_sectors: [missingCount, lowChange, highChange],
      limit_up_sectors: [highCount, highChange],
      discovery: {
        ...historyScope,
        directions: [{ ...highChange, signal: "accelerating" }],
      },
    },
  }),
);
const directionNames = [
  ...directions.matchAll(
    /aria-pressed="(?:true|false)">(涨停最多方向|同家数涨幅领先方向|同家数涨幅较低方向|涨停家数待核验方向)</g,
  ),
];
assert.deepEqual(
  directionNames.map((match) => match[1]),
  [highCount.name, highChange.name, lowChange.name, missingCount.name],
);
assert.match(directions, /aria-sort="descending".*?按涨停家数升序排列/);
assert.match(directions, /盘中发现<\/button>/);
assert.match(directions, /涨幅扩大/);
assert.equal((directions.match(/aria-label="方向内个股"/g) || []).length, 1);
const directionFacts = directions.match(
  /class="intraday-detail-facts">(.*?)<\/div>/,
)?.[1];
assert.ok(directionFacts);
assert.doesNotMatch(directionFacts, /较开盘|成员涨幅中位数|覆盖|首板|连板/);
const zeroDirectionFacts = renderToStaticMarkup(
  createElement(StrengthView, {
    historyScope,
    requestedBoard: "zero",
    snapshot: {
      strong_sectors: [
        {
          instrument_id: "zero",
          name: "合成零值方向",
          change_pct: 0,
          open_change_pct: 0,
          median_change_pct: 0,
          rankable_members: 0,
          member_count: 10,
          first_board_count: 0,
          multi_board_count: 0,
          board_height_count_state: "observed",
        },
      ],
    },
  }),
);
assert.match(zeroDirectionFacts, /较开盘 <strong class="muted">0\.00%/);
assert.match(zeroDirectionFacts, /首板 <strong>0 只/);
assert.match(zeroDirectionFacts, /连板 <strong>0 只/);

const stockStatus = renderToStaticMarkup(
  createElement(StrengthView, {
    historyScope,
    snapshot: {
      candidates: [
        {
          instrument_id: "920007.BJ",
          name: "参考价个股",
          change_pct: 29.99,
          limit_up_reference_price: 39.49,
        },
        {
          instrument_id: "300001.SZ",
          name: "确认首板个股",
          limit_up_state: "sealed",
          continue_day_cnt: 1,
        },
        {
          instrument_id: "300002.SZ",
          name: "确认二板个股",
          limit_up_state: "sealed",
          continue_day_cnt: 2,
        },
        {
          instrument_id: "300003.SZ",
          name: "板数待核验个股",
          limit_up_state: "sealed",
        },
      ],
    },
  }),
);
assert.match(stockStatus, /data-kind="reference"[^>]+>涨停价参考/);
assert.match(stockStatus, /data-kind="sealed"[^>]*>首板/);
assert.match(stockStatus, /data-kind="sealed"[^>]*>2 连板/);
assert.match(stockStatus, /title="本时点已核验封板，板数待核验">涨停/);

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
    "0935",
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
assert.equal(stages[0].label, "开盘观察");
assert.equal(stages[0].start, "09:35");
assert.ok(!timelineSlots({snapshots:[{slot:"auction",label:"早盘竞价"}]}).some(row=>row.key==="auction"), "legacy auction samples never resurrect the removed timeline tab");
assert.equal(intradaySelectedSlot("auction", stages, new Map([["auction",{id:"a"}]]), "auction"), "0935", "missing opening data is not substituted with auction data");
assert.equal(intradaySelectedSlot(null, stages, new Map([["1000",{id:"b"}],["auction",{id:"a"}]]), "auction"), "1000");
assert.equal(intradaySelectedSlot("0935", stages, new Map([["1000",{id:"b"}]]), "1000"), "0935");
assert.equal(
  timelineSlots({ noon_scheduled_at: "11:35" }).find(
    ({ key }) => key === "noon",
  ).start,
  "11:35",
);

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
assert.match(rotation("close"), /按15:00升序排列/);
assert.match(rotation("close"), /涨停家数优先/);

const indexBoard = (change, extras = {}) => ({
  ...theme,
  change_pct: change,
  prev_close: 100,
  latest_price: 100 + change,
  price_basis: "none",
  currency: "CNY",
  quote_source: "fuyao",
  quote_basis: "provider_index",
  ...extras,
});
const sameDate = { date: "2026-11-05" };
const comparisonInput = {
  snapshot: sameDate,
  previousSnapshot: sameDate,
  slot: "1030",
  previousSlot: "1000",
  current: indexBoard(4),
  previous: indexBoard(2),
};
assert.deepEqual(rotationCellChange(comparisonInput), {
  changePP: 2,
  sealedDelta: null,
});
assert.equal(
  rotationCellChange({ ...comparisonInput, current: indexBoard(-1) }).changePP,
  -3,
);
assert.equal(
  rotationCellChange({ ...comparisonInput, previousSlot: "auction" }).changePP,
  null,
);
assert.equal(
  rotationCellChange({ ...comparisonInput, previousSnapshot: undefined })
    .reason,
  "上一档缺档",
);
assert.equal(
  rotationCellChange({
    ...comparisonInput,
    previousSnapshot: { date: "2026-11-04" },
  }).changePP,
  null,
);
for (const mismatch of [
  { kind: "concept" },
  { instrument_id: "884230.TI" },
  { quote_source: "other" },
  { quote_basis: "archived_member_sample" },
  { currency: "USD" },
  { prev_close: 101 },
  { latest_price: 109 },
  { change_pct: null },
])
  assert.equal(
    rotationCellChange({ ...comparisonInput, current: indexBoard(4, mismatch) })
      .changePP,
    null,
  );
const exactMemberProof = {
  member_reference_sha256: "a".repeat(64),
  member_list_complete: true,
  member_count: 20,
  limit_up_count_state: "observed",
};
const sealedComparison = {
  ...comparisonInput,
  current: indexBoard(4, { ...exactMemberProof, limit_up_count: 3 }),
  previous: indexBoard(2, { ...exactMemberProof, limit_up_count: 1 }),
};
assert.equal(rotationCellChange(sealedComparison).sealedDelta, 2);
assert.equal(
  rotationCellChange({
    ...sealedComparison,
    previous: {
      ...sealedComparison.previous,
      member_reference_sha256: "b".repeat(64),
    },
  }).sealedDelta,
  null,
);
assert.equal(
  rotationCellChange({
    ...sealedComparison,
    previous: { ...sealedComparison.previous, limit_up_count_state: "partial" },
  }).sealedDelta,
  null,
);
assert.equal(
  rotationCellChange({
    ...sealedComparison,
    current: { ...sealedComparison.current, limit_up_count: 21 },
  }).sealedDelta,
  null,
);
const adjacentSlots = [
  { key: "1000", start: "10:00", label: "开盘首轮" },
  { key: "1030", start: "10:30", label: "早盘一小时" },
  { key: "1100", start: "11:00", label: "上午观察" },
  { key: "noon", start: "11:35", label: "午评" },
];
const deltaGrid = renderToStaticMarkup(
  createElement(Rotation, {
    stages: adjacentSlots,
    activeStage: "noon",
    onSelect: () => {},
    samples: new Map([
      [
        "1000",
        {
          ...sameDate,
          sectors: [indexBoard(3, { ...exactMemberProof, limit_up_count: 1 })],
        },
      ],
      [
        "1030",
        {
          ...sameDate,
          sectors: [indexBoard(1, { ...exactMemberProof, limit_up_count: 2 })],
        },
      ],
      [
        "noon",
        {
          ...sameDate,
          summary: { comparison_slot: "1030" },
          sectors: [indexBoard(4, { change_pp: 3 })],
        },
      ],
    ]),
  }),
);
assert.match(deltaGrid, /type="checkbox" checked="".*较上一档变化/);
assert.match(deltaGrid, /intraday-rotation-change down.*较10:00 -2\.00 百分点/);
assert.match(
  deltaGrid,
  /较10:00 -2\.00 百分点<span class="up"> · 封板\+1<\/span>/,
);
assert.match(deltaGrid, /上一档缺档/);
assert.doesNotMatch(deltaGrid, /较10:30 \+3\.00/);
assert.match(deltaGrid, /aria-label="11:35 半导体设备 查看个股"/);

const turnover = render({
  market_pulse: {
    indices: [
      {
        instrument_id: "000001.SH",
        name: "上证指数",
        amount: 30_000_000_000,
        amount_comparison: {
          previous_date: "2026-11-04",
          previous_amount: 26_000_000_000,
          comparison_amount: 30_000_000_000,
          change_from_previous: 4_000_000_000,
        },
        interval_amount: {
          amount: 7_000_000_000,
          change_from_previous: -1_000_000_000,
        },
      },
    ],
    turnover: { amount: 25_000_000_000, valid: 300 },
  },
});
assert.match(turnover, /data-direction="up">放量 40\.00亿元/);
assert.match(turnover, /data-direction="down">缩量 10\.00亿元/);
assert.match(turnover, /前一交易日同档暂无可比成交额/);

// A quote outside the short strength lists remains visible across slots.
const unseenTheme = {
  instrument_id: "886108.TI",
  name: "合成AI应用",
  kind: "concept",
};
const completeSectorSnapshot = {
  date: "2026-11-05",
  slot: "1030",
  id: "intraday_" + "b".repeat(32),
  strength_coverage: {},
  sectors: [theme],
  strong_sectors: [theme],
  rotation_sectors: [{ ...unseenTheme, change_pct: 2.81, limit_up_count: 3 }],
};
assert.deepEqual(
  rotationStageBoards(completeSectorSnapshot).map((row) => row.instrument_id),
  ["886108.TI"],
);
assert.deepEqual(
  rotationStageBoards({ ...completeSectorSnapshot, rotation_sectors: [] }),
  [],
  "an explicit empty projection must not silently reuse a different ranking",
);
const allSectorGrid = renderToStaticMarkup(
  createElement(Rotation, {
    stages,
    activeStage: "1030",
    onSelect() {},
    samples: new Map([
      [
        "1000",
        {
          ...completeSectorSnapshot,
          slot: "1000",
          rotation_sectors: [
            { ...unseenTheme, change_pct: 1.91, limit_up_count: 1 },
          ],
        },
      ],
      ["1030", completeSectorSnapshot],
    ]),
  }),
);
assert.match(allSectorGrid, /1\.91%/);
assert.match(allSectorGrid, /2\.81%/);
assert.match(allSectorGrid, /10:30 合成AI应用 查看个股/);
const requestedRetained = renderToStaticMarkup(
  createElement(StrengthView, {
    snapshot: completeSectorSnapshot,
    requestedBoard: unseenTheme.instrument_id,
    historyScope: { date: "2026-11-05", slot: "1030" },
    onStock() {},
  }),
);
assert.match(
  requestedRetained,
  /合成AI应用/,
  "selecting a rotation-only quote opens the original member scope",
);

const retainedDirection = {
  instrument_id: "885001.TI",
  name: "保留完整指标的题材",
  kind: "concept",
  change_pct: 4.2,
  up_ratio: 75,
  limit_up_count: 3,
  broken_count: 2,
  member_count: 8,
  member_priced_count: 8,
  rankable_members: 8,
  quote_source: "fuyao",
  quote_basis: "provider_index",
  candidates: [{ instrument_id: "000001.SZ", name: "走强个股", change_pct: 7 }],
  sealed_stocks: [
    { instrument_id: "000002.SZ", name: "封板个股", change_pct: 10 },
  ],
  broken: [{ instrument_id: "000003.SZ", name: "炸板个股", change_pct: 6 }],
};
const compactRotation = {
  instrument_id: retainedDirection.instrument_id,
  kind: "concept",
  change_pct: 4.3,
  limit_up_count: 4,
};
const merged = mergeIntradayBoards([retainedDirection], [compactRotation]);
assert.equal(merged[0].up_ratio, 75);
assert.equal(merged[0].broken_count, 2);
assert.equal(merged[0].change_pct, 4.3);
assert.equal(merged[0].limit_up_count, 4);
for (const key of ["candidates", "sealed_stocks", "broken"])
  assert.deepEqual(merged[0][key], retainedDirection[key]);
assert.equal(
  retainedDirection.change_pct,
  4.2,
  "display merge cannot mutate the frozen source row",
);
assert.equal(
  mergeIntradayBoards(
    [retainedDirection],
    [{ ...compactRotation, up_ratio: null }],
  )[0].up_ratio,
  null,
  "explicit unknown must not reuse a previous non-null metric",
);
assert.equal(
  mergeIntradayBoards(
    [retainedDirection],
    [{ ...compactRotation, kind: "industry" }],
  )[0].up_ratio,
  undefined,
  "different classification identity cannot inherit stock coverage",
);
const mergedDirectionHtml = renderToStaticMarkup(
  createElement(StrengthView, {
    snapshot: {
      id: "same-frozen-slot",
      date: "2026-11-05",
      slot: "1400",
      strength_coverage: { pool_complete: true },
      strong_sectors: [retainedDirection],
      rotation_sectors: [compactRotation],
      discovery: {
        date: "2026-11-05",
        slot: "1400",
        directions: [{ ...compactRotation, signal: "accelerating" }],
      },
    },
    historyScope: { date: "2026-11-05", slot: "1400" },
    onStock() {},
  }),
);
assert.match(mergedDirectionHtml, /75\.00%/);
assert.match(
  mergedDirectionHtml,
  /炸板<span class="intraday-break-count">2<\/span>/,
);
assert.match(mergedDirectionHtml, /覆盖.*8.*8/);
assert.match(mergedDirectionHtml, /data-intraday-reading="directions"/);

const manyDirections = Array.from({ length: 710 }, (_, index) => ({
  ...theme,
  instrument_id: `theme-${index}`,
}));
const slotDirections = new Map([
  ["1000", new Map(manyDirections.map((row) => [row.instrument_id, row]))],
  ["1030", new Map(manyDirections.map((row) => [row.instrument_id, row]))],
]);
const visibleChanges = rotationChangesForBoards(
  stages.slice(0, 2),
  slotDirections,
  new Map(),
  manyDirections.slice(0, 12),
);
assert.equal(visibleChanges[0].size, 0);
assert.equal(
  visibleChanges[1].size,
  12,
  "adjacent-slot checks are bounded to the 12 visible directions before expansion",
);
assert.equal(visibleChanges[1].has("theme-12"), false);
assert.equal(
  rotationChangesForBoards(
    stages.slice(0, 2),
    slotDirections,
    new Map(),
    manyDirections,
  )[1].size,
  710,
  "explicit expansion retains every saved direction",
);

assert.deepEqual(
  ["auction", "noon", "1000", "1430", "close"].map(intradayReturnLabel),
  ["竞价涨跌幅", "午间涨跌幅", "盘中涨跌幅", "盘中涨跌幅", "收盘涨跌幅"],
);
const closingDate = "2026-11-05";
const closingHistory = {
  date: closingDate,
  slot: "1400",
  closing_date: closingDate,
  closing_snapshot_id: "formal_close",
};
assert.equal(
  showIntradayClosingComparison(
    { date: closingDate, slot: "1400" },
    closingHistory,
  ),
  true,
);
assert.equal(showIntradayClosingComparison(undefined, undefined), false);
assert.equal(
  showIntradayClosingComparison(
    { date: closingDate, slot: "close" },
    { ...closingHistory, slot: "close" },
  ),
  false,
  "the close slot already displays closing change and does not duplicate it",
);
assert.equal(
  showIntradayClosingComparison(
    { date: closingDate, slot: "1400" },
    { ...closingHistory, closing_date: "2026-11-04" },
  ),
  false,
);
assert.equal(
  showIntradayClosingComparison(
    { date: closingDate, slot: "1400" },
    closingHistory,
    true,
  ),
  false,
  "sector index tables do not inherit stock closing columns",
);
assert.equal(
  intradayClosingChange(
    {
      closing_quote_date: closingDate,
      closing_snapshot_id: "formal_close",
      closing_change_pct: 0,
    },
    closingDate,
  ),
  0,
);
assert.equal(
  intradayClosingChange(
    {
      closing_quote_date: "2026-11-04",
      closing_snapshot_id: "formal_close",
      closing_change_pct: 8,
    },
    closingDate,
  ),
  null,
);
assert.equal(
  intradayClosingChange(
    { closing_quote_date: closingDate, closing_change_pct: 8 },
    closingDate,
  ),
  null,
  "a percentage without a formal snapshot binding is not displayed as the close",
);
assert.equal(
  intradayClosingChange(
    {
      closing_quote_date: closingDate,
      closing_snapshot_id: "another_generation",
      closing_change_pct: 8,
    },
    closingDate,
    "formal_close",
  ),
  null,
);

const anchorDirections = [
  {
    instrument_id: "industry-A",
    name: "名字很长也应该保持单行的行业方向",
    kind: "industry",
    change_pct: 2.8,
    stocks: [],
  },
  {
    instrument_id: "concept-A",
    name: "名字很长的人工智能应用题材",
    kind: "concept",
    change_pct: 4.2,
    stocks: [],
  },
  {
    instrument_id: "industry-B",
    name: "行业乙",
    kind: "industry",
    change_pct: -1,
    stocks: [],
  },
];
const groupedAnchors = intradayAnchorGroups(anchorDirections);
assert.deepEqual(
  groupedAnchors.map((group) => [
    group.label,
    group.rows.map((row) => row.instrument_id),
  ]),
  [
    ["行业", ["industry-A", "industry-B"]],
    ["概念", ["concept-A"]],
  ],
);
const anchorHtml = renderToStaticMarkup(
  createElement(AnchorDetails, {
    snapshot: { slot: "noon", id: "noon-anchor", sectors: anchorDirections },
    requestedBoard: "concept-A",
    historyScope: { date: "2026-11-05", slot: "noon" },
  }),
);
assert.match(anchorHtml, /data-kind="industry" aria-label="行业"/);
assert.match(anchorHtml, /data-kind="concept" aria-label="概念"/);
assert.match(anchorHtml, /title="名字很长的人工智能应用题材"/);
assert.match(
  anchorHtml,
  /class="intraday-anchor-row"[^>]*aria-pressed="true"[^>]*>.*?名字很长的人工智能应用题材/,
);
assert.doesNotMatch(anchorHtml, /class="intraday-anchor-board"/);
assert.equal(
  [...anchorHtml.matchAll(/class="intraday-anchor-items" role="region"/g)].length,
  2,
  "industry and concept expose separate keyboard-scrollable lists",
);
for (const label of ["行业", "概念"]) {
  assert.match(
    anchorHtml,
    new RegExp(
      `<h3><span>${label}</span>.*?</h3><div class="intraday-anchor-items" role="region" aria-label="${label}方向名单" tabindex="0">`,
    ),
    "each permanent group heading stays outside its own scrolling member list",
  );
}
