import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

function asModule(source, fileName = "fixture.ts") {
  return (
    "data:text/javascript;base64," +
    Buffer.from(
      ts
        .transpileModule(source, {
          fileName,
          compilerOptions: {
            module: ts.ModuleKind.ES2022,
            target: ts.ScriptTarget.ES2022,
            jsx: ts.JsxEmit.ReactJSX,
          },
        })
        .outputText.replaceAll(
          '"react/jsx-runtime"',
          JSON.stringify(import.meta.resolve("react/jsx-runtime")),
        ),
    ).toString("base64")
  );
}
const helperUrl = asModule(
  await fs.readFile(
    new URL("./permanentDeletePresentation.ts", import.meta.url),
    "utf8",
  ),
);
let source = await fs.readFile(
  new URL("./PermanentDeleteDialog.tsx", import.meta.url),
  "utf8",
);
source = source
  .replace(
    'from "react"',
    `from ${JSON.stringify(import.meta.resolve("react"))}`,
  )
  .replace(
    /import[^;]+from "lucide-react";/,
    "const RotateCcw=()=>null, ShieldCheck=()=>null, Trash2=()=>null;",
  )
  .replace(
    /import \{ list, post, useApi \} from "\.\.\/lib\/api";/,
    `
    let fixtures={};export function setFixtures(value){fixtures=value;}
    const list=value=>Array.isArray(value)?value:Array.isArray(value?.items)?value.items:[];
    const post=()=>{throw new Error("No mutations during SSR verification");};
    const useApi=path=>({data:fixtures[path]||null,loading:false,error:"",reload:()=>{},setData:()=>{}});
  `,
  )
  .replace(
    /import \{ ErrorBox, Loading, Modal, Spinner \} from "\.\/UI";/,
    `
    const Modal=({title,children,escapeUnderStock})=><section data-stock-safe={escapeUnderStock}><h2>{title}</h2>{children}</section>;
    const ErrorBox=({message})=><p role="alert">{message}</p>;
    const Loading=()=> <p>读取中</p>;
    const Spinner=()=>null;
  `,
  )
  .replaceAll(
    'from "./permanentDeletePresentation"',
    `from ${JSON.stringify(helperUrl)}`,
  )
  .replace('import "./PermanentDeleteDialog.css";', "");
const {
  default: Dialog,
  PermanentDeletePreview,
  PermanentDeleteStatus,
  PendingPurgeJobs,
  setFixtures,
} = await import(asModule(source, "dialog.tsx"));
const target = { kind: "strategy", id: "test-strategy", name: "测试策略" };
const preview = {
  target_kind: target.kind,
  target_id: target.id,
  name: target.name,
  can_purge: true,
  confirmation_token: "valid-preview",
  confirmation_text: target.name,
  expires_at_ms: Date.now() + 600000,
  protected_watchlist_count: 0,
  protected_watchlist: [],
  counts: {
    strategies: 1,
    strategy_versions: 2,
    backtests: 3,
    tracking_rounds: 10,
    tracking_events: 17,
    selection_runs: 5,
    selection_candidates: 11,
    tracking_policies: 2,
    policy_applications: 10,
    tracking_ignores: 1,
    execution_settings: 1,
  },
  files: { exclusive_count: 4, exclusive_bytes: 2048, shared_count: 3 },
  retained: ["独立自选、划线、真实全市场行情与已有备份保留。"],
};
const render = (component, props) =>
  renderToStaticMarkup(createElement(component, props));
setFixtures({ "/strategies/test-strategy/purge-preview": preview });
const dialog = render(Dialog, {
  target,
  onClose: () => {},
  onChanged: () => {},
});
assert.match(dialog, /永久删除确认/);
assert.match(dialog, /无法从回收站恢复/);
assert.match(dialog, /跟踪事件<\/dt><dd>17/);
assert.match(dialog, /执行设置<\/dt><dd>1/);
assert.match(dialog, /4 个 · 2.0 KiB/);
assert.match(dialog, /共享文件 3 个保留/);
assert.match(dialog, /独立自选、划线、真实全市场行情与已有备份保留/);
assert.match(dialog, /aria-label="永久删除确认名称"/);
assert.match(dialog, /class="btn danger" disabled=""/);
assert.match(dialog, /data-stock-safe="true"/);

setFixtures({
  "/strategies/test-strategy/purge-preview": {
    ...preview,
    can_purge: false,
    confirmation_token: null,
    blocked_reason: "自选保护",
    protected_watchlist_count: 24,
    protected_watchlist: [{ instrument_id: "601999.SH", name: "出版传媒" }],
  },
});
const protectedDialog = render(Dialog, {
  target,
  onStock: () => {},
  onClose: () => {},
  onChanged: () => {},
});
assert.match(protectedDialog, /自选保护，无法永久删除/);
assert.match(protectedDialog, /全部 24 只均受到保护/);
assert.match(protectedDialog, /出版传媒/);
assert.match(protectedDialog, /601999.SH/);
assert.doesNotMatch(
  protectedDialog,
  /永久删除确认名称|class="btn danger"|强制删除|移除自选/,
);
const unknown = render(PermanentDeletePreview, {
  preview: { ...preview, counts: {}, files: {} },
});
assert.match(unknown, /跟踪事件<\/dt><dd>—/);
assert.match(unknown, /— 个 · —/);

const failed = render(PermanentDeleteStatus, {
  job: {
    status: "failed",
    business_deleted: true,
    removed_file_count: 2,
    remaining_file_count: 1,
    removed_file_bytes: 1024,
    error: "文件被锁定",
  },
});
assert.match(failed, /清理未完成/);
assert.match(failed, /业务记录已永久删除，文件清理尚未完成/);
assert.match(failed, /文件被锁定/);
assert.doesNotMatch(failed, /永久删除完成|可从回收站恢复/);
const completed = render(PermanentDeleteStatus, {
  job: {
    status: "completed",
    business_deleted: true,
    removed_file_count: 3,
    remaining_file_count: 0,
    removed_file_bytes: 2048,
  },
});
assert.match(completed, /永久删除完成/);
assert.match(completed, /专属业务记录与本次可回收文件已清理完成/);
setFixtures({
  "/workspace-purges/job-1": {
    purge_id: "job-1",
    status: "failed",
    business_deleted: true,
    can_retry: true,
    error: "校验后重试",
  },
});
const retry = render(Dialog, {
  target: { ...target, purgeId: "job-1" },
  onClose: () => {},
  onChanged: () => {},
});
assert.match(retry, /重试清理/);
assert.doesNotMatch(retry, /永久删除确认名称|class="btn danger"|重新核验范围/);
setFixtures({
  "/workspace-purges/job-1": {
    purge_id: "job-1",
    status: "failed",
    business_deleted: false,
    can_retry: false,
    can_reconfirm: true,
    error: "范围已变化",
  },
});
const reconfirm = render(Dialog, {
  target: { ...target, purgeId: "job-1" },
  onClose: () => {},
  onChanged: () => {},
});
assert.match(reconfirm, /业务记录尚未删除/);
assert.match(reconfirm, /重新核验范围/);
assert.doesNotMatch(
  reconfirm,
  /关闭此窗口后仍会继续|重试清理|class="btn danger"/,
);
setFixtures({
  "/workspace-purges/job-1": {
    purge_id: "job-1",
    status: "failed",
    business_deleted: true,
    can_retry: true,
    can_reconfirm: true,
  },
});
assert.doesNotMatch(
  render(Dialog, {
    target: { ...target, purgeId: "job-1" },
    onClose: () => {},
    onChanged: () => {},
  }),
  /重新核验范围/,
  "a partial physical cleanup can never be replaced by a widened plan",
);
setFixtures({
  "/workspace-purges/job-1": {
    purge_id: "unrelated-old-job",
    status: "completed",
  },
});
assert.doesNotMatch(
  render(Dialog, {
    target: { ...target, purgeId: "job-1" },
    onClose: () => {},
    onChanged: () => {},
  }),
  /永久删除完成/,
);

setFixtures({
  "/workspace-purges?status=unfinished&target_kind=strategy&limit=20&offset=0&_refresh=2":
    {
      total: 25,
      items: [
        {
          purge_id: "a",
          target_kind: "strategy",
          target_id: "removed-strategy",
          name: "已无业务实体的失败任务",
          status: "failed",
          business_deleted: true,
          can_retry: true,
        },
        {
          purge_id: "b",
          target_kind: "strategy",
          target_id: "old",
          name: "已完成任务",
          status: "completed",
        },
        {
          purge_id: "c",
          target_kind: "backtest",
          target_id: "report",
          name: "另一类任务",
          status: "failed",
        },
      ],
    },
});
const pending = render(PendingPurgeJobs, {
  kind: "strategy",
  refresh: 2,
  onOpen: () => {},
});
assert.match(pending, /已无业务实体的失败任务/);
assert.match(pending, /查看 \/ 重试/);
assert.match(pending, /下一页/);
assert.doesNotMatch(pending, /已完成任务|另一类任务/);
console.log(
  "permanent deletion SSR: exact scope, protected watchlist block, retained data, pending/failed/completed distinction, persisted retries and pagination passed",
);
