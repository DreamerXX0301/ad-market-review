import { resolve } from "node:path";
import { normalizePath, type Plugin } from "vite";

const thirdParty = /[\\/]node_modules[\\/]/;
const scriptModule = /\.(?:[cm]?[jt]s|[jt]sx|json)(?:[?#]|$)/i;
const authorContact =
  /联系作者|author-wechat\.jpg|\\u8054\\u7cfb\\u4f5c\\u8005/i;

export function desktopObfuscation(): Plugin {
  let root: string;
  const processed = new Set<string>();
  const isApplication = (id: string) => {
    const path = normalizePath(id.replace(/^\0/, ""));
    return (
      path.startsWith(root) && scriptModule.test(path) && !thirdParty.test(path)
    );
  };

  return {
    name: "desktop-obfuscation",
    apply: "build",
    configResolved(config) {
      root = normalizePath(resolve(config.root)) + "/";
    },
    buildStart() {
      processed.clear();
    },
    renderChunk: {
      order: "post",
      async handler(code, chunk) {
        if (authorContact.test(code)) {
          this.error(`Author contact reached desktop chunk ${chunk.name}`);
        }
        const appModules = chunk.moduleIds.filter(isApplication);
        if (!appModules.length) return null;
        if (chunk.moduleIds.some((id) => thirdParty.test(id))) {
          this.error(
            `Application and third-party code mixed in desktop chunk ${chunk.name}`,
          );
        }
        const { default: obfuscator } = await import("javascript-obfuscator");
        const result = obfuscator.obfuscate(code, {
          target: "browser-no-eval",
          compact: true,
          seed: 5173,
          identifierNamesGenerator: "hexadecimal",
          renameGlobals: false,
          renameProperties: false,
          controlFlowFlattening: false,
          deadCodeInjection: false,
          debugProtection: false,
          selfDefending: false,
          simplify: false,
          sourceMap: false,
          stringArray: true,
          stringArrayEncoding: ["base64"],
          stringArrayRotate: false,
          stringArrayThreshold: 0.75,
          // Rolldown and Vite still need these for chunk names and CSS preloads.
          reservedStrings: ["!~\\{", "__VITE_PRELOAD__"],
        });
        for (const id of appModules) processed.add(id);
        return { code: result.getObfuscatedCode(), map: null };
      },
    },
    generateBundle: {
      order: "post",
      handler(_, bundle) {
        if (!processed.size)
          this.error("Desktop build protected no application modules");
        for (const item of Object.values(bundle)) {
          if (item.fileName.endsWith(".map")) {
            this.error(`Source map reached desktop bundle: ${item.fileName}`);
          }
          if (item.type !== "chunk") continue;
          if (
            item.moduleIds.some((id) => isApplication(id) && !processed.has(id))
          ) {
            this.error(
              `Desktop application chunk was not protected: ${item.fileName}`,
            );
          }
          if (/sourceMappingURL\s*=|__VITE_PRELOAD__|!~\{/.test(item.code)) {
            this.error(
              `Unexpected build marker in desktop chunk: ${item.fileName}`,
            );
          }
        }
      },
    },
  };
}
