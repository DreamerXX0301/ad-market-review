/** Frozen expanded groups: no API, source calls or application writes. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const encode = (source) =>
  "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const cache = new Map();
async function moduleUrl(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const job = (async () => {
    let source = ts.transpileModule(await fs.readFile(url, "utf8"), {
      fileName: url.pathname,
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText;
    for (const name of [
      ...new Set(
        [...source.matchAll(/from\s+["']([^"']+)["']/g)].map(
          (match) => match[1],
        ),
      ),
    ]) {
      const target = name.startsWith(".")
        ? await moduleUrl(new URL(name + ".ts", url))
        : import.meta.resolve(name);
      source = source
        .replaceAll('from "' + name + '"', "from " + JSON.stringify(target))
        .replaceAll("from '" + name + "'", "from " + JSON.stringify(target));
    }
    return encode(source);
  })();
  cache.set(url.href, job);
  return job;
}
const { auctionStockListRows: rows, auctionStockBoardCounts: counts } =
  await import(
    await moduleUrl(
      new URL("./auctionStockListPresentation.ts", import.meta.url),
    )
  );
const stock = (n, board, height, pct, extra = {}) => ({
  instrument_id: String(n).padStart(6, "0") + ".SH",
  name: "合成" + n,
  board,
  previous_board_count: height,
  previous_state: height == null ? "unknown" : "limit_up",
  state: "observed",
  auction_pct: pct,
  reasons: ["严格前日已核验涨停"],
  board_scope: "来源已核验范围",
  ...extra,
});
const quotes = [
  stock(1, "main", 2, 3),
  stock(2, "chinext", 2, -2),
  stock(3, "star", 1, 0),
  stock(4, "beijing", 5, 1),
  stock(5, "bogus", null, 4),
  stock(6, "main", 2, 99, { state: "unknown" }),
  stock(7, "main", 8, NaN),
  stock(8, "unknown", null, Infinity),
  stock(9, "star", 2, "10"),
  stock(10, "main", 3, 4, { state: "failed" }),
  stock(11, "main", null, 8, { state: undefined }),
  stock(12, "chinext", 2, null),
];
const ids = quotes
  .map((row) => row.instrument_id)
  .concat(["688001.SH", quotes[0].instrument_id, quotes[0].instrument_id]);
const before = structuredClone({ ids, quotes });
const codes = (result) => result.map((row) => row.instrument_id);
const byNum = (result) =>
  result.map((row) => Number(row.instrument_id.slice(0, 6)));
assert.deepEqual(
  byNum(rows(ids, quotes)),
  [7, 4, 10, 1, 2, 6, 9, 12, 3, 5, 8, 11, 688001],
  "default height desc, descending quote within height, missing last",
);
assert.deepEqual(
  byNum(rows(ids, quotes, "all", "height", "asc")),
  [3, 1, 2, 6, 9, 12, 10, 4, 7, 5, 8, 11, 688001],
  "height asc keeps same-height quote desc and unknown heights last",
);
assert.deepEqual(
  byNum(rows(ids, quotes, "all", "change", "desc")),
  [5, 1, 4, 3, 2, 6, 7, 8, 9, 10, 11, 12, 688001],
);
assert.deepEqual(
  byNum(rows(ids, quotes, "all", "change", "asc")),
  [2, 3, 4, 1, 5, 6, 7, 8, 9, 10, 11, 12, 688001],
);
assert.equal(
  rows(ids, quotes).length,
  13,
  "duplicate frozen IDs do not repeat stocks",
);
const missing = rows(ids, quotes).find(
  (row) => row.instrument_id === "688001.SH",
);
assert.deepEqual(
  [missing.name, missing.state, missing.board, missing.auction_pct],
  ["688001.SH", "unknown", "unknown", null],
  "missing quote remains unknown even when code resembles a board",
);
assert.deepEqual(
  counts(ids, quotes).map((row) => [row.key, row.count]),
  [
    ["all", 13],
    ["main", 5],
    ["chinext", 2],
    ["star", 2],
    ["beijing", 1],
    ["unknown", 3],
  ],
);
for (const board of ["main", "chinext", "star", "beijing", "unknown"]) {
  const filtered = rows(ids, quotes, board, "change", "asc");
  assert.equal(
    filtered.length,
    counts(ids, quotes).find((row) => row.key === board).count,
  );
  assert.ok(
    filtered.every((row) =>
      board === "unknown"
        ? !["main", "chinext", "star", "beijing"].includes(row.board)
        : row.board === board,
    ),
  );
}
assert.deepEqual(
  byNum(rows(ids, quotes, "main", "height", "desc")),
  [7, 10, 1, 6, 11],
  "board filter intersects expanded membership before sorting",
);
assert.deepEqual(
  codes(rows(["000001.SH", "000004.SH"], quotes, "star")),
  [],
  "quotes outside the original group cannot leak into a board filter",
);
assert.equal(
  rows(ids, quotes).find((row) => row.instrument_id === "000003.SH")
    .auction_pct,
  0,
  "valid zero is retained",
);
assert.ok(
  rows(ids, quotes)
    .filter((row) =>
      [
        "000006.SH",
        "000007.SH",
        "000008.SH",
        "000009.SH",
        "000010.SH",
        "000011.SH",
        "000012.SH",
        "688001.SH",
      ].includes(row.instrument_id),
    )
    .every((row) => row.auction_pct === null),
  "unobserved/nonfinite/string/missing quotes are never numeric returns",
);
const metadata = rows(ids, quotes).find(
  (row) => row.instrument_id === "000001.SH",
);
assert.equal(metadata.reasons, quotes[0].reasons);
assert.equal(metadata.board_scope, quotes[0].board_scope);
assert.equal(metadata.previous_state, "limit_up");
assert.deepEqual(
  { ids, quotes },
  before,
  "neither frozen IDs, rows nor nested metadata is mutated",
);
assert.notEqual(metadata, quotes[0], "returned rows are copies");
assert.deepEqual(rows([], quotes), []);
assert.ok(counts([], quotes).every((row) => row.count === 0));
console.log(
  "Auction stock lists: height/change dual sorts, stable ties, unknown-last finite quotes, zero, explicit board intersection/counts, missing quotes, ID deduplication and immutable metadata passed",
);
