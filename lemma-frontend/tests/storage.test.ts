import test from "node:test";
import assert from "node:assert/strict";
import { sessionStorageChanged, retainWorkspaceOwner, carryStoredPreferences, key, CARRY_SCRIPT, PREFIX } from "../src/session/storage.ts";

function store(initial: Record<string, string> = {}) {
    const values = new Map(Object.entries(initial));
    return {
        values,
        getItem: (name: string) => values.get(name) ?? null,
        setItem: (name: string, value: string) => { values.set(name, value); },
        removeItem: (name: string) => { values.delete(name); },
    };
}

test("a preference kept under the old prefix survives the rename", () => {
    const browser = store({ "lemma-room:theme": "dark", "lemma-room:accent": "coral" });

    const moved = carryStoredPreferences(browser);

    assert.deepEqual(moved, ["theme", "accent"]);
    assert.equal(browser.getItem(key("theme")), "dark");
    assert.equal(browser.getItem(key("accent")), "coral");
    assert.equal(browser.getItem("lemma-room:theme"), null);
    assert.equal(browser.getItem("lemma-room:accent"), null);
});

test("a choice made since the rename is not overwritten by the one it replaced", () => {
    // The script runs on every page load, so the second run must not undo what
    // happened between them.
    const browser = store({ "lemma-room:theme": "dark", [key("theme")]: "light" });

    const moved = carryStoredPreferences(browser);

    assert.deepEqual(moved, []);
    assert.equal(browser.getItem(key("theme")), "light");
    // Still cleared: leaving it behind means carrying it forever.
    assert.equal(browser.getItem("lemma-room:theme"), null);
});

test("running it twice changes nothing the second time", () => {
    const browser = store({ "lemma-room:org": "org_1" });

    carryStoredPreferences(browser);
    const again = carryStoredPreferences(browser);

    assert.deepEqual(again, []);
    assert.equal(browser.getItem(key("org")), "org_1");
});

test("a browser that never saw the old prefix is left alone", () => {
    const browser = store({ [key("theme")]: "dark", "unrelated": "x" });

    assert.deepEqual(carryStoredPreferences(browser), []);
    assert.deepEqual([...browser.values.entries()].sort(), [["lemma-app:theme", "dark"], ["unrelated", "x"]]);
});

test("the SDK's own keys are none of this app's business", () => {
    // `lemma_token` and `lemma_api_url` are read by lemma-sdk under those exact
    // names. Carrying them onto this app's prefix would sign everybody out.
    const browser = store({ lemma_token: "tok", lemma_api_url: "https://api.example" });

    carryStoredPreferences(browser);

    assert.equal(browser.getItem("lemma_token"), "tok");
    assert.equal(browser.getItem("lemma_api_url"), "https://api.example");
});

test("the inline script carries the same keys the function does", () => {
    // It is generated from the same list, and this is what keeps that true:
    // the document runs the string, the tests exercise the function.
    const moved = carryStoredPreferences(store({
        "lemma-room:theme": "dark",
        "lemma-room:accent": "coral",
        "lemma-room:corners": "round",
        "lemma-room:org": "org_1",
        "lemma-room:tabs": "{}",
        "lemma-room:sidebar-collapsed": "true",
        "lemma-room:sidebar-hidden": "false",
        "lemma-room:header-hidden": "false",
        "lemma-room:data": "sample",
    }));

    for (const name of moved) {
        assert.ok(CARRY_SCRIPT.includes(`"${name}"`), `${name} is missing from the inline script`);
    }
    assert.ok(CARRY_SCRIPT.includes(`'${PREFIX}:'`));
    assert.ok(CARRY_SCRIPT.includes("'lemma-room:'"));
});

test("the inline script does what the function does, in a real engine", () => {
    // The string is hand-written JavaScript in a template literal, which is
    // exactly the kind of thing that is syntactically wrong for a release
    // before anybody notices. Run it against a stand-in for localStorage.
    const browser = store({ "lemma-room:theme": "dark", [key("org")]: "org_2", "lemma-room:org": "org_1" });

    new Function("localStorage", CARRY_SCRIPT)(browser);

    assert.equal(browser.getItem(key("theme")), "dark");
    assert.equal(browser.getItem("lemma-room:theme"), null);
    // The newer value wins, same as the function.
    assert.equal(browser.getItem(key("org")), "org_2");
    assert.equal(browser.getItem("lemma-room:org"), null);
});

test("the inline script is a complete statement", () => {
    // It shipped once without its semicolon. `(f)()(g)()` parses, so nothing in
    // the build said a word; at runtime it calls the first IIFE's return value,
    // throws, and takes the appearance script standing next to it down with it.
    // The symptom was a flash of the wrong theme, which is not a thing anybody
    // files a bug about. So: run it the way the document does — with another
    // statement after it — and require that one to be reached.
    const browser = store({ "lemma-room:theme": "dark" });
    let reachedTheNextScript = false;

    const document = new Function(
        "localStorage",
        "after",
        CARRY_SCRIPT + "(function(){after()})()",
    );
    document(browser, () => { reachedTheNextScript = true; });

    assert.equal(browser.getItem(key("theme")), "dark");
    assert.ok(reachedTheNextScript, "the script after this one never ran");
});

test("account switches discard workspace locations but preserve appearance", () => {
    const browser = store();
    retainWorkspaceOwner(browser, "first");
    browser.setItem(key("tabs"), "private-file-path");
    browser.setItem(key("org"), "first-org");
    browser.setItem("lemma-room:tabs", "old-private-file-path");
    browser.setItem(key("theme"), "dark");
    assert.equal(retainWorkspaceOwner(browser, "second"), true);
    assert.equal(browser.getItem(key("tabs")), null);
    assert.equal(browser.getItem(key("org")), null);
    assert.equal(browser.getItem("lemma-room:tabs"), null);
    assert.equal(browser.getItem(key("theme")), "dark");
});

test("refreshing the same user's session keeps their workspace", () => {
    const browser = store();
    retainWorkspaceOwner(browser, "person");
    browser.setItem(key("tabs"), "file-path");
    assert.equal(retainWorkspaceOwner(browser, "person"), false);
    assert.equal(browser.getItem(key("tabs")), "file-path");
});

test("sign-out and unowned legacy state discard saved locations", () => {
    for (const owner of [null, "new-person"]) {
        const browser = store();
        browser.setItem(key("tabs"), "private-file-path");
        retainWorkspaceOwner(browser, owner);
        assert.equal(browser.getItem(key("tabs")), null);
    }
});


test("other tabs reload for account or credential changes, not appearance or refresh markers", () => {
    for (const name of [key("workspace-owner"), "lemma_token", "lemma_api_url", null]) {
        assert.equal(sessionStorageChanged(name, "before", "after"), true);
    }
    for (const name of [key("theme"), key("tabs"), "sFrontToken", "sIRTFrontend"]) {
        assert.equal(sessionStorageChanged(name, "before", "after"), false);
    }
    assert.equal(sessionStorageChanged(key("workspace-owner"), "same", "same"), false);
});
