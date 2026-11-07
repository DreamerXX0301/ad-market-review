import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build, normalizePath } from "vite";

const root = fileURLToPath(new URL("../", import.meta.url));
const sourceRoot = normalizePath(
  fileURLToPath(new URL("../src/", import.meta.url)),
);
const stringDecoder =
  "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/=";
const author = /联系作者|author-wechat\.jpg/;

async function bundle(desktop) {
  process.env.VITE_DESKTOP_PREVIEW = desktop ? "1" : "0";
  process.env.VITE_DESKTOP_VERSION = desktop ? "0.1" : "";
  process.env.VITE_READONLY_PREVIEW = "0";
  const result = await build({
    root,
    logLevel: "silent",
    build: { write: false, reportCompressedSize: false },
  });
  return result.output;
}

test("desktop protects all app chunks, including lazy routes, without changing the web build", async () => {
  const desktop = await bundle(true);
  const protectedChunks = desktop.filter(
    (item) =>
      item.type === "chunk" &&
      item.moduleIds.some((id) => normalizePath(id).startsWith(sourceRoot)),
  );
  assert.ok(protectedChunks.some((chunk) => chunk.isEntry));
  assert.ok(protectedChunks.some((chunk) => chunk.isDynamicEntry));
  for (const chunk of protectedChunks) {
    assert.ok(chunk.code.includes(stringDecoder), chunk.fileName);
    assert.ok(!author.test(chunk.code), chunk.fileName);
  }
  assert.ok(desktop.every((item) => !item.fileName.endsWith(".map")));
  const vendors = desktop.filter(
    (item) => item.type === "chunk" && item.name.endsWith("vendor"),
  );
  assert.ok(vendors.length > 0);
  assert.ok(vendors.every((chunk) => !chunk.code.includes(stringDecoder)));

  const web = (await bundle(false)).filter((item) => item.type === "chunk");
  assert.ok(web.some((chunk) => author.test(chunk.code)));
  assert.ok(web.every((chunk) => !chunk.code.includes(stringDecoder)));
});
