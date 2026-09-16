import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Tauri loads the dev server from build.devUrl (src-tauri/tauri.conf.json): keep the port fixed.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
    // The screens that type into big forms pass alone but not while every other file runs beside
    // them: the default 5 s is the machine, not the code (they take ~1 s each on an idle one).
    testTimeout: 20_000,
  },
});
