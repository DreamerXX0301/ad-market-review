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
    ssr: new URL("./IntradayBriefCard.tsx", import.meta.url).pathname,
    minify: false,
  },
});
const code = result.output
  .find((item) => item.type === "chunk" && item.isEntry)
  .code.replace(
    /from "([^".][^"]*)"/g,
    (_, id) =>
      `from ${JSON.stringify(pathToFileURL(require.resolve(id)).href)}`,
  );
const {
  default: BriefCard,
  BriefText,
  BriefDirectionText,
  briefDirectionBindings,
  briefSections,
  briefSectionScrollTop,
  activeBriefSection,
} = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const render = (brief, selectedSlot) =>
  renderToStaticMarkup(createElement(BriefCard, { brief, selectedSlot }));

const structured = {
  id: "brief-fixture",
  slot: "1030",
  headline: "10:30 盘面简报",
  body: "旧正文仍保留，但结构化重点优先展示。",
  context: [
    { slot: "auction", label: "09:25 竞价", status: "complete" },
    { slot: "1000", label: "10:00 开盘首轮", status: "missing" },
    { slot: "1030", label: "10:30 早盘一小时", status: "partial" },
  ],
  sections: [
    {
      slot: "auction",
      label: "09:25 竞价",
      status: "complete",
      summary: "高开 40 只，低开 30 只。",
      highlights: [{ id: "a1", kind: "auction", text: "半导体方向 +2.30%。" }],
    },
    {
      slot: "1030",
      label: "10:30 早盘一小时",
      status: "partial",
      summary: "上涨 1,200 只、下跌 300 只。",
      comparison_note: "上一档缺档，不跨时点推算变化。",
      highlights: [
        {
          id: "s1",
          kind: "leading",
          text: "光伏设备 +8.30%，合成甲 +9.80%。",
          sector_id: "884306.TI",
          sector_name: "光伏设备",
        },
        { id: "s2", kind: "strengthening", text: "较上档 +2.30 个百分点。" },
        { id: "s3", kind: "weakening", text: "消费方向 -0.25 个百分点。" },
        { id: "s4", kind: "sealed", text: "封板 25 只。" },
        { id: "s5", kind: "broken", text: "炸板 至少 6 只。" },
        { id: "s6", kind: "__proto__", text: "未知分类保留原文。" },
      ],
    },
  ],
  evidence: [{ id: "e1", label: "成交", text: "<引用> -1,011.20 亿元" }],
};
const parsed = briefSections(structured);
assert.deepEqual(
  parsed.map((section) => section.slot),
  ["auction", "1000", "1030"],
);
assert.equal(parsed[1].status, "missing");
assert.match(parsed[1].summary, /未留存/);
assert.equal(parsed[2].highlights.length, 6);
const html = render(structured);
assert.match(html, /<h2>10:30 早盘一小时累计简报<\/h2>/);
assert.ok(
  html.indexOf('class="ibt-header"') < html.indexOf('class="ibt-scroll"'),
  "header and time chips stay outside the scroll viewport",
);
assert.match(html, /aria-label="分时段简报内容"/);
assert.match(html, /aria-current="step"[^>]*>.*?10:30/s);
for (const label of [
  "领涨方向",
  "相对走强",
  "相对回落",
  "封板变化",
  "炸板观察",
  "竞价方向",
])
  assert.ok(html.includes(label), label);
assert.doesNotMatch(html, /旧正文仍保留/);
assert.match(html, /class="ibt-highlight ibt-market"/);
assert.match(
  html,
  /&lt;引用&gt; <span class="intraday-brief-value down">-1,011\.20 亿元/,
);
assert.ok(
  html.indexOf('class="intraday-brief-evidence ibt-evidence"') >
    html.indexOf('class="ibt-highlights"'),
);
assert.doesNotMatch(html, /<details[^>]* open/);
assert.match(
  render(structured, "1000"),
  /data-slot="1000" data-status="missing"/,
);
assert.match(
  render(structured, "1400"),
  /data-slot="1400" data-status="unavailable"/,
);

const legacyBody =
  "2026-11-05 上涨 1,200 只、下跌 300 只，涨幅 +2.34% → −0.56%，平盘 +0.00%。\n封板 25 只、炸板 至少 6 只，放量后缩量。";
const legacy = render({
  headline: "10:30 盘面简报",
  body: legacyBody,
  evidence: structured.evidence,
});
assert.match(
  legacy,
  /2026-11-05 <span class="intraday-brief-value up">上涨 1,200 只/,
);
assert.match(legacy, /class="intraday-brief-value down">下跌 300 只/);
assert.match(
  legacy,
  /class="intraday-brief-value up">\+2\.34%<\/span> → <span class="intraday-brief-value down">−0\.56%<\/span>，平盘 \+0\.00%。\n/,
);
assert.match(legacy, /class="intraday-brief-value up">封板 25 只/);
assert.match(
  legacy,
  /class="intraday-brief-value intraday-brief-warning">炸板 至少 6 只/,
);
assert.match(
  legacy,
  /class="intraday-brief-value up">放量<\/span>后<span class="intraday-brief-value down">缩量/,
);

const old = briefSections({
  slot: "1030",
  sections: [],
  body: "09:25 竞价 · 高开35只。\n09:25 竞价 · 方向甲 +2.3%。\n10:30 早盘一小时：方向乙 -1.2%。",
  context: [
    { slot: "auction", label: "09:25 竞价", status: "complete" },
    { slot: "1030", label: "10:30 早盘一小时", status: "complete" },
  ],
});
assert.deepEqual(
  old.map((section) => section.slot),
  ["auction", "1030"],
);
assert.equal(old[0].summary, "高开35只。\n方向甲 +2.3%。");
assert.equal(old[1].summary, "方向乙 -1.2%。");
const escapedLabel = briefSections({
  body: "10:00 观察(1) · 真实原文。",
  context: [{ slot: "1000", label: "10:00 观察(1)" }],
});
assert.equal(
  escapedLabel[0].summary,
  "真实原文。",
  "labels with RegExp punctuation remain literal",
);

const unsafe = render({
  headline: '<script>alert("x")</script>',
  body: "<img src=x onerror=alert(1)> +0.00% -0.00% 上涨 0 只 炸板 0 家",
  context: [null],
  evidence: [{ text: "<a href=javascript:alert(1)>来源</a>" }],
});
assert.match(unsafe, /&lt;script&gt;/);
assert.match(unsafe, /&lt;img src=x/);
assert.match(unsafe, /&lt;a href=javascript:/);
assert.doesNotMatch(unsafe, /<script>|<img |<a href/);
const zero = renderToStaticMarkup(
  createElement(BriefText, {
    text: "+0.00% −0.00% +0只 上涨 0 只、封板 至少 0 家、炸板 0 只",
  }),
);
assert.doesNotMatch(
  zero,
  /class=/,
  "zero values stay neutral across signed changes and event counts",
);
assert.match(render({}), /暂未生成可展示的简报/);
assert.match(
  render({
    slot: "noon",
    headline: "11:30 旧午评标题",
    sections: [
      {
        slot: "noon",
        label: "11:35 午评",
        status: "complete",
        summary: "真实回执",
        highlights: [],
      },
    ],
  }),
  /<h2>11:35 午评累计简报<\/h2>/,
);
assert.match(
  legacy,
  /<h2>10:30 盘面简报<\/h2>/,
  "old body-only headlines keep their original wording",
);

const selectedDirections = [];
const onSelectDirection = (...args) => selectedDirections.push(args);
const linked = BriefDirectionText({
  item: structured.sections[1].highlights[0],
  slot: "1030",
  label: "10:30 早盘一小时",
  onSelectDirection,
});
const directionButton = linked.props.children.find(
  (node) => node?.type === "button",
);
assert.ok(directionButton);
directionButton.props.onClick();
assert.deepEqual(
  selectedDirections,
  [["1030", "884306.TI"]],
  "direction links select the explicit source identity and the highlight's own time slot",
);
const clickable = renderToStaticMarkup(
  createElement(BriefCard, { brief: structured, onSelectDirection }),
);
assert.match(
  clickable,
  /class="ibt-direction-link"[^>]*aria-label="查看 10:30 早盘一小时 光伏设备个股"/,
);
assert.doesNotMatch(clickable, /href=|合成甲个股/);
assert.deepEqual(
  briefDirectionBindings({ text: "只有文字没有来源标识：光伏设备" }),
  [],
  "wording alone cannot resolve a clickable sector",
);
assert.deepEqual(
  briefDirectionBindings({
    sector_id: "A",
    sector_name: "同名题材",
    current_sector_counts: [{ sector_id: "B", name: "同名题材" }],
  }),
  [],
  "ambiguous labels cannot silently choose a different source identity",
);
const countLinks = briefDirectionBindings({
  current_sector_counts: [
    { sector_id: "A", name: "题材甲" },
    { sector_id: "B", name: "题材乙" },
  ],
});
assert.deepEqual(countLinks, [
  { id: "A", name: "题材甲" },
  { id: "B", name: "题材乙" },
]);

const viewport = {
  scrollTop: 120,
  getBoundingClientRect: () => ({ top: 300 }),
};
const target = { getBoundingClientRect: () => ({ top: 570 }) };
assert.equal(
  briefSectionScrollTop(viewport, target),
  378,
  "internal geometry adds only its own existing scroll offset",
);
assert.equal(
  briefSectionScrollTop(viewport, {
    getBoundingClientRect: () => ({ top: 0 }),
  }),
  0,
);

const scrollCalls = [];
const scrollBox = {
  scrollTop: 0,
  scrollLeft: 29,
  clientHeight: 250,
  scrollHeight: 850,
  getBoundingClientRect: () => ({ top: 300 }),
  scrollTo(options) {
    scrollCalls.push(options);
    if (options.behavior === "auto") this.scrollTop = options.top;
  },
};
const sectionPositions = [13, 113, 313, 713];
const sectionNodes = ["auction", "1000", "1430", "close"].map(
  (slot, index) => ({
    slot,
    element: {
      getBoundingClientRect: () => ({
        top: 300 + sectionPositions[index] - scrollBox.scrollTop,
        height: [100, 200, 400, 100][index],
      }),
    },
  }),
);
for (const [position, expected] of [
  [0, "auction"],
  [100, "1000"],
  [300, "1430"],
  [599, "close"],
  [400, "1430"],
  [100, "1000"],
  [0, "auction"],
]) {
  scrollBox.scrollTop = position;
  assert.equal(
    activeBriefSection(scrollBox, sectionNodes),
    expected,
    "scrolling both ways follows the brief's own reading line, including its short last section",
  );
}
assert.equal(activeBriefSection(scrollBox, []), null);
assert.equal(
  activeBriefSection({ ...scrollBox, clientHeight: 0 }, sectionNodes),
  null,
);
assert.equal(
  activeBriefSection(scrollBox, [
    {
      slot: "hidden",
      element: { getBoundingClientRect: () => ({ top: 0, height: 0 }) },
    },
    ...sectionNodes,
  ]),
  "auction",
  "hidden sections cannot steal the highlight",
);

// Drive the component's real handlers with deterministic hook state and layout.
const encodeModule = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const hooksURL = encodeModule(`
  const values = [], effects = [], pending = [];
  let cursor = 0, effectCursor = 0;
  export function begin() { cursor = 0; effectCursor = 0; }
  export function useState(initial) {
    const index = cursor++;
    if (!(index in values)) values[index] = initial;
    return [values[index], value => { values[index] = value; }];
  }
  export function useRef(initial) {
    const index = cursor++;
    return values[index] ||= { current: initial };
  }
  export function useId() { return "brief-test"; }
  export function useEffect(callback, deps) {
    const index = effectCursor++, previous = effects[index];
    if (previous && deps.every((value, i) => Object.is(value, previous.deps[i]))) return;
    pending.push(() => {
      previous?.cleanup?.();
      effects[index] = { deps, cleanup: callback() };
    });
  }
  export function flushEffects() { pending.splice(0).forEach(callback => callback()); }
  export function dispose() { effects.forEach(effect => effect.cleanup?.()); }
`);
const hooks = await import(hooksURL);
const { default: InteractiveBrief } = await import(
  encodeModule(
    code.replace(
      `from ${JSON.stringify(pathToFileURL(require.resolve("react")).href)}`,
      `from ${JSON.stringify(hooksURL)}`,
    ),
  )
);
const frames = new Map();
let frameId = 0;
const previousWindow = globalThis.window;
globalThis.window = {
  requestAnimationFrame(callback) {
    frames.set(++frameId, callback);
    return frameId;
  },
  cancelAnimationFrame(id) {
    frames.delete(id);
  },
  matchMedia: () => ({ matches: false }),
};
const flushFrames = () => {
  const callbacks = [...frames.values()];
  frames.clear();
  callbacks.forEach((callback) => callback());
};
const interactionBrief = {
  id: "scroll-fixture",
  slot: "close",
  date: "2026-11-05",
  sections: sectionNodes.map(({ slot }) => ({
    slot,
    status: "complete",
    summary: "测试观察",
    highlights: [],
  })),
};
let scrollProps, timeButtons;
function renderInteractive(selectedSlot = "close") {
  hooks.begin();
  const root = InteractiveBrief({ brief: interactionBrief, selectedSlot });
  timeButtons = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (node.props?.className === "ibt-scroll") {
      scrollProps = node.props;
      scrollProps.ref.current = scrollBox;
    }
    if (node.type === "li" && node.props["data-slot"])
      node.props.ref(
        sectionNodes.find((row) => row.slot === node.props["data-slot"])
          .element,
      );
    if (node.type === "button" && node.props["aria-controls"])
      timeButtons.push(node.props);
    for (const child of [node.props?.children].flat(Infinity)) visit(child);
  }
  visit(root);
  hooks.flushEffects();
}
const activeIndex = () =>
  timeButtons.findIndex((button) => button["aria-current"] === "step");
function userScroll(top) {
  scrollBox.scrollTop = top;
  scrollProps.onScroll({ target: scrollBox, currentTarget: scrollBox });
  flushFrames();
  renderInteractive();
}
renderInteractive();
flushFrames();
assert.equal(
  scrollBox.scrollTop,
  600,
  "initial current slot scrolls only its own viewport",
);
scrollProps.onWheel();
userScroll(100);
assert.equal(activeIndex(), 1, "wheel scrolling updates aria-current");
userScroll(300);
assert.equal(
  activeIndex(),
  2,
  "scrolling forward updates the active time button",
);
userScroll(600);
assert.equal(
  activeIndex(),
  3,
  "scrolling to the bottom activates the short close section",
);
sectionPositions[2] = 613;
timeButtons[2].onClick();
userScroll(600);
assert.equal(
  activeIndex(),
  2,
  "a clicked short section keeps its highlight at a clamped destination",
);
scrollProps.onKeyDown({ key: "Home" });
userScroll(0);
assert.equal(
  activeIndex(),
  0,
  "keyboard scrolling releases the clicked destination",
);
sectionPositions[2] = 313;
timeButtons[1].onClick();
assert.deepEqual(scrollCalls.at(-1), {
  top: 101,
  left: 29,
  behavior: "smooth",
});
userScroll(101);
assert.equal(
  activeIndex(),
  1,
  "click-to-scroll remains linked to the requested time",
);
scrollProps.onTouchStart();
userScroll(0);
assert.equal(activeIndex(), 0, "touch scrolling restores the first section");
scrollProps.onScroll({ target: {}, currentTarget: scrollBox });
assert.equal(
  frames.size,
  0,
  "nested scroll events do not change the brief time",
);
scrollProps.onScroll({ target: scrollBox, currentTarget: scrollBox });
scrollProps.onScroll({ target: scrollBox, currentTarget: scrollBox });
assert.equal(frames.size, 1, "rapid scroll events share one animation frame");
renderInteractive("1000");
flushFrames();
renderInteractive("1000");
assert.equal(
  activeIndex(),
  1,
  "changing observation time resets the initial brief position",
);
scrollProps.onScroll({ target: scrollBox, currentTarget: scrollBox });
hooks.dispose();
assert.equal(frames.size, 0, "unmount cancels pending scroll synchronization");
if (previousWindow === undefined) delete globalThis.window;
else globalThis.window = previousWindow;

const openingBrief = {
  slot: "1000",
  sections: [
    { slot: "1000", summary: "十点观察", highlights: [] },
    { slot: "0935", summary: "开盘五分钟观察", highlights: [] },
    { slot: "auction", summary: "竞价原候选", highlights: [] },
    { slot: "0945", summary: "早盘跟踪观察", highlights: [] },
  ],
};
assert.deepEqual(
  briefSections(openingBrief).map((row) => row.slot),
  ["auction", "0935", "0945", "1000"],
);
const earlyHTML = render(openingBrief, "0935");
assert.match(earlyHTML, /09:35 开盘观察/);
assert.match(earlyHTML, /竞价原候选/);
assert.match(render(openingBrief, "0945"), /aria-current="step"[^>]*>[^<]*<[^>]+><\/span>09:45 早盘跟踪/);
assert.deepEqual(
  briefSections({
    slot: "0945",
    body: "09:35 开盘观察 · 开盘复核。\n09:45 早盘跟踪 · 跟踪复核。",
  }).map(({ slot, summary }) => [slot, summary]),
  [["0935", "开盘复核。"], ["0945", "跟踪复核。"]],
);
const source = await import("node:fs/promises").then((fs) =>
  fs.readFile(new URL("./IntradayBriefCard.tsx", import.meta.url), "utf8"),
);
const css = await import("node:fs/promises").then((fs) =>
  fs.readFile(new URL("./IntradayBriefCard.css", import.meta.url), "utf8"),
);
assert.doesNotMatch(
  source,
  /briefSlotScrollLeft|nav\.scrollTo/,
  "the fixed time selector has no automatic horizontal scrolling",
);
assert.match(
  css,
  /\.ibt-slots\s*\{[^}]*grid-template-columns: repeat\(auto-fit/,
  "time chips wrap in the fixed header instead of hiding later stages",
);
console.log(
  "intraday brief timeline: structured/legacy/missing, safe numeric text, internal scroll tracking and click navigation passed",
);
