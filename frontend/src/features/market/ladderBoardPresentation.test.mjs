import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

// Bundle the two pure local modules in memory; no UI, service or data requests.
const base = await fs.readFile(
  new URL("./ladderPresentation.ts", import.meta.url),
  "utf8",
);
const board = await fs.readFile(
  new URL("./ladderBoardPresentation.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(
  (base + "\n" + board).replace(/^import .*$/gm, ""),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const {
  ladderThemeGroups,
  ladderThemeIndex,
  buildLadderDisplayGroups,
  isStructuralLadderTopic,
  ladderCardAttribution,
} = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const themes = [
  {
    topic_id: "a",
    name: "同名分类",
    kind: "concept",
    limit_up_count: 7,
    limit_up_observed_count: 7,
  },
  {
    topic_id: "b",
    name: "同名分类",
    kind: "industry",
    limit_up_count: null,
    limit_up_observed_count: 3,
  },
  {
    topic_id: "empty",
    name: "没有已加载成员",
    kind: "concept",
    limit_up_count: 20,
  },
];
const rows = Array.from({ length: 137 }, (_, i) => ({
  instrument_id: `${i}.SH`,
  topics: [
    { topic_id: "a", name: "同名分类" },
    ...(i < 3 ? [{ topic_id: "b", name: "同名分类" }] : []),
  ],
}));
const before = structuredClone({ rows, themes });
const groups = ladderThemeGroups(rows, themes);
assert.deepEqual(
  groups.map((group) => group.topic.topic_id),
  ["a", "b"],
);
assert.equal(
  groups[0].items.length,
  137,
  "theme grouping never truncates or paginates its loaded stock list",
);
assert.equal(groups[1].items.length, 3);
assert.equal(groups[0].countLabel, "7只");
assert.equal(groups[1].countLabel, "已确认≥3只");
assert.deepEqual(
  ladderThemeGroups(rows, themes, "concept").map(
    (group) => group.topic.topic_id,
  ),
  ["a"],
);
assert.deepEqual(
  ladderThemeGroups(rows, themes, "industry").map(
    (group) => group.topic.topic_id,
  ),
  ["b"],
);
assert.deepEqual(
  ladderThemeGroups(
    [{ instrument_id: "x", topics: [{ topic_id: "other", name: "同名分类" }] }],
    themes,
  ),
  [],
  "do not merge different topic IDs by same name",
);
assert.deepEqual(
  ladderThemeGroups([{ instrument_id: "x", topics: ["同名分类"] }], themes),
  [],
  "no ID-less association is assigned to a provider ID",
);
assert.equal(ladderThemeIndex(themes).size, 3);
assert.deepEqual({ rows, themes }, before);

for (const name of [
  "融资融券",
  "沪深股通",
  "ST板块",
  "注册制次新股",
  "沪深300成分股",
  "MSCI中国",
  "富时罗素概念",
])
  assert.equal(isStructuralLadderTopic({ name }), true, name);
for (const name of ["人工智能", "国企改革", "机器人", "股权转让"])
  assert.equal(isStructuralLadderTopic({ name }), false, name);

const displayThemes = Array.from({ length: 12 }, (_, i) => ({
  topic_id: `concept-${String(i).padStart(2, "0")}`,
  name: `真实概念${i}`,
  kind: "concept",
  limit_up_count: 5,
  limit_up_observed_count: 5,
}));
const structural = {
  topic_id: "all-structural",
  name: "融资融券",
  kind: "concept",
  limit_up_count: 60,
  limit_up_observed_count: 60,
};
const sourceIndustry = {
  topic_id: "industry",
  name: "已知行业",
  kind: "industry",
  limit_up_count: 60,
};
const displayRows = Array.from({ length: 60 }, (_, i) => ({
  instrument_id: `${600000 + i}.SH`,
  topics: [displayThemes[Math.floor(i / 5)], structural, sourceIndustry],
}));
const plan = buildLadderDisplayGroups(displayRows, [
  structural,
  sourceIndustry,
  ...displayThemes,
]);
assert.equal(plan.groups.length, 7, "at most six named groups plus other");
const assigned = plan.groups.flatMap((group) =>
  group.items.map((row) => row.instrument_id),
);
assert.equal(assigned.length, 60);
assert.equal(
  new Set(assigned).size,
  60,
  "each loaded stock appears exactly once",
);
assert(
  plan.groups
    .slice(0, -1)
    .every(
      (group) => group.items.length >= 2 && group.theme.kind === "concept",
    ),
);
assert.equal(plan.groups.at(-1).key, "other");
assert(
  !plan.groups.some(
    (group) =>
      group.theme?.topic_id === structural.topic_id ||
      group.theme?.topic_id === sourceIndustry.topic_id,
  ),
);
const identity = (result) =>
  result.groups.map((group) => [
    group.key,
    group.items.map((row) => row.instrument_id),
  ]);
assert.deepEqual(
  identity(
    buildLadderDisplayGroups(
      [...displayRows].reverse(),
      [...displayThemes].reverse().concat(structural, sourceIndustry),
    ),
  ),
  identity(plan),
  "partition ties and item order are deterministic",
);
const a = {
  topic_id: "a",
  name: "题材甲",
  kind: "concept",
  limit_up_count: null,
  limit_up_observed_count: 20,
};
const b = { topic_id: "b", name: "题材乙", kind: "concept", limit_up_count: 3 };
const c = { topic_id: "c", name: "题材丙", kind: "concept", limit_up_count: 2 };
const overlapRows = Array.from({ length: 6 }, (_, i) => ({
  instrument_id: `${i}.SH`,
  topics: [
    ...(i < 4 ? [a] : []),
    ...(i >= 2 && i <= 4 ? [b] : []),
    ...(i >= 4 ? [c] : []),
  ],
}));
const overlap = buildLadderDisplayGroups(overlapRows, [a, b, c]);
assert.deepEqual(
  overlap.groups.map((group) => [group.key, group.items.length]),
  [
    ["id:a", 4],
    ["id:c", 2],
  ],
  "greedy ranks unassigned matches, not raw association totals",
);
assert.equal(
  overlap.groups[0].countLabel,
  "已确认≥20只",
  "associated source total remains separate from assigned four",
);
assert.equal(overlap.groups[0].theme.limit_up_count, null);
assert.equal(overlap.topicsByInstrument.get("0.SH").topic_id, "a");
assert.equal(overlap.topicsByInstrument.get("4.SH").topic_id, "c");
const noConcept = buildLadderDisplayGroups(
  displayRows.map((row) => ({ ...row, topics: [structural, sourceIndustry] })),
  [structural, sourceIndustry],
);
assert.equal(noConcept.groups.length, 1);
assert.equal(noConcept.groups[0].topic, null);
assert.equal(noConcept.groups[0].items.length, 60);
console.log(
  "ladder board: unique complete deterministic partition, six-group cap, structural-label exclusions, unassigned greedy counts and separate source bounds passed",
);

const publishing = {
  instrument_id: "601999.SH",
  name: "出版传媒",
  source_industry: "出版",
  reason: "出版发行+AI应用+数智基金",
  topics: [
    { topic_id: "state", name: "国企改革", kind: "concept" },
    { topic_id: "media", name: "文化传媒", kind: "industry" },
    { topic_id: "publishing", name: "出版", kind: "industry" },
    { topic_id: "margin", name: "融资融券", kind: "concept" },
    { topic_id: "ai", name: "AI应用", kind: "concept" },
  ],
};
const publishingBefore = structuredClone(publishing);
const attribution = ladderCardAttribution(publishing, {
  key: "id:state",
  name: "国企改革",
  kind: "concept",
});
assert.equal(attribution.sourceIndustry, "出版");
assert.deepEqual(
  attribution.industryTopics.map((topic) => topic.name),
  ["出版", "文化传媒"],
  "same-date source industry takes precedence over a broad concept partition",
);
assert.equal(attribution.sourceReason, "出版发行+AI应用+数智基金");
assert.deepEqual(
  attribution.conceptTopics.map((topic) => topic.name),
  ["国企改革", "AI应用"],
  "all dated concepts remain associations, separate from industry and reason",
);
assert.deepEqual(
  attribution.otherTopics.map((topic) => topic.name),
  ["融资融券"],
);
assert.deepEqual(publishing, publishingBefore);
const missingCause = ladderCardAttribution({
  name: "出版传媒",
  topics: [{ topic_id: "state", name: "国企改革", kind: "concept" }],
});
assert.equal(missingCause.sourceReason, null);
assert.equal(missingCause.sourceIndustry, null);
assert.equal(missingCause.industryTopics.length, 0);
assert.equal(
  missingCause.conceptTopics[0].name,
  "国企改革",
  "never invent industry/cause from a stock name or concept count",
);
const distinct = ladderCardAttribution({
  source_industry: "来源行业甲",
  reason: " — ",
  topics: [
    { topic_id: "i-a", name: "同名行业", kind: "industry" },
    { topic_id: "i-b", name: "同名行业", kind: "industry" },
    { topic_id: "c", name: "同名行业", kind: "concept" },
    { name: "未分类归属" },
  ],
});
assert.equal(distinct.industryTopics.length, 2);
assert.equal(distinct.conceptTopics.length, 1);
assert.equal(distinct.sourceIndustry, "来源行业甲");
assert.equal(distinct.sourceReason, null);
assert.equal(distinct.otherTopics.length, 1);
console.log(
  "ladder attribution: industry, source reason and concept associations stay distinct",
);
