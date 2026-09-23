import test from "node:test";
import assert from "node:assert/strict";
import {
    capabilities, capabilityFor, capabilityList, colleaguesFrom, firstSentence,
    grantedToolsets, POD_DEFAULT_TOOLSETS, toolsetWord,
} from "../src/stage/colleagues.ts";

test("the agent you are already talking to is not listed among its own subordinates", () => {
    // The whole page is about the default agent. Listing it here is the page
    // introducing itself twice.
    const found = colleaguesFrom({ items: [
        { name: "pod_default", description: "The one you talk to." },
        { name: "chef", description: "Plans meals." },
    ] });

    assert.deepEqual(found.map((c) => c.name), ["chef"]);
});

test("the default agent is recognised however the row spells it", () => {
    const found = colleaguesFrom({ items: [
        { name: "POD_DEFAULT" },
        { name: "something", kind: "POD_DEFAULT" },
        { name: "real" },
    ] });

    assert.deepEqual(found.map((c) => c.name), ["real"]);
});

test("a row name becomes a readable label", () => {
    const [one] = colleaguesFrom({ items: [{ name: "customer-support_bot" }] });

    assert.equal(one.name, "customer-support_bot", "the identifier is kept as it is");
    assert.equal(one.label, "Customer support bot");
});

test("a card gets one sentence, not a paragraph cut in half", () => {
    // Cutting mid-sentence reads as a bug; cutting at the full stop reads as a
    // summary, which is what a first sentence usually is.
    assert.equal(
        firstSentence("Plans meals for the week. Then it orders the shopping, checks the budget, and files a receipt."),
        "Plans meals for the week.",
    );
    assert.equal(firstSentence("No full stop here"), "No full stop here");
    assert.equal(firstSentence("   "), "");
    assert.equal(firstSentence("Line one\n\nline two"), "Line one line two", "whitespace collapses to one line");
});

test("a sentence longer than a card still fits on a card", () => {
    const long = "It " + "does a great many things ".repeat(20) + "and then stops.";

    const said = firstSentence(long);

    assert.ok(said.length <= 120);
    assert.ok(said.endsWith("…"));
});

test("an agent with nothing written about it still gets a card", () => {
    const [one] = colleaguesFrom({ items: [{ name: "quiet" }] });

    assert.equal(one.label, "Quiet");
    assert.equal(one.blurb, "");
});

test("instructions stand in when there is no description", () => {
    const [one] = colleaguesFrom({ items: [{ name: "x", instructions: "Watches the queue. Escalates anything old." }] });

    assert.equal(one.blurb, "Watches the queue.");
});

test("a list that is not one produces no cards", () => {
    assert.deepEqual(colleaguesFrom(null), []);
    assert.deepEqual(colleaguesFrom({ items: [] }), []);
});

/* ── what an agent may reach for ─────────────────────────────────────── */

test("the pod's own responder reports no toolsets, and is not believed", () => {
    // `pod_default` comes back with `"toolsets": []` because its set is
    // assigned at run time — `POD_DEFAULT_AGENT_TOOLSETS` in the backend's
    // `agent/tools/registry.py`. Reading that literally is how this app told
    // people their teammate could "talk, and that is all" about an agent with
    // a computer, a browser, the pod's files and tables and web search.
    assert.deepEqual(grantedToolsets([], true), [...POD_DEFAULT_TOOLSETS]);
    assert.equal(grantedToolsets([], true).length, 12);
});

test("an agent somebody made with no toolsets genuinely has none", () => {
    // The same empty array means the opposite thing here, which is why the
    // caller has to say which agent it is asking about: "User-created agents
    // get EXACTLY the toolsets they were created with — no implicit defaults
    // are added."
    assert.deepEqual(grantedToolsets([], false), []);
    assert.deepEqual(grantedToolsets(null, false), []);
    assert.deepEqual(grantedToolsets(undefined, true), [...POD_DEFAULT_TOOLSETS]);
});

test("a list that is actually there is taken as it comes, default or not", () => {
    assert.deepEqual(grantedToolsets(["browser", " POD "], true), ["BROWSER", "POD"]);
    assert.deepEqual(grantedToolsets(["POD", "POD"], false), ["POD"], "listed twice is granted once");
    assert.deepEqual(grantedToolsets([null, 3, "POD"], false), ["POD"], "a payload is never trusted");
});

test("a capability is one or two words, with the sentence kept for a title", () => {
    // Eleven full clauses was a paragraph in pill form. The icon does the work
    // and the word disambiguates it; `says` is there for whoever wants it.
    const shell = capabilityFor("WORKSPACE_CLI");
    assert.equal(shell.word, "Computer");
    assert.equal(shell.says, "Works a computer");
    assert.equal(shell.icon, "shell");
    assert.ok(shell.word.split(" ").length <= 2, "a chip is not a sentence");

    for (const code of POD_DEFAULT_TOOLSETS) {
        assert.ok(capabilityFor(code).word.length <= 16, code + " is too long for a chip");
    }
});

test("memory is named as a capability and never as a tool", () => {
    // The backend is explicit: it "contributes no tools… it is in this list so
    // Lem is taught the memory contract", and the reading and writing happen
    // through WORKSPACE_CLI and POD.
    assert.equal(capabilityFor("MEMORY").tools, false);
    assert.equal(capabilityFor("MEMORY").says, "Remembers across conversations");
    assert.equal(capabilityFor("WORKSPACE_CLI").tools, true);
});

test("the ones every teammate has are marked, and the telling ones are not", () => {
    for (const code of ["USER_INTERACTION", "TODO", "SNOOZE"]) {
        assert.equal(capabilityFor(code).plain, true, code + " says nothing about this teammate");
    }
    for (const code of ["WORKSPACE_CLI", "BROWSER", "POD", "MEMORY", "SPEECH"]) {
        assert.equal(capabilityFor(code).plain, false);
    }
});

test("a toolset this build has never seen is still drawn, not dropped", () => {
    const unknown = capabilityFor("TIME_TRAVEL");

    assert.equal(unknown.word, "Time travel");
    assert.equal(unknown.icon, "other");
    assert.deepEqual(capabilities(["TIME_TRAVEL"]), ["Time travel"]);
});

test("the agent rows still get their words, from the same table", () => {
    // `capabilities()` is what every agent row renders. It is `capabilityList`
    // with the words taken off, so shortening a word shortens both.
    assert.equal(toolsetWord("web_search"), "Web");
    assert.deepEqual(
        capabilities(["MEMORY", "WORKSPACE_CLI", "BROWSER"]),
        ["Computer", "Browser", "Memory"],
        "the ones that tell you what it is for come first",
    );
    assert.deepEqual(capabilityList(["BROWSER"]).map((one) => one.word), capabilities(["BROWSER"]));
});
