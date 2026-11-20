import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";
import {
    BUILT,
    CATALOG_SIZE,
    DAY_SECTIONS,
    getPlugin,
    LINES,
    PLANNED,
    TABLES,
    tiesOf,
    type BuiltPlugin,
} from "../src/site/plugins/catalog.ts";
import { faceOf, FACES, pickedFor } from "../src/site/plugins/faces.ts";
import { stackOf } from "../src/site/plugins/stack.ts";

/* The catalog is generated from the plugins (awesome-lemma/_build/
   site_catalog.py). These hold the generated file to what the pages assume,
   so a stale or hand-edited one fails here rather than on the site. */

const PUBLIC = path.resolve(import.meta.dirname, "../public");
const slugs = new Set([...BUILT.map((p) => p.slug), ...PLANNED.map((p) => p.slug)]);

test("every slug is one plugin, built or planned", () => {
    assert.equal(slugs.size, BUILT.length + PLANNED.length);
    assert.equal(CATALOG_SIZE, slugs.size);
});

test("every built plugin has pictures, and every picture exists in light and dark", () => {
    for (const p of BUILT) {
        assert.ok(p.pictures.length > 0, `${p.slug} has no pictures`);
        for (const picture of p.pictures) {
            for (const src of [picture.light, picture.dark]) {
                assert.match(src, new RegExp(`^/plugins/${p.slug}/`), `${p.slug}: ${src}`);
                assert.ok(existsSync(path.join(PUBLIC, src)), `${p.slug}: ${src} is missing from public/`);
            }
            assert.ok(picture.caption.length < 56, `${p.slug}: caption too long: ${picture.caption}`);
        }
    }
});

test("a demo, when there is one, is in public/, with every app its bar switches to", () => {
    for (const p of BUILT) {
        if (!p.demo) continue;
        assert.ok(existsSync(path.join(PUBLIC, p.demo)), `${p.slug}: ${p.demo}`);
        for (const view of p.demoViews) {
            /* A folder, served as its index.html by next.config.ts's rewrites. */
            const folder = view.src.replace(/[?#].*$/, "");
            assert.match(folder, new RegExp(`^/plugins/${p.slug}/demo/(?:[^/]+/)?$`), `${p.slug}: ${view.src}`);
            assert.ok(existsSync(path.join(PUBLIC, folder, "index.html")), `${p.slug}: ${view.src} has no index.html`);
        }
    }
});

test("every plugin a plugin carries or builds on is a real plugin", () => {
    for (const p of BUILT) {
        for (const slug of [...p.carries, ...p.carriesDirect]) {
            assert.ok(BUILT.some((b) => b.slug === slug), `${p.slug} carries "${slug}", which is not built`);
        }
        for (const slug of p.carriesDirect) assert.ok(p.carries.includes(slug), `${p.slug}: ${slug} missing from carries`);
        assert.ok(!p.carries.includes(p.slug), `${p.slug} carries itself`);
    }
    for (const p of PLANNED) {
        for (const slug of p.buildsOn) assert.ok(getPlugin(slug), `${p.slug} builds on "${slug}", which is not in the catalog`);
    }
});

test("every planned plugin has a line and a place in the build order", () => {
    const lines = new Set(LINES.map((l) => l.id));
    for (const p of [...BUILT, ...PLANNED]) assert.ok(lines.has(p.line), `${p.slug}: line "${p.line}"`);
    const positions = [...BUILT, ...PLANNED].map((p) => p.position).sort((a, b) => a - b);
    assert.deepEqual(positions, Array.from({ length: CATALOG_SIZE }, (_, i) => i + 1));
    for (const p of PLANNED) assert.ok(p.one.length > 0, `${p.slug} has no line of its own`);
});

test("every shared table a plugin names is one the site has words for, and has a maker", () => {
    for (const p of BUILT) {
        for (const t of [...p.makes, ...p.reads]) assert.ok(TABLES[t], `${p.slug} names table "${t}"`);
        for (const t of p.reads) assert.ok(BUILT.some((b) => b.makes.includes(t)), `nobody makes "${t}"`);
    }
});

test("what a plugin adds to Today is one of Today's sections", () => {
    const verbs = new Set(DAY_SECTIONS.map((d) => d.verb));
    for (const p of BUILT) for (const item of p.today) assert.ok(verbs.has(item.verb), `${p.slug}: ${item.verb}`);
});

/** Every word on a plugin's landing page, its pictures' descriptions included. */
function landingCopy(p: BuiltPlugin): string[] {
    const l = p.landing;
    if (!l) return [];
    const visuals = [l.hero.visual, ...l.steps.map((x) => x.visual), ...l.moments.map((x) => x.visual)];
    return [
        l.hero.eyebrow,
        l.hero.headline,
        l.hero.sub,
        l.closing,
        ...l.steps.flatMap((x) => [x.title, x.body ?? ""]),
        ...l.moments.flatMap((x) => [x.title, x.body]),
        ...l.betterWith.map((x) => x.line),
        ...l.trust.flatMap((x) => [x.title, x.body]),
        ...l.faq.flatMap((x) => [x.q, x.a]),
        ...visuals.map((v) => v.alt),
    ];
}

test("a landing page's pictures are all in public/, in light and dark, and loops have a still and both videos", () => {
    for (const p of BUILT) {
        const l = p.landing;
        if (!l) continue;
        const visuals = [l.hero.visual, ...l.steps.map((x) => x.visual), ...l.moments.map((x) => x.visual)];
        for (const visual of visuals) {
            assert.ok(["still", "loop"].includes(visual.kind), `${p.slug}: ${visual.kind}`);
            for (const size of [visual, ...(visual.phone ? [visual.phone] : [])]) {
                assert.ok(size.width > 0 && size.height > 0, `${p.slug}: ${visual.alt} has no size`);
                for (const files of [size.light, size.dark]) {
                    const wanted = visual.kind === "loop" ? [files.still, files.mp4, files.webm] : [files.still];
                    for (const src of wanted) {
                        assert.ok(src, `${p.slug}: ${visual.alt} is missing a file`);
                        assert.match(src!, new RegExp(`^/plugins/${p.slug}/moments/`), `${p.slug}: ${src}`);
                        assert.ok(existsSync(path.join(PUBLIC, src!)), `${p.slug}: ${src} is missing from public/`);
                    }
                }
            }
        }
    }
});

test("a landing page's \"better with\" names other plugins in the catalog", () => {
    for (const p of BUILT) {
        for (const item of p.landing?.betterWith ?? []) {
            assert.ok(slugs.has(item.slug), `${p.slug}: better with "${item.slug}", which is not a plugin`);
            assert.notEqual(item.slug, p.slug, `${p.slug} is better with itself`);
        }
    }
});

test("visible copy never says pod, teammate or r-o-o-m, or names a sample", () => {
    const words = /\b(pods?|teammates?)\b|r[o]om/i;
    const names = /\b(Kit|June|Remy|Scout)\b/;
    const copy = [
        ...BUILT.flatMap((p) => [
            p.name,
            p.job,
            p.intro,
            ...p.does,
            ...p.setup,
            ...p.today.map((i) => i.line),
            ...p.accounts.map((a) => a.why),
            ...p.pictures.map((x) => x.caption),
            p.memory ?? "",
            p.memoryPersonal ?? "",
            ...landingCopy(p),
        ]),
        ...PLANNED.flatMap((p) => [p.name, p.one, ...p.merges]),
        ...LINES.map((l) => l.name),
    ];
    for (const line of copy) {
        assert.doesNotMatch(line, words, line);
        assert.doesNotMatch(line, names, line);
    }
});

test("every built plugin wears its own face, and no face repeats within a line", () => {
    assert.deepEqual(pickedFor(), BUILT.map((p) => p.slug));
    const built = new Set(BUILT.map((p) => faceOf(p.slug)));
    assert.equal(built.size, BUILT.length);
    for (const line of LINES) {
        const faces = [...BUILT, ...PLANNED].filter((p) => p.line === line.id).map((p) => faceOf(p.slug));
        assert.equal(new Set(faces).size, faces.length, `${line.id} repeats a face`);
    }
    for (const p of PLANNED) {
        assert.ok(FACES.includes(faceOf(p.slug)));
        assert.ok(!built.has(faceOf(p.slug)), `${p.slug} wears a built plugin's face`);
    }
});

test("the stack puts every built plugin in it once, sources at the bottom", () => {
    const stack = stackOf();
    const placed = [...stack.main, ...stack.beside].flatMap((row) => row.nodes.map((n) => n.slug));
    assert.deepEqual([...placed].sort(), BUILT.map((p) => p.slug).sort());
    const ground = stack.main[stack.main.length - 1].nodes.map((n) => n.slug);
    for (const slug of ground) {
        const p = BUILT.find((b) => b.slug === slug)!;
        assert.equal(p.carriesDirect.length, 0, `${slug} is a source but carries something`);
        assert.ok(BUILT.some((b) => b.carriesDirect.includes(slug)), `${slug} is a source nobody carries`);
    }
    for (const edge of stack.edges) {
        const row = (slug: string) => [...stack.main, ...stack.beside].findIndex((r) => r.nodes.some((n) => n.slug === slug));
        assert.ok(row(edge.from) < row(edge.to), `${edge.from} is not above ${edge.to}`);
    }
});

test("shared tables tie two plugins both ways; carrying ties only the one carried", () => {
    for (const p of BUILT) {
        for (const tie of tiesOf(p)) {
            const back = tiesOf(tie.plugin).find((t) => t.plugin.slug === p.slug);
            if (tie.uses.length || tie.usedFor.length) {
                assert.ok(back, `${p.slug} shares tables with ${tie.plugin.slug} but not back`);
                assert.deepEqual(back.uses, tie.usedFor);
            }
            if (tie.buildsOnThis) assert.ok(tie.plugin.carriesDirect.includes(p.slug));
        }
    }
});
