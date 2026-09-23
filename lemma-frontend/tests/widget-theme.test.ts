import { strict as assert } from "node:assert";
import test from "node:test";
import { APP_THEME_MESSAGE_TYPE, WIDGET_THEME_MESSAGE_TYPE, buildThemeMessage } from "@/thread/widget-theme";

const PALETTE: Record<string, string> = {
    "--canvas": "#fbfbfd", "--paper": "#ffffff", "--chrome": "#f3f2f7", "--wash-b": "#f8f6fe",
    "--ink": "#16151b", "--ink-2": "#56535f", "--ink-3": "#757187",
    "--line": "rgb(22 21 27 / 0.08)", "--line-2": "rgb(22 21 27 / 0.15)",
    "--accent": "#6b4fe0", "--accent-soft": "rgb(107 79 224 / 0.09)",
    "--ok": "#2f7d57", "--wait": "#b5811f", "--bad": "#b3261e", "--info": "#2563a8",
    "--r-sm": "7px", "--r-md": "10px", "--r-lg": "14px", "--r-xl": "20px",
    "--ease-out": "cubic-bezier(.2,.8,.2,1)",
};

const widget = () => buildThemeMessage({
    prefix: "lemma-widget", type: WIDGET_THEME_MESSAGE_TYPE, theme: "light",
    readToken: (name) => PALETTE[name] ?? "", fontFamily: "Schibsted Grotesk, sans-serif",
});

/* The first pass sent `lemma-app-theme` with tokens named after this app's own
   variables. Both are real, and both were wrong for a widget, and nothing threw
   — the widget just kept its defaults. These are the two facts that failed. */
test("a widget is addressed by the widget message type", () => {
    assert.equal(widget().type, "lemma-widget-theme");
    assert.notEqual(widget().type, APP_THEME_MESSAGE_TYPE);
});

test("every token carries the widget prefix and none carry this app's own names", () => {
    const names = Object.keys(widget().tokens);
    assert.ok(names.length > 20, "expected the full vocabulary, got " + names.length);
    for (const name of names) assert.ok(name.startsWith("--lemma-widget-"), name + " is not a widget token");
    for (const own of ["--lemma-widget-ink", "--lemma-widget-paper", "--lemma-widget-canvas", "--lemma-widget-r-lg"]) {
        assert.ok(!names.includes(own), own + " is this app's own name, not the shared vocabulary");
    }
});

test("the vocabulary is semantic, and says what a widget asks for", () => {
    const { tokens } = widget();
    assert.equal(tokens["--lemma-widget-bg"], "#fbfbfd");
    assert.equal(tokens["--lemma-widget-surface"], "#ffffff");
    assert.equal(tokens["--lemma-widget-text"], "#16151b");
    assert.equal(tokens["--lemma-widget-muted"], "#56535f");
    assert.equal(tokens["--lemma-widget-border"], "rgb(22 21 27 / 0.08)");
    assert.equal(tokens["--lemma-widget-danger"], "#b3261e");
});

/* A widget's main corner is `radius`; an app's is `radius-lg`. It is the one
   name the two vocabularies disagree on. */
test("the main corner is named for the surface it is sent to", () => {
    assert.equal(widget().tokens["--lemma-widget-radius"], "14px");
    assert.equal(widget().tokens["--lemma-widget-radius-lg"], undefined);
    const app = buildThemeMessage({
        prefix: "lemma-app", type: APP_THEME_MESSAGE_TYPE, theme: "light",
        readToken: (name) => PALETTE[name] ?? "", fontFamily: "x",
    });
    assert.equal(app.tokens["--lemma-app-radius-lg"], "14px");
    assert.equal(app.tokens["--lemma-app-radius"], undefined);
});

test("a token with no answer is left out rather than guessed at", () => {
    const sparse = buildThemeMessage({
        prefix: "lemma-widget", type: WIDGET_THEME_MESSAGE_TYPE, theme: "dark",
        readToken: () => "", fontFamily: "",
    });
    assert.equal(sparse.tokens["--lemma-widget-bg"], undefined);
    /* Except the two it computes rather than reads. */
    assert.equal(sparse.tokens["--lemma-widget-color-scheme"], "dark");
    assert.equal(sparse.tokens["--lemma-widget-danger-soft"], "#331919");
});
