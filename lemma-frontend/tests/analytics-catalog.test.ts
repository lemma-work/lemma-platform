import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { CLIENT_CATALOG, KNOWN_UNEMITTED } from "../src/site/analytics/catalog.ts";

/** The catalog held to the code, on both sides.
 *
 *  A declared event nothing raises is a dashboard that reads zero for ever.
 *  That is how every client event went quiet: the frontend was rebuilt, the
 *  catalog came across and its call sites did not, and the backend's own
 *  test went on passing because it took the client's word for what the client
 *  sends. */

const SRC = path.resolve(import.meta.dirname, "../src");
const BACKEND = path.resolve(import.meta.dirname, "../../lemma-backend/app/core");

function sourceFiles(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const at = path.join(dir, entry.name);
        if (entry.isDirectory()) return sourceFiles(at);
        return /\.(ts|tsx)$/.test(entry.name) ? [at] : [];
    });
}

/** Event name → the files that raise it, read off `captureEvent("…"` calls
 *  outside the analytics module itself. */
function callSites(): Map<string, string[]> {
    const sites = new Map<string, string[]>();
    for (const file of sourceFiles(SRC)) {
        if (file.includes(path.join("site", "analytics") + path.sep)) continue;
        for (const match of readFileSync(file, "utf8").matchAll(/captureEvent\(\s*"([^"]+)"/g))
            sites.set(match[1], [...(sites.get(match[1]) ?? []), path.relative(SRC, file)]);
    }
    return sites;
}

const sites = callSites();
const emitted = new Set(Object.keys(CLIENT_CATALOG).filter((name) => sites.has(name)));

test("every catalogued event is raised somewhere, or is named as not yet raised", () => {
    const silent = Object.keys(CLIENT_CATALOG).filter((name) => !sites.has(name) && !KNOWN_UNEMITTED.includes(name as never));
    assert.deepEqual(silent, [], "catalogued with no call site and not in KNOWN_UNEMITTED");
    const stale = KNOWN_UNEMITTED.filter((name) => sites.has(name));
    assert.deepEqual(stale, [], "in KNOWN_UNEMITTED but raised: take it off the list");
});

test("nothing raises an event the catalog does not declare", () => {
    /* `captureEvent` is typed against the catalog, so this is a backstop for a
       cast; the `before_send` gate would drop it anyway, silently. */
    const unknown = [...sites.keys()].filter((name) => !(name in CLIENT_CATALOG));
    assert.deepEqual(unknown, []);
});

/** `"name": AnalyticEvent(properties=frozenset({…})` in the backend catalog. */
function backendProperties(name: string): string[] | null {
    const catalog = readFileSync(path.join(BACKEND, "analytics/event_catalog.py"), "utf8");
    const escaped = name.replace(/\./g, "\\.");
    const match = catalog.match(new RegExp(`"${escaped}":\\s*AnalyticEvent\\(\\s*properties=frozenset\\(\\{([^}]*)\\}\\)`));
    return match ? [...match[1].matchAll(/"([^"]+)"/g)].map((key) => key[1]).sort() : null;
}

test("what the backend counts on the client for, the client sends, under the backend's keys", () => {
    const wiring = readFileSync(path.join(BACKEND, "tests/unit/test_analytics_wiring.py"), "utf8");
    const declared = wiring.match(/CLIENT_EMITTED\s*=\s*frozenset\(\{([^}]*)\}\)/);
    assert.ok(declared, "CLIENT_EMITTED not found in test_analytics_wiring.py");
    const expected = [...declared[1].matchAll(/"([^"]+)"/g)].map((name) => name[1]);
    assert.ok(expected.length > 0);
    for (const name of expected) {
        assert.ok(emitted.has(name), `${name} is CLIENT_EMITTED on the backend, but nothing here raises it`);
        /* A shared name with divergent shapes is worse than two names: it looks
           right in a dashboard. */
        assert.deepEqual(
            [...(CLIENT_CATALOG[name as keyof typeof CLIENT_CATALOG]?.properties ?? [])].sort(),
            backendProperties(name),
            `${name} declares different keys on the two sides`,
        );
    }
});
