import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import ts from "typescript";

registerHooks({
    load(url, context, nextLoad) {
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});

/** What somebody with no organization meets first. A Lemma's link used to be
 *  dropped for a new organization and the shelf; it is their way in now. */

const { arrivalStep } = await import("../src/outcomes/arrival.tsx");

test("nothing is decided before both reads are back", () => {
    assert.equal(arrivalStep({ invitations: null, suggested: 0, linkedPodId: "p1", ownInstead: false }), "wait");
    assert.equal(arrivalStep({ invitations: 0, suggested: null, linkedPodId: null, ownInstead: false }), "wait");
});

test("a Lemma's link, with no invitation, goes to that Lemma's door", () => {
    assert.equal(arrivalStep({ invitations: 0, suggested: 0, linkedPodId: "p1", ownInstead: false }), "knock");
    // Ahead of their company's organization, too: they came for the link.
    assert.equal(arrivalStep({ invitations: 0, suggested: 2, linkedPodId: "p1", ownInstead: false }), "knock");
});

test("invitations are accepted first, link or not", () => {
    assert.equal(arrivalStep({ invitations: 1, suggested: 0, linkedPodId: "p1", ownInstead: false }), "begin");
    assert.equal(arrivalStep({ invitations: 1, suggested: 3, linkedPodId: null, ownInstead: false }), "begin");
});

test("turning the door down for a place of their own goes the usual way", () => {
    assert.equal(arrivalStep({ invitations: 0, suggested: 0, linkedPodId: "p1", ownInstead: true }), "begin");
    assert.equal(arrivalStep({ invitations: 0, suggested: 1, linkedPodId: "p1", ownInstead: true }), "choose");
});

test("with no link, the arrival is what it always was", () => {
    assert.equal(arrivalStep({ invitations: 0, suggested: 0, linkedPodId: null, ownInstead: false }), "begin");
    assert.equal(arrivalStep({ invitations: 0, suggested: 1, linkedPodId: null, ownInstead: false }), "choose");
});
