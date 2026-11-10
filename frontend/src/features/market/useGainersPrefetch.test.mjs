import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = ts.transpileModule(
  await fs.readFile(
    new URL("./useGainersPrefetch.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const encode = (text) =>
  "data:text/javascript;base64," + Buffer.from(text).toString("base64");
const { previousGainersDays, outsideAuctionWindow } = await import(
  encode(
    source
      .replaceAll(
        '"react"',
        JSON.stringify(
          encode(
            "export function useEffect(){}; export function useMemo(){}; export function useRef(){}",
          ),
        ),
      )
      .replaceAll(
        '"../../lib/api"',
        JSON.stringify(
          encode(
            "export function prefetchGainersReads(){}; export function usePageActive(){}",
          ),
        ),
      ),
  )
);

assert.deepEqual(
  previousGainersDays("2026-11-10", [
    "2026-11-09",
    "2026-11-06",
    "2026-11-11",
    "2026-11-10",
    "2026-11-09",
    "2026-11-05",
  ]),
  ["2026-11-09", "2026-11-06"],
);
assert.deepEqual(previousGainersDays("2026-11-07", ["2026-11-06"]), []);
assert.equal(outsideAuctionWindow(new Date("2026-11-10T09:22:59+08:00")), true);
assert.equal(
  outsideAuctionWindow(new Date("2026-11-10T09:23:00+08:00")),
  false,
);
assert.equal(
  outsideAuctionWindow(new Date("2026-11-10T09:25:30+08:00")),
  false,
);
assert.equal(
  outsideAuctionWindow(new Date("2026-11-10T09:29:59+08:00")),
  false,
);
assert.equal(outsideAuctionWindow(new Date("2026-11-10T09:30:00+08:00")), true);
