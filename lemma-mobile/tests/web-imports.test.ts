import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/** The web's code runs where it lives (`web-imports.js`), never copied in.
 *  These hold TypeScript to the same folder Metro and vitest use, each shim
 *  to what the app's web code asks of it, and `src/` to holding no copy. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
const { WEB, SHIMS } = createRequire(import.meta.url)(mobile + "web-imports.js") as { WEB: string; SHIMS: Record<string, string> };
const present = existsSync(WEB);

function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) return sources(path);
        return /\.tsx?$/.test(name) ? [path] : [];
    });
}

const read = (file: string) => readFileSync(file, "utf8");
const body = (text: string) => text.replace(/^import[\s\S]*?;\n/gm, "").replace(/^export \* from[^\n]*\n/gm, "").trim();

function moduleAt(base: string): string | null {
    for (const ending of ["", ".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        if (existsSync(base + ending) && statSync(base + ending).isFile()) return base + ending;
    }
    return null;
}

/** Every import a file makes that loads code, with the names it takes. */
function importsOf(file: string): { spec: string; names: string[] }[] {
    const found: { spec: string; names: string[] }[] = [];
    for (const match of read(file).matchAll(/^(?:import|export)\s+(type\s+)?([\s\S]*?)\s+from\s+"([^"]+)";/gm)) {
        if (match[1]) continue;
        const inner = /\{([\s\S]*)\}/.exec(match[2]);
        const names = inner ? inner[1].split(",").map((one) => one.trim()).filter((one) => one && !one.startsWith("type ")).map((one) => one.split(/\s+as\s+/)[0]) : [];
        found.push({ spec: match[3], names });
    }
    return found;
}

describe("the web's code, run where it lives", () => {
    it("TypeScript resolves @web/ and the web's own @/ to the folder Metro and vitest use", () => {
        const paths = (JSON.parse(read(mobile + "tsconfig.json")) as { compilerOptions: { paths: Record<string, string[]> } }).compilerOptions.paths;
        for (const alias of ["@web/*", "@/*"]) {
            expect(paths[alias]).toHaveLength(1);
            expect(resolve(mobile, paths[alias][0].replace(/\*$/, ""))).toBe(resolve(WEB));
        }
    });

    it.runIf(present)("each shim exports every name the app's web code takes from the module it replaces", () => {
        const shimmed = new Map(Object.entries(SHIMS).map(([web, own]) => [join(WEB, web), mobile + own]));
        const wanted = new Map<string, Set<string>>();
        const seen = new Set<string>();
        const queue: string[] = [];
        /* From the app's imports of the web, through every web file they load. */
        for (const file of sources(mobile + "src")) {
            for (const { spec } of importsOf(file)) {
                const found = spec.startsWith("@web/") ? moduleAt(join(WEB, spec.slice(5))) : null;
                if (found) queue.push(found);
            }
        }
        while (queue.length) {
            const file = queue.pop()!;
            if (seen.has(file) || shimmed.has(file)) continue;
            seen.add(file);
            for (const { spec, names } of importsOf(file)) {
                const found = spec.startsWith("@/") ? moduleAt(join(WEB, spec.slice(2))) : spec.startsWith(".") ? moduleAt(resolve(dirname(file), spec)) : null;
                if (!found) continue;
                if (shimmed.has(found)) for (const name of names) (wanted.get(found) ?? wanted.set(found, new Set()).get(found)!).add(name);
                queue.push(found);
            }
        }
        for (const [web, own] of shimmed) {
            const exported = new Set([...read(own).matchAll(/^export (?:async )?(?:const|function|interface|type|class) (\w+)/gm)].map((match) => match[1]));
            for (const name of wanted.get(web) ?? []) expect(exported.has(name), `${relative(mobile, own)} lacks ${name}, which ${relative(WEB, web)} gives the app's web code`).toBe(true);
        }
    });

    it.runIf(present)("src/ holds no copy of a web module: import it as @web/ instead", () => {
        const web = new Map<string, string>();
        for (const file of sources(WEB)) {
            const text = body(read(file));
            if (text.length > 200) web.set(text, relative(WEB, file));
        }
        const copies = sources(mobile + "src")
            .filter((file) => web.has(body(read(file))))
            .map((file) => `${relative(mobile, file)} is lemma-frontend/src/${web.get(body(read(file)))}`);
        expect(copies).toEqual([]);
    });
});
