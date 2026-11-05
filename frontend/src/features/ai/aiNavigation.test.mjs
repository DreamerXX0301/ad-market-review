import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const load = async (file) => {
  const compiled = ts.transpileModule(
    await fs.readFile(new URL(file, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText;
  return import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );
};
const {
  aiLaunchLocation,
  aiMarketQuestion,
  readAILaunch,
  upgradeChatLocation,
} = await load("./aiNavigation.ts");
const { chatLocation, readChatLocation } = await load("./aiChatLocation.ts");
const anchor = {
  kind: "stock",
  instrumentId: "600519.SH",
  asOf: "2026-10-30",
  reportId: "report_fixture",
};
const shortcut = aiLaunchLocation(anchor, "launch_fixture");
const opened = readAILaunch(shortcut);
assert.equal(opened.key, "launch_fixture");
assert.equal(opened.explicit, true);
assert.equal(opened.instrumentId, anchor.instrumentId);
assert.equal(opened.asOf, anchor.asOf);
assert.ok(
  !shortcut.includes("ai_chat"),
  "opening an entry does not pretend a chat has been created",
);
assert.equal(
  readAILaunch("#ai").explicit,
  false,
  "sidebar entry must preserve the current mounted chat",
);
const linked = chatLocation(
  shortcut,
  { ...anchor, pageRoute: "ai", pageLaunchId: "launch_fixture" },
  "session_fixture",
);
assert.equal(
  readChatLocation(linked, {
    kind: "market",
    pageRoute: "ai",
    pageLaunchId: "launch_fixture",
    asOf: "2020-01-01",
  }),
  "session_fixture",
  "history remains open even if the new-page anchor differs",
);
assert.equal(
  new URLSearchParams(linked.split("?")[1]).get("ai_launch"),
  "launch_fixture",
);
const changed = chatLocation(
  linked,
  {
    kind: "market",
    pageRoute: "ai",
    pageLaunchId: "launch_fixture",
    asOf: "2026-11-02",
  },
  "another_fixture",
);
const changedParams = new URLSearchParams(changed.split("?")[1]);
assert.equal(changedParams.get("ai_day"), "2026-11-02");
assert.equal(
  changedParams.get("ai_stock"),
  null,
  "opening market history must not retain a previous stock",
);
const resolvedMarket = chatLocation(
  linked,
  {
    kind: "market",
    pageRoute: "ai",
    resolvedSession: true,
    pageLaunchId: "launch_fixture",
    asOf: "2026-11-02",
  },
  "session_fixture",
);
const resolvedParams = new URLSearchParams(resolvedMarket.split("?")[1]);
assert.equal(
  resolvedParams.get("ai_stock"),
  null,
  "resolved market history clears a stale loading-time stock anchor even for the same ID",
);
assert.equal(resolvedParams.get("ai_report"), null);
const noChat = chatLocation(
  linked,
  { ...anchor, pageRoute: "ai", pageLaunchId: "launch_fixture" },
  "",
);
assert.equal(new URLSearchParams(noChat.split("?")[1]).get("ai_chat"), null);
assert.equal(readAILaunch(noChat).instrumentId, "600519.SH");
const legacy =
  "#research?ai_chat=session_old&ai_stock=600519.SH&ai_day=2026-10-30&ai_report=report_old";
const upgraded = upgradeChatLocation(legacy);
assert.ok(upgraded.startsWith("#ai?"));
assert.equal(
  readChatLocation(upgraded, { kind: "stock", pageRoute: "ai" }),
  "session_old",
);
assert.equal(readAILaunch(upgraded).reportId, "report_old");
for (const ordinary of [
  "#market?date=2026-10-30",
  "#research",
  "#market?ai_chat=%2Fbad",
])
  assert.equal(upgradeChatLocation(ordinary), ordinary);
const untrusted = readAILaunch(
  "#ai?ai_stock=bad&ai_report=%2Ftmp%2Fsecret&ai_day=2026-02-30&ai_launch=%3Cscript%3E",
);
assert.equal(untrusted.instrumentId, undefined);
assert.equal(untrusted.reportId, undefined);
assert.equal(untrusted.asOf, undefined);
const malformedStock = "回答未完成，以下仅供核对。后续内容.SH";
const unsafeStockLink = aiLaunchLocation({
  ...anchor,
  instrumentId: malformedStock,
});
assert.equal(
  new URLSearchParams(unsafeStockLink.split("?")[1]).get("ai_stock"),
  null,
  "response prose must never become an instrument in a contextual AI link",
);
const malformedChat =
  "#ai?ai_kind=stock&ai_chat=session_fixture&ai_stock=" +
  encodeURIComponent(malformedStock);
const sanitizedChat = chatLocation(
  malformedChat,
  { kind: "stock", pageRoute: "ai", instrumentId: malformedStock },
  "session_fixture",
);
assert.equal(
  new URLSearchParams(sanitizedChat.split("?")[1]).get("ai_stock"),
  null,
  "neither loading-time URL fallback nor malformed page data may restore prose as a stock",
);
assert.equal(
  new URLSearchParams(
    chatLocation(
      malformedChat,
      { ...anchor, pageRoute: "ai", resolvedSession: true },
      "session_fixture",
    ).split("?")[1],
  ).get("ai_stock"),
  anchor.instrumentId,
  "opening an existing chat replaces the polluted anchor with its actual saved identity",
);
assert.equal(
  new URLSearchParams(
    upgradeChatLocation(
      "#research?ai_chat=session_fixture&ai_stock=" +
        encodeURIComponent(malformedStock),
    ).split("?")[1],
  ).get("ai_stock"),
  null,
  "legacy link upgrades also discard malformed stock anchors",
);
for (const [marketStage, clock, phrase] of [
  ["auction", "09:25", /重点名单之外/],
  ["0935", "09:35", /盘中观察快照/],
  ["1000", "10:00", /盘中观察快照/],
  ["1430", "14:30", /盘中观察快照/],
  ["noon", "", /午评快照/],
  ["close", "15:00", /收盘复盘/],
]) {
  const context = { kind: "market", asOf: "2026-10-30", marketStage };
  const link = aiLaunchLocation(context);
  const launch = readAILaunch(link);
  assert.equal(launch.marketStage, marketStage);
  assert.match(aiMarketQuestion(launch), new RegExp(`2026-10-30 ${clock}`));
  assert.match(aiMarketQuestion(launch), phrase);
  assert.doesNotMatch(link, /ai_chat/);
  assert.doesNotMatch(
    chatLocation(link, { ...context, pageRoute: "ai" }, "sample_chat"),
    /ai_stage/,
  );
}
const noonQuestion = aiMarketQuestion({kind: "market", asOf: "2026-11-05", marketStage: "noon"});
assert.match(noonQuestion, /竞价.*全部已存上午盘中观察.*午评本身/);
assert.doesNotMatch(noonQuestion, /11:30/, "a configured 11:35 noon capture must not be described as 11:30");
assert.notEqual(
  readAILaunch("#ai?ai_kind=market&ai_day=2026-10-30&ai_stage=1000").key,
  readAILaunch("#ai?ai_kind=market&ai_day=2026-10-30&ai_stage=1430").key,
);
for (const hash of [
  "#ai?ai_kind=market&ai_day=2026-10-30&ai_stage=__proto__",
  "#ai?ai_kind=market&ai_day=2026-02-30&ai_stage=auction",
  "#ai?ai_kind=stock&ai_day=2026-10-30&ai_stage=1000",
])
  assert.equal(aiMarketQuestion(readAILaunch(hash)), "");
assert.equal(
  readChatLocation("#ai?ai_chat=%2Ftmp", { kind: "stock", pageRoute: "ai" }),
  "",
);
assert.equal(
  chatLocation(shortcut, { kind: "market", pageRoute: "ai" }, "stale_session"),
  shortcut,
  "old retained workspace cannot overwrite a new launch URL",
);
assert.equal(
  readChatLocation(linked, {
    kind: "stock",
    pageRoute: "ai",
    pageLaunchId: "other_launch",
  }),
  "",
);
const restoredLaunch = chatLocation(
  "#ai",
  { kind: "stock", pageRoute: "ai", pageLaunchId: "launch_fixture" },
  "session_fixture",
);
assert.equal(
  new URLSearchParams(restoredLaunch.split("?")[1]).get("ai_launch"),
  "launch_fixture",
);
console.log(
  "AI hub navigation: contextual entry, shared history links, refresh identity, legacy redirect and unsafe anchor rejection passed",
);
