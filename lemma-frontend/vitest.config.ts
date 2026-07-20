import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = dirname(fileURLToPath(import.meta.url));

// Pure-logic unit tests only (no component/DOM stack). Keep the include tight so
// vitest never tries to load broad Next/React component surfaces.
export default defineConfig({
  resolve: {
    alias: {
      "@": root,
    },
  },
  test: {
    environment: "node",
    include: [
      "components/auth/portal/auth/**/*.{test,spec}.ts",
      "components/agents/*.{test,spec}.ts",
      "lib/**/*.{test,spec}.ts",
    ],
    exclude: [
      "**/node_modules/**",
      // gogett-fork WIP test fixtures under __tests__/ are skipped on
      // purpose: they import from a path that's no longer exported.
      "components/agents/__tests__/**",
    ],
  },
});
