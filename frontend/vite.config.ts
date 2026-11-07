import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { desktopObfuscation } from "./scripts/desktop-obfuscation.ts";

export default defineConfig(({ command }) => {
  const desktop =
    command === "build" && process.env.VITE_DESKTOP_PREVIEW === "1";
  return {
    plugins: [react(), ...(desktop ? [desktopObfuscation()] : [])],
    server: {
      port: 5173,
      proxy: {
        "/api": { target: "http://127.0.0.1:8788", changeOrigin: true },
      },
    },
    build: {
      chunkSizeWarningLimit: 1500,
      ...(desktop && {
        sourcemap: false,
        rolldownOptions: {
          output: {
            codeSplitting: {
              includeDependenciesRecursively: false,
              groups: [
                {
                  name: "charts-vendor",
                  test: /[\\/]node_modules[\\/](?:echarts|zrender)[\\/]/,
                  priority: 10,
                },
                { name: "vendor", test: /[\\/]node_modules[\\/]/ },
              ],
            },
          },
        },
      }),
    },
  };
});
