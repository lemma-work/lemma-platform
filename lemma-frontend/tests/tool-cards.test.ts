import test from "node:test";
import assert from "node:assert/strict";
import { diffLines, hostOf, parseToolCard, restLength } from "../src/thread/tool-cards.ts";
import { toolKey, toolLabel } from "../src/thread/tool-name.ts";
import { argSummary, buildTurns, commentOf, liveNote, openSignIn, type RawMessage } from "../src/thread/turns.ts";

/** What these tools actually return, pinned.
 *
 *  Every value here is a wire value: the arguments are whatever the model
 *  emitted and the result is whatever the backend serialised. So the cases that
 *  matter are the malformed ones — a card that throws on a missing field takes
 *  the whole transcript with it, and the only safe failure is falling back to
 *  the grey note the rest of the toolset already gets. */

const call = (toolName: string, args: unknown, result?: unknown, answered = result !== undefined) =>
    parseToolCard({ toolName, args, result, answered });

/* ── the tool name ─────────────────────────────────────────────────── */

test("reads a tool through whatever it was namespaced with", () => {
    assert.equal(toolKey("exec_command"), "exec_command");
    assert.equal(toolKey("mcp__lemma__exec_command"), "exec_command");
    // Old conversations hold what local agents used to report.
    assert.equal(toolKey("mcp__lemma_tools__lemma_exec_command"), "exec_command");
    assert.equal(toolKey("lemma_tools_lemma_web_search"), "web_search");
    assert.equal(toolKey("Exec-Command"), "exec_command");
    assert.equal(toolKey(undefined), "");
});

test("never hands somebody else's MCP tool a Lemma card", () => {
    // Namespaced: the server is not Lemma's.
    assert.equal(toolKey("mcp__search__web_search"), "");
    assert.equal(parseToolCard({ toolName: "mcp__search__web_search", args: { query: "acp" }, answered: false }), null);
    // Bare name, but the Agent Host says whose it is.
    const metadata = { tool_source: "mcp", tool_server: "exa" };
    assert.equal(parseToolCard({ toolName: "web_search", args: { query: "acp" }, answered: false, metadata }), null);
    assert.equal(toolLabel("web_search", metadata), "Web search · exa");
    assert.equal(toolLabel("mcp__github__create_issue"), "Create issue · github");
    assert.equal(toolLabel("mcp__lemma_tools__lemma_exec_command"), "Exec command");
});

test("leaves every other tool on the grey note", () => {
    // The fallback is the contract for thirty-odd tools nothing here claims.
    assert.equal(call("pod_write_file", { path: "/me/a.md" }), null);
    assert.equal(call("pod_list_files", { path: "/me" }), null);
});

/* ── browser_sign_in ───────────────────────────────────────────────── */

test("a paused sign-in names the site and stays open", () => {
    const card = call("browser_sign_in", { origin: "https://app.example.com", reason: "The invoices are behind a login." });
    assert.equal(card?.kind, "sign-in");
    assert.equal(card && card.kind === "sign-in" && card.host, "app.example.com");
    assert.equal(card && card.kind === "sign-in" && card.resolved, false);
});

test("resolved is the return landing, not a decision key", () => {
    /* `_browser_sign_in_return` writes `outcome` and `saved`. There is no
       `decision` on this return at all, and reading for one leaves an answered
       pause offering a live link forever. */
    const card = call("browser_sign_in", { origin: "https://app.example.com" }, { outcome: "signed_in", saved: true });
    assert.equal(card && card.kind === "sign-in" && card.resolved, true);
    assert.equal(card && card.kind === "sign-in" && card.signedIn, true);
    assert.equal(card && card.kind === "sign-in" && card.kept, true);
});

test("declined is resolved and not signed in", () => {
    const card = call("browser_sign_in", { origin: "https://app.example.com" }, { outcome: "declined" });
    assert.equal(card && card.kind === "sign-in" && card.resolved, true);
    assert.equal(card && card.kind === "sign-in" && card.signedIn, false);
});

test("an origin that is not a URL still gets a name", () => {
    // `new URL` throws on anything not absolute, and this comes off the model.
    assert.equal(hostOf("app.example.com"), "app.example.com");
    const card = call("browser_sign_in", { origin: "app.example.com" });
    assert.equal(card && card.kind === "sign-in" && card.host, "app.example.com");
});

test("a sign-in with no site is not a card", () => {
    assert.equal(call("browser_sign_in", { reason: "because" }), null);
});

/* ── the browser toolset ───────────────────────────────────────────── */

const browser = (card: ReturnType<typeof call>) => (card && card.kind === "browser" ? card : null);

test("an open names the page it reached, not the one it asked for", () => {
    /* `BrowserResult.url` is "the page's URL after the call" — so a redirect
       is already in the payload, and the card that reads `args.url` instead
       would headline a login page as the dashboard it never got to. */
    const card = browser(
        call(
            "browser_open",
            { url: "https://app.example.com/billing", wait_for_url: "**/billing**", comment: "The table is behind the account." },
            { success: true, url: "https://app.example.com/billing?from=nav", title: "Billing · Example", snapshot: "@e1 link Home" },
        ),
    );
    assert.equal(card?.did, "open");
    assert.equal(card?.host, "app.example.com");
    assert.equal(card?.trail, "/billing?from=nav");
    assert.equal(card?.title, "Billing · Example");
    assert.equal(card?.moved, true);
    assert.equal(card?.awaited, "**/billing**");
    assert.equal(card?.comment, "The table is behind the account.");
});

test("a trailing slash is not a redirect", () => {
    // Otherwise nearly every open would claim it ended up somewhere else.
    const card = browser(call("browser_open", { url: "https://example.com/" }, { success: true, url: "https://example.com" }));
    assert.equal(card?.moved, false);
    assert.equal(card?.trail, "");
});

test("an open still in flight names the host it was pointed at", () => {
    const card = browser(parseToolCard({ toolName: "browser_open", args: { url: "https://example.com/a" }, answered: false }));
    assert.equal(card?.pending, true);
    assert.equal(card?.host, "example.com");
});

test("an open with no address is not a card", () => {
    assert.equal(call("browser_open", { comment: "somewhere" }), null);
});

test("an act is said as the thing it did", () => {
    assert.equal(browser(call("browser_act", { action: "click", target: "@e3" }))?.what, "Clicked @e3");
    assert.equal(browser(call("browser_act", { action: "fill", target: "@e7", text: "ada@example.com" }))?.what, "Filled @e7 with “ada@example.com”");
    assert.equal(browser(call("browser_act", { action: "press", key: "Enter" }))?.what, "Pressed Enter");
    assert.equal(browser(call("browser_act", { action: "scroll", scroll_direction: "down", scroll_amount: 500 }))?.what, "Scrolled down 500px");
    // `scroll_direction` defaults to "down" on the request model.
    assert.equal(browser(call("browser_act", { action: "scroll" }))?.what, "Scrolled down");
});

test("an act outside the nine the backend accepts is not a card", () => {
    // `BrowserActRequest.action` is a closed Literal; anything else would have
    // been rejected before it ran, and this app cannot name it either.
    assert.equal(call("browser_act", { action: "teleport", target: "@e3" }), null);
    assert.equal(call("browser_act", {}), null);
});

test("an act reports where the page ended up", () => {
    const card = browser(
        call(
            "browser_act",
            { action: "click", target: "@e12", wait_for_text: "Business" },
            { success: true, url: "https://app.example.com/plans?compare=1", title: "Compare plans" },
        ),
    );
    assert.equal(card?.host, "app.example.com");
    assert.equal(card?.awaited, "the text “Business”");
    // No before-URL on the wire, so an act never claims a redirect.
    assert.equal(card?.moved, false);
});

test("a read carries the text itself, not a length", () => {
    /* `read_internal` puts the extracted text in `output` — already head- or
       tail-truncated server side — so there is something real to show. */
    const card = browser(
        call("browser_read", { what: "text", target: "@e18" }, { success: true, output: "Starter $12\nTeam $22", url: "https://x.test" }),
    );
    assert.equal(card?.what, "the text of @e18");
    assert.equal(card?.body, "Starter $12\nTeam $22");
    // Short enough that a size label would be longer than the thing measured.
    assert.equal(card?.size, "");
});

test("a long read says how much came back, and whether it was cut", () => {
    const card = browser(
        call("browser_read", { what: "html" }, { success: true, output: "x".repeat(4200), truncated: true }),
    );
    assert.equal(card?.what, "the page’s HTML");
    assert.equal(card?.size, "4.2k characters");
    assert.equal(card?.truncated, true);
});

test("the logs are not the page, and are not named as it", () => {
    // `console` and `network` are the two `what` values that explain a blank
    // screen; calling them "read the page" would hide the debugging call.
    assert.equal(browser(call("browser_read", { what: "console" }))?.what, "the console log");
    assert.equal(browser(call("browser_read", { what: "network" }))?.what, "the network log");
    assert.equal(browser(call("browser_read", { what: "attr", target: "@e2", attribute: "href" }))?.what, "href of @e2");
});

test("a read with no what is not a card", () => {
    assert.equal(call("browser_read", { target: "@e1" }), null);
});

test("a failed browser call keeps the explanation the backend wrote", () => {
    /* `classify_browser_failure` puts the reason in `error` and leaves the
       output empty — the shed browser in particular has no other symptom. */
    const card = browser(
        call("browser_read", { what: "console" }, { success: false, error: "The browser is not running. It is shed automatically…" }),
    );
    assert.equal(card?.failed, true);
    assert.match(card?.error ?? "", /shed automatically/);
});

test("a snapshot is counted in elements when its refs show", () => {
    const plain = browser(
        call("browser_snapshot", {}, { success: true, snapshot: "@e1 link Home\n@e2 button Go\n@e3 table Plans", truncated: false }),
    );
    assert.equal(plain?.what, "the elements it can act on");
    assert.equal(plain?.size, "3 elements");

    // `snapshot_argv_for` passes `--json`, so the refs arrive as a key.
    const json = browser(call("browser_snapshot", {}, { success: true, snapshot: '{"nodes":[{"ref":"e1"},{"ref":"e2"}]}' }));
    assert.equal(json?.size, "2 elements");

    // Neither shape: a character count is less useful and still true.
    const opaque = browser(call("browser_snapshot", {}, { success: true, snapshot: "the page" }));
    assert.equal(opaque?.size, "8 characters");
});

test("the full tree and the interactive one are different captures", () => {
    // `interactive_only` defaults to true, so an absent flag is the narrow one.
    assert.equal(browser(call("browser_snapshot", {}))?.what, "the elements it can act on");
    assert.equal(browser(call("browser_snapshot", { interactive_only: false }))?.what, "the whole page tree");
});

test("a screenshot has no image to show, and says what it captured instead", () => {
    /* Verified against the backend: `screenshot_internal` returns
       `ToolReturn(return_value=BrowserScreenshotResponse(...), content=[BinaryContent(...)])`,
       and pydantic-ai sends `content` to the model as a separate prompt part.
       Only `return_value` becomes the ToolReturnPart, so `tool_result` carries
       metadata and nothing else — there are no bytes here to draw. */
    const card = browser(
        call(
            "browser_screenshot",
            { full_page: true, instructions: "Is the Business column filled in?" },
            {
                success: true,
                message: "Screenshot of https://x.test/plans.",
                url: "https://x.test/plans",
                title: "Plans",
                media_type: "image/jpeg",
                size_bytes: 418_233,
                full_page: true,
            },
        ),
    );
    assert.equal(card?.did, "shot");
    assert.equal(card?.host, "x.test");
    assert.equal(card?.size, "408 KB");
    assert.equal(card?.fullPage, true);
    assert.equal(card?.asked, "Is the Business column filled in?");
    // `message` here is the boilerplate "Screenshot of <url>." and must not be
    // shown as though somebody looked at the picture.
    assert.equal(card?.seen, "");
});

test("a screenshot for a model that cannot see comes back as words", () => {
    /* The shape this brief did not predict. With no vision the tool delegates
       to `describe_single_image` and returns a `ViewImageResponse`, whose
       `message` IS the description and whose `file_path` — set to
       `parsed.url or path` — is the only address it carries. */
    const card = browser(
        call(
            "browser_screenshot",
            { annotate: true, instructions: "Which control switches to monthly?" },
            {
                success: true,
                message: "The table has three columns and no monthly toggle above it.",
                file_path: "https://x.test/plans",
                media_type: "image/png",
                source: "workspace",
                size_bytes: 271_904,
            },
        ),
    );
    assert.equal(card?.seen, "The table has three columns and no monthly toggle above it.");
    assert.equal(card?.host, "x.test");
    assert.equal(card?.size, "266 KB");
});

test("a browser call reads through whatever it was namespaced with", () => {
    assert.equal(browser(call("mcp__lemma__browser_snapshot", {}))?.kind, "browser");
});

test("a malformed browser payload falls back rather than throwing", () => {
    // Every value here is off the wire, and a card that throws takes the
    // transcript with it.
    assert.equal(call("browser_open", { url: 42 }), null);
    assert.equal(browser(call("browser_snapshot", null, "not an object", true))?.size, "");
    assert.equal(browser(call("browser_open", { url: "not a url" }, { url: 7, title: null }))?.host, "not a url");
    assert.equal(browser(call("browser_screenshot", {}, { size_bytes: "big" }))?.size, "");
});

/* ── exec_command / execute_python ─────────────────────────────────── */

test("a command carries its output and its exit status", () => {
    const card = call(
        "exec_command",
        { cmd: "npm test", workdir: "/workspace/app" },
        { success: true, stdout: "2 passing\n", stderr: "", exit_code: 0, completed: true },
    );
    assert.equal(card?.kind, "terminal");
    assert.equal(card && card.kind === "terminal" && card.exitCode, 0);
    assert.equal(card && card.kind === "terminal" && card.failed, false);
    assert.equal(card && card.kind === "terminal" && card.output, "2 passing");
    assert.equal(card && card.kind === "terminal" && card.workdir, "/workspace/app");
});

test("a non-zero exit is a failure and a live process is not", () => {
    const broke = call("exec_command", { cmd: "npm ci" }, { success: false, exit_code: 1, stderr: "boom" });
    assert.equal(broke && broke.kind === "terminal" && broke.failed, true);

    /* `completed: false` means the call's wait window ended, not the command.
       Painting a running build red is the worse of the two readings. */
    const going = call("exec_command", { cmd: "npm run build" }, { success: true, completed: false, process_id: "abc" });
    assert.equal(going && going.kind === "terminal" && going.running, true);
    assert.equal(going && going.kind === "terminal" && going.failed, false);
    assert.equal(going && going.kind === "terminal" && going.processId, "abc");
});

test("an unfamiliar return is not a failure", () => {
    // No exit code and no `success` is a shape this app has not seen, and
    // colouring every one of those red makes the colour mean nothing.
    const card = call("exec_command", { cmd: "ls" }, { stdout: "a\nb\n" });
    assert.equal(card && card.kind === "terminal" && card.failed, false);
    assert.equal(card && card.kind === "terminal" && card.lines, 2);
});

test("python keeps its value and renders its traceback", () => {
    const card = call(
        "execute_python",
        { code: "1/0" },
        {
            success: false,
            stdout: "",
            result: null,
            error_in_exec: { ename: "ZeroDivisionError", evalue: "division by zero", traceback: ["  line 1", "ZeroDivisionError"] },
        },
    );
    assert.equal(card && card.kind === "terminal" && card.language, "python");
    assert.match(card && card.kind === "terminal" ? card.errorOutput : "", /ZeroDivisionError: division by zero/);
    assert.equal(card && card.kind === "terminal" && card.failed, true);
});

test("a command with no command is not a card", () => {
    assert.equal(call("exec_command", { workdir: "/workspace" }), null);
    assert.equal(call("execute_python", {}), null);
});

test("a return nested under output reads the same as a flat one", () => {
    // A resolved pause is replayed wrapped; an agent host returns it flat.
    const card = call("exec_command", { cmd: "ls" }, { output: { exit_code: 2, stdout: "x" } });
    assert.equal(card && card.kind === "terminal" && card.exitCode, 2);
});

/* ── web_search / web_fetch ────────────────────────────────────────── */

test("search results become sources a reader can follow", () => {
    const card = call(
        "web_search",
        { query: "pydantic ai toolsets" },
        {
            success: true,
            results: [
                { title: "Toolsets", url: "https://ai.pydantic.dev/toolsets/", snippet: "A toolset is…", source: "pydantic" },
                { title: "No url", snippet: "dropped", source: "x" },
            ],
        },
    );
    assert.equal(card?.kind, "sources");
    assert.equal(card && card.kind === "sources" && card.sources.length, 1);
    assert.equal(card && card.kind === "sources" && card.sources[0].host, "ai.pydantic.dev");
});

test("a fetch names its pages and where they were saved", () => {
    const card = call(
        "web_fetch",
        { urls: ["https://example.com/a"] },
        {
            success: true,
            out_dir: "research",
            pages: [
                {
                    url: "https://example.com/a",
                    success: true,
                    title: "A",
                    files: { markdown: "research/a.md", pdf: "research/a.pdf" },
                    preview: "first words",
                },
            ],
        },
    );
    assert.equal(card && card.kind === "sources" && card.action, "fetch");
    assert.equal(card && card.kind === "sources" && card.sources[0].savedAs, "research/a.md");
});

test("a fetch in flight shows the pages it was asked for", () => {
    // Fetching five pages takes minutes, and an empty card for the length of
    // it says less than the grey line it replaced.
    const card = parseToolCard({ toolName: "web_fetch", args: { urls: ["https://a.test/x"] }, answered: false });
    assert.equal(card && card.kind === "sources" && card.pending, true);
    assert.equal(card && card.kind === "sources" && card.sources.length, 1);
});

test("a search with no query is not a card", () => {
    assert.equal(call("web_search", { max_results: 5 }), null);
});

/* ── run_connector_operation ───────────────────────────────────────── */

test("a connector call names the install and the operation", () => {
    const card = call(
        "run_connector_operation",
        { auth_config: "gmail", operation: "GMAIL_SEND_EMAIL", arguments: { to: "a@b.co", body: { html: "…" } } },
        { result: { id: "m1" } },
    );
    assert.equal(card?.kind, "connector");
    assert.equal(card && card.kind === "connector" && card.connector, "gmail");
    assert.equal(card && card.kind === "connector" && card.failed, false);
    assert.deepEqual(
        card && card.kind === "connector" ? card.params : [],
        [
            { name: "to", value: "a@b.co" },
            { name: "body", value: "{ html }" },
        ],
    );
});

test("a connector failure is a dictionary, not an exception", () => {
    const card = call(
        "run_connector_operation",
        { auth_config: "outlook", operation: "OUTLOOK_SEND" },
        { error: "invalid_arguments", message: "The arguments do not match this operation's input schema." },
    );
    assert.equal(card && card.kind === "connector" && card.failed, true);
    assert.match(card && card.kind === "connector" ? card.error : "", /input schema/);
});

test("a connector call missing either name is not a card", () => {
    assert.equal(call("run_connector_operation", { auth_config: "gmail" }), null);
});

/* ── view_image ────────────────────────────────────────────────────── */

const look = (card: ReturnType<typeof call>) => (card && card.kind === "image" ? card : null);

test("an image the agent looked at names its file and which store holds it", () => {
    // `ViewImageResponse.source` is `datastore` or `workspace`, and it is the
    // authority: `view_image_internal` picks the store from whichever argument
    // was set and echoes the choice back, explicitly refusing to infer it from
    // the path's shape.
    const pod = look(
        call(
            "view_image",
            { pod_file_path: "/launch/hero.png", instructions: "Is the logo legible?" },
            { success: true, message: "Successfully read image /launch/hero.png", file_path: "/launch/hero.png", source: "datastore", media_type: "image/png", size_bytes: 842_118 },
        ),
    );
    assert.equal(pod?.store, "pod");
    assert.equal(pod?.path, "/launch/hero.png");
    assert.equal(pod?.name, "hero.png");
    assert.equal(pod?.relative, false);
    assert.equal(pod?.asked, "Is the logo legible?");
    assert.equal(pod?.weight, "822 KB");
    assert.equal(pod?.failed, false);
});

test("a relative workspace path is flagged, because it resolves somewhere this app cannot guess", () => {
    // `images/output.png` is joined onto the *conversation's* directory by the
    // agent's file manager and onto `/workspace` by this app's own route. Same
    // string, two different files — so the card carries the fact and the view
    // fetches the directory before it fetches anything else.
    const card = look(
        call(
            "view_image",
            { workspace_file_path: "images/output.png", instructions: "Read the axis labels." },
            { success: true, file_path: "images/output.png", source: "workspace" },
        ),
    );
    assert.equal(card?.store, "workspace");
    assert.equal(card?.relative, true);
    assert.equal(card?.name, "output.png");

    const absolute = look(
        call("view_image", { workspace_file_path: "/workspace/c/2026-09-18/a/out.png" }, { success: true, source: "workspace" }),
    );
    assert.equal(absolute?.relative, false);
});

test("the return's path wins over the argument", () => {
    const card = look(
        call(
            "view_image",
            { workspace_file_path: "out.png" },
            { success: true, file_path: "/workspace/c/2026-09-18/slug/out.png", source: "workspace" },
        ),
    );
    assert.equal(card?.path, "/workspace/c/2026-09-18/slug/out.png");
    assert.equal(card?.relative, false);
});

test("the argument carries the card while the call is still in flight", () => {
    const card = look(call("view_image", { pod_file_path: "/me/photo.jpg", instructions: "Whose desk is this?" }));
    assert.equal(card?.store, "pod");
    assert.equal(card?.path, "/me/photo.jpg");
    assert.equal(card?.pending, true);
    assert.equal(card?.described, "");
});

test("the boilerplate message is not a description; the delegated one is", () => {
    // Two shapes, one field. A run whose model can see gets
    // `Successfully read image <path>`, which says nothing the card is not
    // already showing. A run whose model cannot gets the picture in words from
    // the vision model `describe_single_image` handed it to.
    const plain = look(call("view_image", { pod_file_path: "/me/a.png" }, { success: true, message: "Successfully read image /me/a.png", source: "datastore" }));
    assert.equal(plain?.described, "");

    const seen = look(call("view_image", { pod_file_path: "/me/a.png" }, { success: true, message: "Three columns, each headed by a yearly price.", source: "datastore" }));
    assert.equal(seen?.described, "Three columns, each headed by a yearly price.");
});

test("two paths or none is not a file this app can name", () => {
    // The "exactly one path" rule lives in `view_image_internal`, not in a
    // validator, so both of these reach the transcript. Neither may throw, and
    // neither may be guessed at: the store stays empty, which is what stops the
    // card offering to fetch one of two files it cannot choose between.
    const both = look(call("view_image", { pod_file_path: "/me/a.png", workspace_file_path: "a.png" }));
    assert.equal(both?.store, "");

    // Nothing to name at all drops to the grey step, like any unread call.
    assert.equal(call("view_image", { instructions: "look at it" }), null);
    assert.equal(call("view_image", {}), null);
    assert.equal(call("view_image", null), null);
});

test("a refused look says why and is not offered as a picture", () => {
    const card = look(
        call(
            "view_image",
            { workspace_file_path: "notes.pdf" },
            { success: false, error: "This is a PDF, not an image.", file_path: "notes.pdf", source: "workspace" },
        ),
    );
    assert.equal(card?.failed, true);
    assert.equal(card?.error, "This is a PDF, not an image.");
});

test("a malformed view_image return never throws", () => {
    assert.doesNotThrow(() => call("view_image", { pod_file_path: "/me/a.png" }, "not an object"));
    assert.doesNotThrow(() => call("view_image", { pod_file_path: 7 }, { file_path: [] }));
    // A path that is a number is no path at all.
    assert.equal(call("view_image", { pod_file_path: 7 }, { file_path: [] }), null);
});

/* ── snooze ────────────────────────────────────────────────────────── */

test("a sleeping run says so, and when it is due back", () => {
    const card = parseToolCard({
        toolName: "snooze",
        args: { reason: "waiting for the nightly build", seconds: 600 },
        answered: false,
        atMs: 1_700_000_000_000,
    });
    assert.equal(card?.kind, "snooze");
    assert.equal(card && card.kind === "snooze" && card.sleeping, true);
    // Nothing on the wire carries a wake time; it is the call's clock plus the
    // length it asked for.
    assert.equal(card && card.kind === "snooze" && card.wakeAtMs, 1_700_000_000_000 + 600_000);
});

test("a woken run says what woke it", () => {
    const card = call("snooze", { reason: "build", seconds: 600 }, { woke_because: "TIMER", slept_seconds: 600 });
    assert.equal(card && card.kind === "snooze" && card.sleeping, false);
    assert.equal(card && card.kind === "snooze" && card.wokeBecause, "TIMER");
});

test("a sleep is said in round numbers", () => {
    assert.equal(restLength(30), "30 seconds");
    assert.equal(restLength(600), "10 minutes");
    assert.equal(restLength(7200), "2 hours");
    assert.equal(restLength(0), "");
});

/* ── through the turn builder ──────────────────────────────────────── */

const at = (seconds: number) => new Date(1_700_000_000_000 + seconds * 1000).toISOString();
function message(partial: Partial<RawMessage> & { sequence: number }): RawMessage {
    return { id: "m" + partial.sequence, created_at: at(partial.sequence), ...partial };
}

test("work stays behind the fold, however well this app can read it", () => {
    // The point of the fold is that a run's dozens of tool calls do not bury
    // the two sentences the teammate actually said. Reading a call well is a
    // reason to render the step better, not a reason to promote it into the
    // transcript — a card in the conversation per tool call is the same wall
    // of output the fold was built to prevent, only prettier.
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "check the build" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "exec_command", tool_call_id: "c1", tool_args: { cmd: "npm run build" } }),
        message({ sequence: 3, kind: "TOOL_RETURN", tool_call_id: "c1", tool_result: { success: true, exit_code: 0, stdout: "ok" } }),
        message({ sequence: 4, kind: "TOOL_CALL", tool_name: "pod_list_files", tool_args: { path: "/me" } }),
        // A browser run is the case that would break this fastest: open,
        // snapshot, act, read, screenshot is five cards for one glance at one
        // page, and none of them is the run handing control back.
        message({ sequence: 5, kind: "TOOL_CALL", tool_name: "browser_open", tool_args: { url: "https://example.com" } }),
        message({ sequence: 6, kind: "TOOL_CALL", tool_name: "browser_screenshot", tool_args: { full_page: true } }),
        // A card that can show a picture is the strongest temptation to make an
        // exception of, and is not one: a run that looks at a chart, a crop of
        // it and the fixed version is three images in the transcript for one
        // piece of working.
        message({ sequence: 7, kind: "TOOL_CALL", tool_name: "view_image", tool_args: { workspace_file_path: "shot.png" } }),
        message({ sequence: 8, role: "assistant", text: "Green." }),
    ]);

    assert.deepEqual(turn.items.map((item) => item.kind), ["text"]);
    assert.deepEqual(
        turn.notes.map((note) => note.label),
        ["Exec command", "Pod list files", "Browser open", "Browser screenshot", "View image"],
    );
    // The read ones carry their card, so opening the steps shows the command
    // and its output. The unclaimed one still gets the grey line it always had.
    assert.equal(turn.notes[0].card?.kind, "terminal");
    assert.equal(turn.notes[1].card, undefined);
    assert.equal(turn.notes[2].card?.kind, "browser");
    assert.equal(turn.notes[3].card?.kind, "browser");
    assert.equal(turn.notes[4].card?.kind, "image");
});

test("a run waiting on a person is not work, and leaves the fold", () => {
    // The one exception, and it is the same rule the approval card follows:
    // this is the run handing control back. A resolved one stays out too —
    // what you were asked and what you answered is a record worth scrolling
    // back to.
    const open = buildTurns([
        message({ sequence: 1, role: "user", text: "pull the invoices" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "browser_sign_in", tool_call_id: "s1", tool_args: { origin: "https://billing.example.com" } }),
    ])[0];

    assert.deepEqual(open.items.map((item) => item.kind), ["tool-card"]);
    assert.equal(open.notes.length, 0);

    const settled = buildTurns([
        message({ sequence: 1, role: "user", text: "pull the invoices" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "browser_sign_in", tool_call_id: "s1", tool_args: { origin: "https://billing.example.com" } }),
        message({ sequence: 3, kind: "TOOL_RETURN", tool_call_id: "s1", tool_result: { outcome: "signed_in", saved: true } }),
    ])[0];

    assert.deepEqual(settled.items.map((item) => item.kind), ["tool-card"]);
});

test("an unanswered sign-in is the pause the composer has to name", () => {
    const turns = buildTurns([
        message({ sequence: 1, role: "user", text: "pull the invoices" }),
        message({
            sequence: 2,
            kind: "TOOL_CALL",
            tool_name: "browser_sign_in",
            tool_call_id: "call_signin",
            tool_args: { origin: "https://billing.example.com", reason: "The invoices are behind a login." },
        }),
    ]);

    assert.equal(openSignIn(turns)?.host, "billing.example.com");

    const answered = buildTurns([
        message({ sequence: 1, role: "user", text: "pull the invoices" }),
        message({
            sequence: 2,
            kind: "TOOL_CALL",
            tool_name: "browser_sign_in",
            tool_call_id: "call_signin",
            tool_args: { origin: "https://billing.example.com" },
        }),
        message({ sequence: 3, kind: "TOOL_RETURN", tool_call_id: "call_signin", tool_result: { outcome: "signed_in" } }),
    ]);
    assert.equal(openSignIn(answered), null);
});

test("the agent's own line is what a step says, when it gave one", () => {
    // `comment` is described by the backend, in as many words, as "One-line
    // statement of intent, shown to the user" — a shared field on every
    // browser, web and shell tool. It was being dropped, and the step printed
    // the *names of the arguments* instead: "comment, full_page, instructions".
    assert.equal(commentOf({ comment: "Fix boot timing and re-render" }), "Fix boot timing and re-render");
    assert.equal(commentOf({ comment: "  wraps   and   trims  " }), "wraps and trims");
    // Nothing invented where nothing was said.
    assert.equal(commentOf({ full_page: true }), "");
    assert.equal(commentOf(null), "");
    assert.equal(commentOf({ comment: 42 }), "");
    assert.equal(commentOf({ comment: "   " }), "");
    // A statement of intent that is a paragraph is not one; cut it.
    assert.ok(commentOf({ comment: "x".repeat(400) }).endsWith("…"));
    assert.equal(commentOf({ comment: "x".repeat(400) }).length, 140);
});

test("a step falls back to describing the call, as it always did", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "look at the page" }),
        message({
            sequence: 2, kind: "TOOL_CALL", tool_name: "browser_screenshot",
            tool_args: { comment: "Fix boot timing and re-render", full_page: true, instructions: "whole page" },
        }),
        message({ sequence: 3, kind: "TOOL_CALL", tool_name: "browser_snapshot", tool_args: { full_page: true, instructions: "x" } }),
    ]);

    assert.equal(turn.notes[0].detail, "Fix boot timing and re-render");
    assert.equal(turn.notes[0].said, true);
    // No comment: the old summary, and not dressed up as something the agent
    // said — the two read differently and are marked differently.
    assert.equal(turn.notes[1].detail, argSummary({ full_page: true, instructions: "x" }));
    assert.equal(turn.notes[1].said, false);
});

test("a run in flight says what the step in front of it is for", () => {
    // The closed row sat still through a four-minute run. Two causes, both in
    // `liveNote`: the live step carried no arguments, so there was no comment
    // to show and it fell back to the last one that had already landed —
    // describing something the agent finished with several steps ago — and the
    // label it did show was the raw envelope name, `exec_command`, two lines
    // under a comment complaining about exactly that.
    const inFlight = liveNote({
        text: "", thinking: "",
        tool: { toolName: "web_search", args: { comment: "Gathering primary reporting on UPI", query: "upi" } },
    });

    assert.equal(inFlight[0].label, "Web search");
    assert.equal(inFlight[0].detail, "Gathering primary reporting on UPI");
    assert.equal(inFlight[0].said, true);

    // No comment: the call is still named properly and described by its args.
    const quiet = liveNote({ text: "", thinking: "", tool: { toolName: "exec_command", args: { command: "npm test" } } });

    assert.equal(quiet[0].label, "Exec command");
    assert.equal(quiet[0].detail, "npm test");
    assert.equal(quiet[0].said, false);

    // A pause is named for what it wants, not for the tool that carries it.
    assert.equal(liveNote({ text: "", thinking: "", tool: { toolName: "request_approval", args: {} } })[0].label, "Waiting on you");
    // Streaming text means the work is over; the row has nothing to add.
    assert.deepEqual(liveNote({ text: "Here you go", thinking: "", tool: null }), []);
});

/* ── a local agent's canonical tools ───────────────────────────────── */

const host = (toolName: string, args: unknown, result?: unknown, answered = result !== undefined) =>
    parseToolCard({ toolName, args, result, answered, metadata: { tool_source: "native", tool_title: toolName } });

test("read_file names the file and keeps what was in it", () => {
    const card = host("read_file", { file_path: "/workspace/fixture/notes.txt", offset: 10, limit: 5 }, { content: "one\ntwo\n" });
    assert.equal(card?.kind, "file-read");
    if (card?.kind !== "file-read") return;
    assert.equal(card.name, "notes.txt");
    assert.equal(card.range, "lines 10–15");
    assert.equal(card.lines, 2);
    assert.equal(card.failed, false);
    assert.equal(host("read_file", {}), null);
});

test("a failed call keeps its error", () => {
    const card = host("read_file", { file_path: "/nope" }, { success: false, error: "not allowed" });
    assert.equal(card?.kind === "file-read" && card.failed, true);
    assert.equal(card?.kind === "file-read" && card.error, "not allowed");
});

test("write_file is a file of added lines", () => {
    const card = host("write_file", { file_path: "/w/notes.txt", content: "one\n" }, { message: "Wrote file successfully." });
    assert.equal(card?.kind, "file-change");
    if (card?.kind !== "file-change") return;
    assert.equal(card.action, "write");
    assert.deepEqual(card.files[0].lines, [{ sign: "+", text: "one" }]);
    assert.equal(card.added, 1);
    assert.equal(card.message, "Wrote file successfully.");
});

test("edit_file prefers the applied changes over the strings asked for", () => {
    // Codex's shape: `changes` on both sides, the return being what landed.
    const changes = [{ file_path: "/w/notes.txt", kind: "update", old_text: "one\n", new_text: "two\n" }];
    const card = host("edit_file", { file_path: "/w/notes.txt", changes }, { changes });
    assert.equal(card?.kind === "file-change" && card.added, 1);
    assert.equal(card?.kind === "file-change" && card.removed, 1);
    // OpenCode's: one string replacement.
    const replaced = host("edit_file", { file_path: "/w/a.ts", old_string: "const a = 1;", new_string: "const a = 2;" });
    assert.equal(replaced?.kind === "file-change" && replaced.pending, true);
    assert.deepEqual(replaced?.kind === "file-change" && replaced.files[0].lines, [
        { sign: "-", text: "const a = 1;" },
        { sign: "+", text: "const a = 2;" },
    ]);
});

test("a patch across several files names how many", () => {
    const card = host("edit_file", {
        changes: [
            { file_path: "/w/a.ts", kind: "add", new_text: "x\n" },
            { file_path: "/w/b.ts", kind: "delete", old_text: "y\n" },
        ],
    });
    assert.equal(card?.kind === "file-change" && card.name, "2 files");
    assert.equal(card?.kind === "file-change" && card.files[1].change, "delete");
});

test("delete and move name their files", () => {
    const gone = host("delete_file", { file_path: "/w/old.txt" }, { message: "deleted" });
    assert.equal(gone?.kind === "file-change" && gone.action, "delete");
    const moved = host("move_file", { source: "/w/a.txt", destination: "/w/b.txt" }, { message: "moved" });
    assert.equal(moved?.kind === "file-change" && moved.path, "/w/a.txt");
    assert.equal(moved?.kind === "file-change" && moved.destination, "/w/b.txt");
});

test("a diff folds what did not change", () => {
    const before = Array.from({ length: 20 }, (_, index) => "line " + index).join("\n");
    const after = before.replace("line 10", "line ten");
    const lines = diffLines(before, after);
    assert.deepEqual(lines.filter((line) => line.sign === "-"), [{ sign: "-", text: "line 10" }]);
    assert.deepEqual(lines.filter((line) => line.sign === "+"), [{ sign: "+", text: "line ten" }]);
    assert.equal(lines[0].sign, "gap");
    assert.equal(lines.at(-1)?.sign, "gap");
    assert.ok(lines.length < 12);
});

test("list_files, glob and grep carry their pattern and output", () => {
    const grep = host("grep", { pattern: "two", path: "/w", glob: "*.txt" }, { output: "notes.txt:1:two\nnotes.txt:4:two\n" });
    assert.equal(grep?.kind === "file-search" && grep.pattern, "two");
    assert.equal(grep?.kind === "file-search" && grep.filter, "*.txt");
    assert.equal(grep?.kind === "file-search" && grep.count, 2);
    const glob = host("glob", { pattern: "*.txt", path: "/w" }, { output: "/w/notes.txt" });
    assert.equal(glob?.kind === "file-search" && glob.count, 1);
    // Codex lists with no arguments at all; the adapter's title stands in.
    const listed = host("list_files", {}, { output: "notes.txt\n" });
    assert.equal(listed?.kind === "file-search" && listed.title, "list_files");
    assert.equal(listed?.kind === "file-search" && listed.output, "notes.txt");
});

test("task says what the sub-agent was sent to do and what it said", () => {
    const card = host("task", { description: "Find the flaky test", prompt: "Look in tests/", subagent_type: "explore" }, { output: "It is test_x." });
    assert.equal(card?.kind, "task");
    if (card?.kind !== "task") return;
    assert.equal(card.description, "Find the flaky test");
    assert.equal(card.agentType, "explore");
    assert.equal(card.output, "It is test_x.");
    assert.equal(host("task", {}), null);
});

test("a local agent's web_search reads its canonical output", () => {
    const prose = host("web_search", { query: "Agent Client Protocol" }, { output: "ACP standardizes editors and agents." });
    assert.equal(prose?.kind === "sources" && prose.text, "ACP standardizes editors and agents.");
    assert.equal(prose?.kind === "sources" && prose.listed, false);
    // Codex closes a search with nothing: no count, no "nothing came back".
    const bare = host("web_search", { query: "acp" }, null);
    assert.equal(bare?.kind === "sources" && bare.listed, false);
    assert.equal(bare?.kind === "sources" && bare.text, "");
    // A result list written as JSON text is still a list.
    const json = JSON.stringify({ results: [{ url: "https://agentclientprotocol.com", title: "ACP" }] });
    const listed = host("web_search", { query: "acp" }, { output: json });
    assert.equal(listed?.kind === "sources" && listed.sources.length, 1);
    assert.equal(listed?.kind === "sources" && listed.text, "");
});

test("a local agent's web_fetch names its one url and what it asked", () => {
    const card = host("web_fetch", { url: "https://example.com/doc", prompt: "What is the rate limit?" }, { output: "100 a minute." });
    assert.equal(card?.kind, "sources");
    if (card?.kind !== "sources") return;
    assert.equal(card.sources[0]?.host, "example.com");
    assert.equal(card.asked, "What is the rate limit?");
    assert.equal(card.text, "100 a minute.");
});

test("the pod agent's richer web shapes still read as before", () => {
    const search = call("web_search", { query: "acp" }, { results: [{ url: "https://a.example", title: "A" }] });
    assert.equal(search?.kind === "sources" && search.listed, true);
    assert.equal(search?.kind === "sources" && search.sources.length, 1);
    const fetch = call("web_fetch", { urls: ["https://a.example"] }, { pages: [{ url: "https://a.example", success: true, files: { markdown: "/w/a.md" } }] });
    assert.equal(fetch?.kind === "sources" && fetch.sources[0]?.savedAs, "/w/a.md");
});
