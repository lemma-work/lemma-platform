import { registerHooks } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const SRC = pathToFileURL(path.resolve(import.meta.dirname, "../src/")).href + "/";
const EXTENSIONS = [".ts", ".tsx", "/index.ts"];

registerHooks({
    resolve(specifier, context, nextResolve) {
        const target = specifier.startsWith("@/") ? SRC + specifier.slice(2) : specifier;
        try {
            return nextResolve(target, context);
        } catch (error) {
            for (const extension of EXTENSIONS) {
                try {
                    return nextResolve(target + extension, context);
                } catch {
                    /* try the next one */
                }
            }
            throw error;
        }
    },
});
