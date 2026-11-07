import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/* The web's code resolves where it lives, as in the app (`web-imports.js`). */
const { WEB, SHIMS } = createRequire(import.meta.url)("./web-imports.js") as { WEB: string; SHIMS: Record<string, string> };
const here = (file: string) => fileURLToPath(new URL(file, import.meta.url));
const shimmed = new Map(Object.entries(SHIMS).map(([web, own]) => [join(WEB, web), here("./" + own)]));

export default defineConfig({
    plugins: [
        {
            name: "web-shims",
            enforce: "pre",
            async resolveId(source, importer, options) {
                if (!importer?.startsWith(WEB)) return null;
                const found = await this.resolve(source, importer, { ...options, skipSelf: true });
                return (found && shimmed.get(found.id)) ?? null;
            },
        },
    ],
    resolve: {
        alias: [
            { find: "react-native", replacement: here("./tests/react-native-stub.ts") },
            { find: /^@web\//, replacement: WEB + "/" },
            { find: /^@\//, replacement: WEB + "/" },
        ],
        /* A package a web file imports comes from the app's node_modules. */
        dedupe: ["react", "@tanstack/react-query", "lemma-sdk"],
    },
});
