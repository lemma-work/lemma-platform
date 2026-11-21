import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

registerHooks({ load(url, context, nextLoad) {
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });
const { StartScreen, StartPage } = await import("../src/outcomes/start-screen.tsx");
const { PageBoundary } = await import("../src/outcomes/page-boundary.tsx");

/** Starting up is one screen (design.md, "States"): the frame's shape, and
 *  words only once it has taken a while. */

test("starting up draws the frame's shape and says nothing at first", () => {
    const html = renderToStaticMarkup(createElement(StartScreen, { words: "Checking you’re signed in" }));
    assert.match(html, /role="status"/);
    assert.match(html, /aria-label="Checking you’re signed in…"/);
    assert.match(html, /kboot__side/);
    assert.match(html, /kboot__logo/);
    assert.doesNotMatch(html, /kboot__note/);
    assert.doesNotMatch(html, /Loading|Opening Lemma/);
});

test("a page loading inside the frame has no sidebar of its own", () => {
    const html = renderToStaticMarkup(createElement(StartPage, { words: "Opening Fjord" }));
    assert.match(html, /aria-label="Opening Fjord…"/);
    assert.doesNotMatch(html, /kboot__side/);
});

test("Lemma not answering at start counts down to its next look and offers one now", () => {
    const html = renderToStaticMarkup(createElement(StartScreen, { down: { seconds: 4, onTryNow: () => undefined } }));
    assert.match(html, /Can’t reach Lemma right now/);
    assert.match(html, /Trying again in <span class="kboot__fig">4<\/span> s\./);
    assert.match(html, /Try now/);
    assert.match(html, /kboot__side/);
    /* Not a status region: the countdown is not read out every second. */
    assert.doesNotMatch(html, /role="status"/);
    const looking = renderToStaticMarkup(createElement(StartScreen, { down: { seconds: null, onTryNow: () => undefined } }));
    assert.match(looking, /Trying again now…/);
});

test("a page that broke draws again at another address, and not at its own", () => {
    const derive = PageBoundary.getDerivedStateFromProps as (props: { at: string }, state: { error: unknown; failedAt: string | null }) => object | null;
    const thrown = PageBoundary.getDerivedStateFromError(new Error("boom"));
    assert.equal(thrown.failedAt, null);
    const noted = derive({ at: "/apps" }, thrown);
    assert.deepEqual(noted, { failedAt: "/apps" });
    assert.equal(derive({ at: "/apps" }, { ...thrown, failedAt: "/apps" }), null);
    assert.deepEqual(derive({ at: "/needs" }, { ...thrown, failedAt: "/apps" }), { error: null, failedAt: null });
    assert.equal(derive({ at: "/apps" }, { error: null, failedAt: null }), null);
    /* Something thrown that is not an error still counts as broken. */
    assert.notEqual(PageBoundary.getDerivedStateFromError(null).error, null);
});
