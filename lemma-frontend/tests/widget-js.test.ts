/* widget.js, the script a website loads for the space's chat and forms.
 *
 * It is plain JavaScript served by the backend, so it is loaded here as it
 * ships — the backend's own file, run in a fresh jsdom window per test — with
 * only `fetch` answered by the test. The pure pieces come out through the hook
 * the script exposes when `__LEMMA_WIDGET_TEST__` is set; everything else is
 * driven through the page the way a visitor would. */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM, type DOMWindow } from "jsdom";

const SOURCE = readFileSync(new URL("../../lemma-backend/app/modules/agent_surfaces/public/widget.js", import.meta.url), "utf8");
const API = "https://api.example";
const BASE = "/public/web/pk_test";

type Call = { method: string; path: string; body: Record<string, unknown> | null };
type Route = (call: Call) => Response | Promise<Response>;

interface Hook {
    safeHref(url: string): string | null;
    markdown(source: string, into: HTMLElement): void;
    lineSplitter(onFrame: (frame: Record<string, unknown>) => void): (text: string) => void;
}

const json = (data: unknown, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

/** A stream body that arrives in pieces of `size` bytes, wherever they fall. */
const ndjson = (frames: unknown[], size = 7) => {
    const bytes = new TextEncoder().encode(frames.map((frame) => JSON.stringify(frame) + "\n").join(""));
    let at = 0;
    return new Response(new ReadableStream<Uint8Array>({
        pull(controller) {
            if (at >= bytes.length) return controller.close();
            controller.enqueue(bytes.slice(at, at + size));
            at += size;
        },
    }), { status: 200 });
};

/** No chat and no form: the script only loads, for its pure pieces. */
const QUIET = 'data-lemma-chat="off"';

const SESSION = { access_token: "at_1", secret: "vs_1", is_contact: false, display_name: null, title: "Kit", expires_in: 900 };

const ROUTES: Record<string, Route> = {
    "GET /challenge": () => json({ enabled: false }),
    "POST /session": () => json(SESSION),
    "GET /history": () => json({ messages: [] }),
    "POST /messages": () => json({ ok: true }, 202),
    "GET /stream": () => new Response(null, { status: 204 }),
};

/** Let the script's promises and fetches run their course. */
const settle = async (turns = 20) => {
    for (let i = 0; i < turns; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

/* Each window closes after its test: the chat's pollers would otherwise keep
   the process alive. */
const windows: DOMWindow[] = [];
test.afterEach(() => {
    for (const window of windows.splice(0)) window.close();
});

function load({ body = "", script = "", routes = {}, hook = false }: {
    body?: string;
    script?: string;
    routes?: Record<string, Route>;
    hook?: boolean;
}) {
    const dom = new JSDOM(
        `<!doctype html><html><body>${body}<script data-lemma-key="pk_test" src="${API}/public/web/widget.js" ${script}></script></body></html>`,
        { url: "https://shop.example/", runScripts: "outside-only" },
    );
    const window = dom.window as DOMWindow & Record<string, unknown>;
    windows.push(window);
    // Reduced motion: a reply is drawn as it arrives, with no frames to wait on.
    window.matchMedia = (() => ({ matches: true })) as unknown as typeof window.matchMedia;
    window.ReadableStream = ReadableStream;
    window.TextDecoder = TextDecoder;
    const calls: Call[] = [];
    const answer = { ...ROUTES, ...routes };
    window.fetch = (async (url: string, init: RequestInit = {}) => {
        const target = new URL(url);
        const call: Call = {
            method: init.method ?? "GET",
            path: target.pathname.replace(BASE, "") + target.search,
            body: typeof init.body === "string" ? JSON.parse(init.body) : null,
        };
        calls.push(call);
        const route = answer[call.method + " " + target.pathname.replace(BASE, "")];
        if (!route) throw new TypeError("no route for " + call.method + " " + call.path);
        return route(call);
    }) as unknown as typeof window.fetch;
    if (hook) window.__LEMMA_WIDGET_TEST__ = true;
    window.eval(SOURCE);
    const shadow = () => {
        const host = Array.from(window.document.querySelectorAll("div")).find((node) => node.shadowRoot);
        assert.ok(host?.shadowRoot, "the widget drew nothing");
        return host.shadowRoot;
    };
    return { window, calls, shadow, hook: () => window.__LEMMA_WIDGET_TEST__ as Hook };
}

/** Open the chat bubble, as a visitor clicking it. */
async function openChat(page: ReturnType<typeof load>) {
    await settle();
    (page.shadow().querySelector(".lw-launch") as HTMLButtonElement).click();
    await settle();
}

/** Type a message and press Enter. */
async function say(page: ReturnType<typeof load>, text: string) {
    const box = page.shadow().querySelector("textarea") as HTMLTextAreaElement;
    box.value = text;
    box.dispatchEvent(new page.window.Event("input"));
    box.dispatchEvent(new page.window.KeyboardEvent("keydown", { key: "Enter" }));
    await settle();
}

const bubbles = (page: ReturnType<typeof load>, kind: "user" | "assistant") =>
    Array.from(page.shadow().querySelectorAll(".lw-msg.lw-" + kind + ":not(.lw-typing)")).map((node) => node.textContent);

test("only a link with a web or mail scheme is a link", () => {
    const { safeHref } = load({ hook: true, script: QUIET }).hook();
    for (const bad of [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "//evil.example/x",
        "/relative",
        "vbscript:msgbox(1)",
    ]) assert.equal(safeHref(bad), null, bad);
    assert.equal(safeHref("https://ok.example/a?b=1"), "https://ok.example/a?b=1");
    assert.equal(safeHref("HTTP://ok.example"), "http://ok.example/");
    assert.equal(safeHref(" mailto:ana@ok.example "), "mailto:ana@ok.example");
});

test("markdown builds nodes and never parses what the model wrote as HTML", () => {
    const page = load({ hook: true, script: QUIET });
    const into = page.window.document.createElement("div");
    page.hook().markdown(
        "Hi <img src=x onerror=alert(1)> **there**\n\n- [bad](javascript:alert(1))\n- [good](https://ok.example)\n\n```\n<script>alert(1)</script>\n```",
        into,
    );
    assert.equal(into.querySelector("img, script"), null);
    assert.match(into.textContent ?? "", /<img src=x onerror=alert\(1\)>/);
    assert.equal(into.querySelector("strong")?.textContent, "there");
    const links = Array.from(into.querySelectorAll("a"));
    assert.deepEqual(links.map((a) => a.getAttribute("href")), ["https://ok.example/"]);
    assert.equal(links[0].rel, "noopener noreferrer nofollow");
    assert.equal(into.querySelector("pre code")?.textContent, "<script>alert(1)</script>");
});

test("a stream's lines are whole frames however its pieces fall", async () => {
    const frames: Record<string, unknown>[] = [];
    const push = load({ hook: true, script: QUIET }).hook().lineSplitter((frame) => frames.push(JSON.parse(JSON.stringify(frame))));
    push('{"type":"del');
    push('ta","text":"a"}\n\nnot json\n{"ty');
    push('pe":"done"}');
    assert.deepEqual(frames, [{ type: "delta", text: "a" }]);
    push("\n");
    assert.deepEqual(frames, [{ type: "delta", text: "a" }, { type: "done" }]);

    // And through the page: seven bytes at a time, splitting "é" in two. Said
    // once, on the stream the message opens; a live stream never replays.
    let streams = 0;
    const page = load({
        routes: {
            "GET /stream": () => (streams++ === 0 ? new Response(null, { status: 204 }) : ndjson([
                { type: "open" },
                { type: "delta", text: "Bonjour " },
                { type: "delta", text: "é <b>x</b>" },
                { type: "message", text: "Bonjour é <b>x</b>", sequence: 1 },
                { type: "done" },
            ])),
        },
    });
    await openChat(page);
    await say(page, "hello");
    assert.deepEqual(bubbles(page, "assistant").slice(1), ["Bonjour é <b>x</b>"]);
    assert.equal(page.shadow().querySelector(".lw-assistant b"), null);
});

test("a reply is painted piece by piece, built as markdown once, and only then read out", async () => {
    let feed: ReadableStreamDefaultController<Uint8Array> | null = null;
    let streams = 0;
    const page = load({
        routes: {
            "GET /stream": () => (streams++ === 0 ? new Response(null, { status: 204 }) : new Response(new ReadableStream<Uint8Array>({
                start(controller) { feed = controller; },
            }))),
        },
    });
    const send = (frame: unknown) => feed?.enqueue(new TextEncoder().encode(JSON.stringify(frame) + "\n"));
    await openChat(page);
    await say(page, "hello");
    const root = page.shadow();
    assert.equal(root.querySelector(".lw-log")?.getAttribute("aria-live"), null);
    send({ type: "open" });
    send({ type: "delta", text: "**Two** " });
    send({ type: "delta", text: "pieces" });
    await settle();
    const raw = root.querySelector(".lw-raw");
    assert.ok(raw, "the reply is drawn while it is written");
    assert.equal(raw.textContent, "**Two** pieces");
    assert.equal(raw.childNodes.length, 3, "two text nodes and the caret");
    assert.equal(root.querySelector(".lw-sr")?.textContent, "");
    send({ type: "message", text: "**Two** pieces", sequence: 1 });
    send({ type: "done" });
    await settle();
    assert.equal(root.querySelector(".lw-raw"), null);
    assert.equal(root.querySelector(".lw-assistant strong")?.textContent, "Two");
    assert.equal(root.querySelector(".lw-sr")?.textContent, "Two pieces");
    feed!.close();
});

test("the chat is a labelled dialog its button opens, and closing gives focus back", async () => {
    const page = load({});
    await openChat(page);
    const root = page.shadow();
    const launch = root.querySelector(".lw-launch") as HTMLButtonElement;
    const panel = root.querySelector(".lw-panel") as HTMLElement;
    assert.equal(panel.getAttribute("role"), "dialog");
    assert.equal(panel.getAttribute("aria-modal"), "true");
    assert.equal(launch.getAttribute("aria-expanded"), "true");
    (root.querySelector(".lw-close") as HTMLButtonElement).click();
    assert.equal(launch.getAttribute("aria-expanded"), "false");
    assert.equal(root.activeElement, launch);
});

test("a stream that won't open is retried after longer and longer waits", async () => {
    const page = load({ routes: { "GET /stream": () => Promise.reject(new TypeError("offline")) } });
    page.window.Math.random = () => 0.5;
    const waits: { delay: number; run: () => void }[] = [];
    const realTimeout = page.window.setTimeout.bind(page.window);
    page.window.setTimeout = ((run: () => void, delay = 0) => {
        if (delay < 1000) return realTimeout(run, delay);
        waits.push({ delay, run });
        return 0;
    }) as typeof page.window.setTimeout;
    await openChat(page);
    await say(page, "hello");
    for (let i = 0; i < 3 && waits[i]; i += 1) {
        waits[i].run();
        await settle();
    }
    const delays = waits.map((wait) => wait.delay);
    assert.ok(delays.length >= 2, "retried: " + JSON.stringify(delays));
    for (let i = 1; i < delays.length; i += 1) assert.ok(delays[i] > delays[i - 1], JSON.stringify(delays));
    assert.ok(delays.every((delay) => delay <= 30_000));
});

test("the visitor's own message is drawn once, matched by the name the page gave it", async () => {
    let sent: string | null = null;
    const page = load({
        routes: {
            "POST /messages": (call) => {
                sent = String(call.body?.client_nonce);
                return json({ ok: true }, 202);
            },
            "GET /stream": () => ndjson([{ type: "open" }]),
            "GET /history": () => json({
                // The same words from another tab, answered there, landed first:
                // they are another message, not the copy of this one.
                messages: sent ? [
                    { role: "user", text: "same words", sequence: 0, client_nonce: "elsewhere" },
                    { role: "assistant", text: "Answered there", sequence: 1, client_nonce: null },
                    { role: "user", text: "same words", sequence: 2, client_nonce: sent },
                ] : [],
            }),
        },
    });
    await openChat(page);
    await say(page, "same words");
    assert.ok(sent, "the message carries a client nonce");
    const log = Array.from(page.shadow().querySelectorAll(".lw-msg:not(.lw-typing)")).slice(1);
    assert.deepEqual(log.map((node) => [node.classList.contains("lw-user") ? "user" : "assistant", node.textContent]), [
        ["user", "same words"],
        ["user", "same words"],
        ["assistant", "Answered there"],
    ]);
});

test("a message the server refuses comes off the log, with the server's reason", async () => {
    const page = load({
        routes: { "POST /messages": () => json({ message: "Keep it under 4000 characters", code: "too_long" }, 422) },
    });
    await openChat(page);
    const box = page.shadow().querySelector("textarea") as HTMLTextAreaElement;
    assert.equal(box.maxLength, 4000);
    await say(page, "too much");
    assert.deepEqual(bubbles(page, "user"), []);
    assert.equal(page.shadow().querySelector(".lw-note")?.textContent, "Keep it under 4000 characters");
    assert.equal(box.value, "too much");
});

test("signing someone in starts the log over, numbered from their conversation's start", async () => {
    let who = "anonymous";
    const page = load({
        routes: {
            "POST /session": (call) => {
                if (call.body?.host_token) who = "ana";
                return json({ ...SESSION, access_token: "at_" + who, secret: call.body?.host_token ? null : "vs_1" });
            },
            "GET /history": (call) => json({
                messages: who === "anonymous" && call.path.endsWith("after=-1")
                    ? [{ role: "user", text: "before", sequence: 0 }, { role: "assistant", text: "Hi stranger", sequence: 7 }]
                    : who === "ana" && call.path.endsWith("after=-1") ? [{ role: "assistant", text: "Welcome back, Ana", sequence: 0 }] : [],
            }),
        },
    });
    await openChat(page);
    assert.deepEqual(bubbles(page, "assistant").slice(1), ["Hi stranger"]);
    const lemma = page.window.Lemma as { identify(token: string): Promise<void> };
    await lemma.identify("host-token");
    await settle();
    assert.deepEqual(bubbles(page, "user"), []);
    assert.deepEqual(bubbles(page, "assistant").slice(1), ["Welcome back, Ana"]);
    assert.equal(page.calls.filter((call) => call.path === "/history?after=-1").length, 2);
});

test("a fill frame fills only the table's open columns on a page's own form", async () => {
    const page = load({
        body: '<form method="post" data-lemma-table="signups"><input name="full_name"><input name="internal_note"></form>',
        routes: {
            "GET /table": () => json({
                table: "signups",
                contacts_only: false,
                columns: [{ name: "full_name", type: "TEXT", required: true, options: [], description: null, input: "text" }],
            }),
            "GET /stream": () => ndjson([
                { type: "open" },
                { type: "fill", table: "signups", values: { full_name: "Ana", internal_note: "not yours" } },
                { type: "fill", table: "elsewhere", values: { full_name: "Bo" } },
            ]),
        },
    });
    await openChat(page);
    await say(page, "I'm Ana");
    const form = page.window.document.querySelector("form") as HTMLFormElement;
    assert.equal((form.elements.namedItem("full_name") as HTMLInputElement).value, "Ana");
    assert.equal((form.elements.namedItem("internal_note") as HTMLInputElement).value, "");
});

test("a page's own form is never submitted by the browser, and is sent as a row", async () => {
    const page = load({
        body: '<form data-lemma-table="signups" data-lemma-chat="off"><input name="full_name" value="Ana"></form>',
        routes: { "POST /rows": () => json({ ok: true }, 201) },
    });
    const form = page.window.document.querySelector("form") as HTMLFormElement;
    const submit = new page.window.Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(submit);
    assert.equal(submit.defaultPrevented, true);
    await settle();
    const row = page.calls.find((call) => call.path === "/rows");
    assert.deepEqual(row?.body, { table: "signups", values: { full_name: "Ana" } });
    // "off" on the form means no chat bubble anywhere on the page.
    assert.equal(page.window.document.querySelector("div"), null);
});

test("the hosted form is drawn when no session starts, and says why", async () => {
    const page = load({
        script: 'data-lemma-page data-lemma-table="signups" data-lemma-chat="off"',
        routes: {
            "POST /session": () => json({ message: "This widget is not available", code: "widget_not_found" }, 404),
            "GET /table": () => json({
                table: "signups",
                contacts_only: false,
                columns: [
                    { name: "email", type: "TEXT", required: true, options: [], description: null, input: "email" },
                    { name: "message", type: "TEXT", required: false, options: [], description: null, input: "textarea" },
                ],
            }),
        },
    });
    await settle();
    const root = page.shadow();
    // The table's own controls, not a guess from the column's type.
    assert.equal((root.querySelector('[name="email"]') as HTMLInputElement).type, "email");
    assert.equal(root.querySelector('[name="message"]')?.tagName, "TEXTAREA");
    assert.equal((root.querySelector(".lw-submit") as HTMLButtonElement).disabled, true);
    assert.match(root.textContent ?? "", /This widget is not available/);
});
