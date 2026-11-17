import test from "node:test";
import assert from "node:assert/strict";
import {
    PLUGINS,
    gapsOf,
    getPlugin,
    makersOf,
    partsOf,
    stageOf,
} from "../src/site/plugins/catalog.ts";
import { CATALOG_ORDER } from "../src/site/plugins/more.ts";
import { JOBS, SHARED_TABLES } from "../src/site/plugins/tables.ts";
import type { TableKind } from "../src/site/plugins/types.ts";
import { PUBLIC_TEMPLATES } from "../src/site/templates/catalog.ts";

test("every plugin in the catalog's order is written once, and only once", () => {
    const slugs = PLUGINS.map((p) => p.slug);
    assert.equal(new Set(slugs).size, slugs.length);
    assert.deepEqual(slugs, CATALOG_ORDER);
});

test("every plugin a plugin names exists", () => {
    for (const p of PLUGINS) {
        const named = [
            ...p.links.map((l) => l.slug),
            ...(p.needs ?? []),
            ...(p.basis ?? []).flatMap((b) => (b.plugin ? [b.plugin] : [])),
        ];
        for (const slug of named)
            assert.ok(getPlugin(slug), `${p.slug} names "${slug}"`);
    }
});

test("every template a plugin starts from installs today", () => {
    const templates = new Set(PUBLIC_TEMPLATES.map((t) => t.slug));
    for (const p of PLUGINS)
        for (const b of p.basis ?? [])
            if (b.template)
                assert.ok(templates.has(b.template), `${p.slug} → ${b.template}`);
});

test("every shared table a plugin uses has a plugin that makes it", () => {
    const kinds = Object.keys(SHARED_TABLES) as TableKind[];
    for (const p of PLUGINS)
        for (const k of [...p.makes, ...p.uses, ...(p.reads ?? [])])
            assert.ok(kinds.includes(k), `${p.slug} names table "${k}"`);
    for (const k of kinds)
        assert.ok(makersOf(k).length > 0, `nobody makes "${k}"`);
});

test("every plugin sits under a job the shelf can filter by", () => {
    const jobs = new Set(JOBS.map((j) => j.id));
    for (const p of PLUGINS) {
        assert.ok(p.jobs.length > 0, p.slug);
        for (const j of p.jobs) assert.ok(jobs.has(j), `${p.slug}: ${j}`);
    }
});

test("a plugin's stage follows from what exists", () => {
    for (const p of PLUGINS) {
        const stage = stageOf(p);
        if (p.install) assert.equal(stage, "ready", p.slug);
        else if (partsOf(p)) assert.equal(stage, "runs", p.slug);
        if (stage !== "ready") assert.ok(gapsOf(p).length > 0, `${p.slug} lists no gap`);
    }
});

test("visible copy never says pod or teammate, or names a sample", () => {
    const banned = /\b(pods?|teammates?|Kit|June)\b/;
    for (const p of PLUGINS) {
        const copy = [
            p.name,
            p.headline,
            p.lede,
            ...(p.does ?? []),
            ...(p.basis ?? []).map((b) => b.text),
            ...p.links.map((l) => l.why),
            ...p.gaps.map((g) => g.text),
        ];
        for (const line of copy) assert.doesNotMatch(line, banned, `${p.slug}: ${line}`);
    }
});
