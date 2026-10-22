import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";

const source = await fs.readFile(
  new URL("./WatchlistLineAlerts.tsx", import.meta.url),
  "utf8",
);
const dialog = source.slice(
  source.indexOf("export function LineAlertDialog("),
  source.indexOf("export default function WatchlistLineAlerts("),
);
const prelude = `const Modal=()=>null, LineAlertEvidence=()=>null, CopyStockCode=()=>null, WatchlistButton=()=>null, ArrowUpRight=()=>null;`;
const compiled = ts
  .transpileModule(prelude + dialog, {
    fileName: "dialog.tsx",
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  .outputText.replaceAll(
    '"react/jsx-runtime"',
    JSON.stringify(import.meta.resolve("react/jsx-runtime")),
  );
const { LineAlertDialog } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
const event = Object.freeze({
  event_id: "preserved-event",
  instrument_id: "601900.SH",
  trade_date: "2026-10-21",
  name: "南方传媒",
});
let selected = event,
  drawer = null;
const calls = [];
function render() {
  return LineAlertDialog({
    event: selected,
    onClose() {
      calls.push("close");
      selected = null;
    },
    onStock(value) {
      assert.equal(
        selected,
        null,
        "the reminder must close before the chart opens",
      );
      assert.equal(value, event);
      calls.push("chart");
      drawer = value;
    },
  });
}
const first = render();
const actions = first.props.children[1];
const chartButton = actions.props.children.find(
  (child) => child?.type === "button",
);
chartButton.props.onClick();
assert.deepEqual(calls, ["close", "chart"]);
assert.equal(drawer, event);
assert.equal(selected, null);
// Closing the chart does not delete the immutable saved reminder. It can be reopened and closed normally.
drawer = null;
selected = event;
render().props.onClose();
assert.equal(selected, null);
assert.deepEqual(calls, ["close", "chart", "close"]);
assert.equal(event.event_id, "preserved-event");
console.log(
  "line alert navigation: reminder closes before historical chart, saved event survives and reopened X closes normally passed",
);
