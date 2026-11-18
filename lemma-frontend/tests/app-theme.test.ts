import test from "node:test";
import assert from "node:assert/strict";
import { frameThemeKeeper, keepAppThemed, type ThemedView } from "../src/desktop/app-theme.ts";
import type { ThemeMessage } from "../src/thread/widget-theme.ts";

/** An app framed by a Lemma is told the Lemma's theme when it loads and on
 *  every change, and only at its own origin. */

function frame() {
    const told: { message: unknown; origin: string }[] = [];
    const view: ThemedView = { postMessage: (message, origin) => { told.push({ message, origin }); } };
    return { view, told };
}

function appearance() {
    const listening = new Set<() => void>();
    return {
        onChange: (callback: () => void) => {
            listening.add(callback);
            return () => { listening.delete(callback); };
        },
        flip: () => listening.forEach((run) => run()),
        get listeners() { return listening.size; },
    };
}

const theme = (mode: "light" | "dark"): ThemeMessage => ({ type: "lemma-app-theme", theme: mode, tokens: { "--lemma-app-surface": mode === "dark" ? "#212121" : "#ffffff" } });

test("the app is told the theme as soon as it has loaded", () => {
    const { view, told } = frame();
    keepAppThemed(view, "https://people.apps.example/1042", { build: () => theme("light"), onChange: appearance().onChange, base: "https://lemma.example/l/x" });
    assert.equal(told.length, 1);
    assert.deepEqual(told[0], { message: theme("light"), origin: "https://people.apps.example" });
});

test("every appearance change is told again, with the theme as it is then", () => {
    const { view, told } = frame();
    const look = appearance();
    let mode: "light" | "dark" = "light";
    keepAppThemed(view, "https://people.apps.example/", { build: () => theme(mode), onChange: look.onChange, base: "https://lemma.example/" });
    mode = "dark";
    look.flip();
    mode = "light";
    look.flip();
    assert.deepEqual(told.map((t) => (t.message as ThemeMessage).theme), ["light", "dark", "light"]);
    assert.ok(told.every((t) => t.origin === "https://people.apps.example"));
});

test("stopping ends the subscription", () => {
    const { view } = frame();
    const look = appearance();
    const stop = keepAppThemed(view, "https://people.apps.example/", { build: () => theme("light"), onChange: look.onChange, base: "https://lemma.example/" });
    stop();
    assert.equal(look.listeners, 0);
});

test("a relative or aliased src is addressed by the origin it resolves to", () => {
    const { view, told } = frame();
    keepAppThemed(view, "/apps/people/", { build: () => theme("dark"), onChange: appearance().onChange, base: "https://lemma.example/l/x" });
    assert.equal(told[0].origin, "https://lemma.example");
});

test("an address with no origin to aim at is never sent to", () => {
    const { view, told } = frame();
    const look = appearance();
    keepAppThemed(view, "data:text/html,hi", { build: () => theme("dark"), onChange: look.onChange, base: "https://lemma.example/" });
    look.flip();
    assert.equal(told.length, 0);
    assert.equal(look.listeners, 0);
});

test("a frame that has gone does not throw", () => {
    const view: ThemedView = { postMessage: () => { throw new Error("detached"); } };
    assert.doesNotThrow(() => keepAppThemed(view, "https://people.apps.example/", { build: () => theme("light"), onChange: appearance().onChange, base: "https://lemma.example/" }));
});

/* The frame's window is the same object on every load, so a reload, an app
   that navigates, or a sign-in coming back has to be caught by the load
   itself; a check for a new window would see nothing new. */

test("every load of the same frame is told again, and only one subscription is left", () => {
    const { view, told } = frame();
    const look = appearance();
    const keeper = frameThemeKeeper({ build: () => theme("dark"), onChange: look.onChange, base: "https://lemma.example/" });
    keeper.loaded(view, "https://people.apps.example/");
    keeper.loaded(view, "https://people.apps.example/");
    assert.equal(told.length, 2);
    assert.equal(look.listeners, 1);
    look.flip();
    assert.equal(told.length, 3);
});

test("a load with no window to speak to leaves nothing listening", () => {
    const { view, told } = frame();
    const look = appearance();
    const keeper = frameThemeKeeper({ build: () => theme("light"), onChange: look.onChange, base: "https://lemma.example/" });
    keeper.loaded(view, "https://people.apps.example/");
    keeper.loaded(null, "https://people.apps.example/");
    assert.equal(look.listeners, 0);
    look.flip();
    assert.equal(told.length, 1);
});

test("when the frame goes, the theme stops following it", () => {
    const { view, told } = frame();
    const look = appearance();
    const keeper = frameThemeKeeper({ build: () => theme("light"), onChange: look.onChange, base: "https://lemma.example/" });
    keeper.loaded(view, "https://people.apps.example/");
    keeper.stop();
    keeper.stop();
    look.flip();
    assert.equal(told.length, 1);
    assert.equal(look.listeners, 0);
});
