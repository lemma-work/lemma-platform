import assert from "node:assert/strict";
import { before, test } from "node:test";
import { JSDOM } from "jsdom";

import type { CaptureResult, PostHog } from "posthog-js";

/** The export boundary, held against the real SDK.
 *
 *  `/privacy` says a page address leaves this browser as its route pattern and
 *  that the names of things people make reach analytics only as identifiers.
 *  posthog-js decides most of what an event carries, and it attaches the
 *  address under keys of its own choosing: `$session_entry_url` on every event,
 *  `$prev_pageview_pathname` and `title` on pageviews, `$set_once` beside the
 *  properties rather than inside them. A test against a hand-written event only
 *  proves the keys somebody thought of. This one starts the app's own client
 *  (`startAnalytics`) on a page whose every address, query and title is
 *  private, walks it through a visit, and reads each event as posthog-js hands
 *  it on after our `before_send` chain — the last step before the network.
 *
 *  Nothing is sent: `fetch` and `XMLHttpRequest` are stubbed. */

const POD_IN_ADDRESS = "pod-in-the-address";
const ENTRY =
    "https://lemma.work/s/contact/ada-lovelace?n=Ada%20Lovelace&em=ada%40example.test&wa=%2B15551234567";
const REFERRER = "https://mail.example.test/inbox/thread-42?q=quarterly+layoffs";

/** Every string that must never leave. The referrer's host is not here: another
 *  site's origin is where somebody came from, and is kept on purpose. */
const PRIVATE = [
    "ada", "Ada", "15551234567", "example.test/inbox", "thread-42", "quarterly",
    POD_IN_ADDRESS, "q3-layoffs", "private", "Payroll export",
];

const sent: CaptureResult[] = [];
let dom: JSDOM;
let posthog: PostHog;
let client: typeof import("../src/site/analytics/client.ts");

/** Let the client's `startAnalytics().then(…)` callbacks run. */
const settle = () => new Promise((resolve) => setImmediate(resolve));

before(async () => {
    dom = new JSDOM("<!doctype html><title>Ada Lovelace | Lemma</title>", {
        url: ENTRY,
        referrer: REFERRER,
        pretendToBeVisual: true,
    });
    const offline = () => Promise.reject(new Error("offline in tests"));
    class NoRequest {
        open() {}
        send() {}
        setRequestHeader() {}
        addEventListener() {}
    }
    Object.assign(dom.window, { fetch: offline, XMLHttpRequest: NoRequest });
    (dom.window as unknown as { __LEMMA_SITE__: object }).__LEMMA_SITE__ = {
        analyticsKey: "phc_test_not_a_key",
        deployment: "hosted",
    };
    for (const [key, value] of Object.entries({
        window: dom.window,
        document: dom.window.document,
        navigator: dom.window.navigator,
        location: dom.window.location,
        localStorage: dom.window.localStorage,
        sessionStorage: dom.window.sessionStorage,
        fetch: offline,
        XMLHttpRequest: NoRequest,
    })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    /* posthog-js schedules its flushes and session checks on the global timers
       and never stops them, so this file's process would never exit. Every
       timer is unreferenced instead: it still fires while the tests run, and
       holds nothing open once they are done. */
    for (const name of ["setTimeout", "setInterval"] as const) {
        const original = globalThis[name];
        const unreferenced = (...args: Parameters<typeof original>) => {
            const handle = original(...args);
            handle.unref();
            return handle;
        };
        Object.defineProperty(globalThis, name, { value: unreferenced, configurable: true, writable: true });
    }

    /* posthog-js reads `window` once, when it is first imported, so nothing
       imports it until there is one. */
    client = await import("../src/site/analytics/client.ts");
    await client.startAnalytics();
    ({ posthog } = await import("posthog-js"));
    posthog.on("eventCaptured", (event) => sent.push(structuredClone(event)));

    client.capturePageview();
    dom.window.history.pushState(null, "", `/t/${POD_IN_ADDRESS}/file/private/q3-layoffs.md?view=raw`);
    dom.window.document.title = "q3-layoffs | Lemma";
    client.capturePageview();
    client.setAnalyticsIdentity({ userId: "user-1", organizationId: "org-1", podId: "pod-1" });
    client.captureEvent("share_link.viewed", { kind: "contact", viewer_is_member: false });
    /* What a careless call site could do, past the types: the gate is the
       only thing between these and the network. */
    posthog.capture("share_link.viewed", { kind: "contact", email: "ada@example.test" });
    posthog.capture("not.in.the.catalog", { kind: "contact" });
    await settle();

    const thrown = new TypeError(`Cannot read "Payroll export" of undefined`);
    thrown.stack = [
        `TypeError: Cannot read "Payroll export" of undefined`,
        "    at render (https://lemma.work/_next/static/chunks/app-1.js:1:200)",
        `    at inline (https://lemma.work/t/${POD_IN_ADDRESS}/file/private/q3-layoffs.md:3:10)`,
    ].join("\n");
    client.captureClientError(thrown);
    dom.window.dispatchEvent(new dom.window.ErrorEvent("error", { error: new Error("Payroll export failed"), message: "Payroll export failed" }));
    posthog.capture("$pageleave");
    await settle();
});

const of = (name: string) => sent.filter((event) => event.event === name);

test("the SDK was really driven: every kind of event came out of the chain", () => {
    const kinds = new Set(sent.map((event) => event.event));
    for (const kind of ["$pageview", "$identify", "$groupidentify", "share_link.viewed", "$exception", "$pageleave"])
        assert.ok(kinds.has(kind), `${kind} never reached the end of before_send`);
});

test("no address, query, title, name or error message leaves through any property", () => {
    for (const event of sent) {
        const payload = JSON.stringify(event);
        for (const secret of PRIVATE)
            assert.ok(!payload.includes(secret), `${event.event} carried "${secret}": ${payload}`);
        assert.equal(event.properties.title, undefined, `${event.event} carried the page title`);
    }
});

test("what is kept is still worth having: route templates, places, and where people came from", () => {
    const [entry, file] = of("$pageview");
    assert.equal(entry.properties.$current_url, "https://lemma.work/s/[kind]/[...path]");
    assert.equal(entry.properties.$session_entry_url, "https://lemma.work/s/[kind]/[...path]");
    assert.equal(entry.properties.$referrer, "https://mail.example.test");
    assert.equal(entry.properties.$referring_domain, "mail.example.test");
    assert.equal(file.properties.$pathname, "/t/[pod]/file/[...path]");
    assert.equal(file.properties.$prev_pageview_pathname, "/s/[kind]/[...path]");

    const [identify] = of("$identify");
    assert.equal(identify.$set_once?.$initial_current_url, "https://lemma.work/s/[kind]/[...path]");
});

test("an undeclared event never leaves, and a declared one keeps only its keys and the SDK's", () => {
    assert.equal(of("not.in.the.catalog").length, 0);
    const shared = of("share_link.viewed");
    assert.equal(shared.length, 2, "a catalogued event was dropped on the way out");
    for (const event of shared) {
        assert.equal(event.properties.kind, "contact");
        assert.equal(event.properties.token, "phc_test_not_a_key");
        assert.equal(event.properties.distinct_id, "user-1");
        assert.equal("email" in event.properties, false);
    }
});

test("every event carries the deployment, posthog-js's own pageviews included", () => {
    for (const event of sent) assert.equal(event.properties.deployment, "hosted", event.event);
});

test("an error keeps its type and our code's frames, and loses its message and the page", () => {
    const [caught, uncaught] = of("$exception");
    const [entry] = caught.properties.$exception_list as { type: string; value: string; stacktrace: { frames: { filename: string }[] } }[];
    assert.equal(entry.type, "TypeError");
    assert.equal(entry.value, "TypeError");
    const files = entry.stacktrace.frames.map((frame) => frame.filename).sort();
    assert.deepEqual(files, ["https://lemma.work/_next/static/chunks/app-1.js", "https://lemma.work/t/[pod]/file/[...path]"]);
    assert.ok(uncaught, "an uncaught error on the window was not reported");
});

test("without consent the person is identified, but no anonymous id is merged into them", () => {
    const [identify] = of("$identify");
    assert.equal(identify.properties.distinct_id, "user-1");
    assert.equal("$anon_distinct_id" in identify.properties, false);
});

test("withdrawing consent clears the browser, and a signed-in person is still counted", async () => {
    client.applyAnalyticsPersistence(true);
    posthog.capture("$pageview");
    assert.ok(Object.keys(dom.window.localStorage).some((key) => key.startsWith("ph_")), "consent stored nothing");

    const before = sent.length;
    client.applyAnalyticsPersistence(false);
    assert.equal(Object.keys(dom.window.localStorage).some((key) => key.startsWith("ph_")), false, "an identifier stayed in localStorage");
    assert.equal(dom.window.document.cookie.includes("ph_"), false, "an identifier stayed in a cookie");

    client.capturePageview();
    await settle();
    const after = sent.slice(before);
    assert.ok(after.some((event) => event.event === "$identify" && event.properties.distinct_id === "user-1"), "not identified again");
    const pageview = after.find((event) => event.event === "$pageview");
    assert.equal(pageview?.properties.distinct_id, "user-1");
    assert.equal(pageview?.properties.deployment, "hosted");
});

test("a visit with no referrer says so, and an unreadable address is dropped", () => {
    const [, scrub] = client.analyticsOptions().before_send;
    const event = scrub({
        uuid: "u",
        event: "$pageview",
        properties: { $referrer: "$direct", $session_entry_url: "not a url", $pathname: "/t/x/table/payroll" },
    } as CaptureResult);
    assert.equal(event?.properties.$referrer, "$direct");
    assert.equal("$session_entry_url" in (event?.properties ?? {}), false);
    assert.equal(event?.properties.$pathname, "/t/[pod]/table/[name]");
});
