#!/usr/bin/env node
/** Render project-owned SVG diagrams; no application/runtime dependency is added.
 * Build-only renderer: @resvg/resvg-js 2.6.2, MPL-2.0,
 * https://github.com/thx/resvg-js . See docs/diagrams/README.md.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const modulePath = process.env.SHIXU_DOC_RENDER_MODULE;
if (!modulePath) throw new Error('Set SHIXU_DOC_RENDER_MODULE to the isolated @resvg/resvg-js installation.');
const { Resvg } = require(modulePath);
const packageInfo = require(path.join(modulePath, 'package.json'));
if (packageInfo.version !== '2.6.2') throw new Error('The reviewed documentation renderer version is 2.6.2.');
for (const name of ['daily-review-workflow', 'strategy-evidence-flow', 'ai-research-flow']) {
  const source = path.join(root, 'docs/diagrams', name + '.svg');
  const svg = readFileSync(source, 'utf8');
  if (/<(?:script|foreignObject|image|use)\b|<!DOCTYPE|<!ENTITY|(?:href|xlink:href)\s*=/i.test(svg)) {
    throw new Error('Public diagrams must be self-contained SVG primitives: ' + name);
  }
  const renderer = new Resvg(svg, { font: { loadSystemFonts: true, defaultFontFamily: 'PingFang SC' } });
  if (renderer.imagesToResolve().length) throw new Error('External images are not part of public diagrams.');
  const image = renderer.render();
  if (image.width > 8192 || image.height > 8192 || image.width * image.height * 4 > 64 * 1024 * 1024) {
    throw new Error('Public diagram exceeds the release dimensions.');
  }
  const png = image.asPng();
  if (png.length > 2 * 1024 * 1024) throw new Error('Public PNG exceeds the release byte limit.');
  writeFileSync(path.join(root, 'docs/images', name + '.png'), png);
  process.stdout.write(`${name}: ${image.width}x${image.height}, ${png.length} bytes\n`);
}
