import { resolve } from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  test: {
    // Pure-logic whiteboard modules are deliberately DOM-free and run in node.
    // DOM-facing modules (renderer/input/engine) are verified via the type
    // check + Vite build + manual smoke test, not exercised in unit tests.
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
