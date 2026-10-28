import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(new URL("./noonRichText.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { noonTextParts } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));

const colored = (value) => noonTextParts(value).filter((part) => part.tone !== "neutral");
const sample = "本次5569只有价可比样本中，上涨4497只、下跌916只、平盘156只。主要指数：上证+0.72%、深证+1.99%、创业板-3.00%，科创500.00%。";
assert.deepEqual(colored(sample), [
  { text: "上涨4497只", tone: "up" },
  { text: "下跌916只", tone: "down" },
  { text: "+0.72%", tone: "up" },
  { text: "+1.99%", tone: "up" },
  { text: "-3.00%", tone: "down" },
]);
assert.deepEqual(colored("上涨604 / 下跌4,916 / 平盘42"), [
  { text: "上涨604", tone: "up" },
  { text: "下跌4,916", tone: "down" },
]);
assert.deepEqual(colored("上涨 4,497 只， 下跌 916 家，平盘 156只"), [
  { text: "上涨 4,497 只", tone: "up" },
  { text: "下跌 916 家", tone: "down" },
]);
for (const text of [
  "2026-10-28：代码600838.SH，累计成交额10220.41亿元。",
  "上涨—只 / 下跌未知 / 平盘未提供；上涨因果待核验。",
  "跌幅≥5%，覆盖率98.20%，+0.00%、-0.00%、0.00%均中性。",
  "上涨12.5%不是家数；上涨30交易日不是样本家数。",
  "上涨1,23只为无效千位格式，不局部染色。",
]) assert.deepEqual(colored(text), [], text);
const cases = [sample, "", "普通文字", "同段+20.00%与-20.00%", "<script>+3.00%</script>",
  "首板 30只 / 上涨21 / 下跌9 / 平盘0。", "上涨0只，下跌0家，平盘0只。", "增长+1,234.50%"];
for (const text of cases) {
  const parts = noonTextParts(text);
  assert.equal(parts.map((part) => part.text).join(""), text, "render parts must not rewrite retained prose");
  assert.ok(parts.every((part) => ["up", "down", "neutral"].includes(part.tone)));
}
assert.equal(noonTextParts("普通文字")[0].tone, "neutral");
assert.deepEqual(colored("增长+1,234.50%"), [{ text: "+1,234.50%", tone: "up" }]);
console.log("noon rich text: explicit direction, zero/unknown neutrality, no incidental coloring and lossless text passed");
