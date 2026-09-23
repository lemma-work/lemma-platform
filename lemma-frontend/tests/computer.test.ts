import test from "node:test";
import assert from "node:assert/strict";
import {
    crumbs, describe, isNoise, machineState, ordered, parentOf, readableSize, rootsOf,
    screenSay, viewerFor, watchable, type Entry, type Listing,
} from "../src/computer/machine.ts";
import { isSettled, retryDelay, socketUrl, stateFromClose } from "../src/computer/live.ts";
import { outcomeSay, whereabouts } from "../src/computer/sign-in.ts";
import { groupSites, lastsFor, loginNote, saidSoFor } from "../src/computer/logins.ts";
import type { WebLogin } from "../src/computer/queries.ts";

/* What a current deployment answers with. Written out rather than imported
   because the point of the roots is that this app does not know them: a test
   that shared a constant with the code could not tell a served root from a
   guessed one. */
const ROOTS = { home: "/home/user", workspace: "/home/user/lemma" };
const ROOT = ROOTS.workspace;

function entry(name: string, kind: Entry["kind"] = "file"): Entry {
    return { path: ROOT + "/" + name, name, kind, size_bytes: 0, modified_at: "2026-09-18T10:00:00Z" };
}

test("the roots are the server's answer, and the constant is only a first guess", () => {
    // This is the whole fix. The sandbox root moved into the durable home and
    // the hardcoded copy stayed behind, so the pane went on asking for a path
    // the route now refuses outright — which reads as a machine that cannot be
    // reached rather than as a client looking in the wrong place. A listing
    // says where things are; nothing here may decide it.
    const moved: Listing = { path: "/srv/box/work", home_root: "/srv/box", workspace_root: "/srv/box/work" };

    assert.deepEqual(rootsOf(moved), { home: "/srv/box", workspace: "/srv/box/work" });
    assert.equal(parentOf("/srv/box/work", rootsOf(moved)), "/srv/box");
    assert.equal(parentOf("/srv/box", rootsOf(moved)), null);
    assert.match(describe("/srv/box/work/c/2026-09-18/ab12cd34", rootsOf(moved)) ?? "", /one conversation's shell/);

    // A deployment too old to send them, and one that sends them empty, are
    // the same thing to a reader: no answer.
    assert.deepEqual(rootsOf({ path: ROOT }), ROOTS);
    assert.deepEqual(rootsOf({ path: ROOT, home_root: "", workspace_root: "" }), ROOTS);
    assert.deepEqual(rootsOf(undefined), ROOTS);
});

test("the top of the machine has nowhere above it", () => {
    // The ceiling is the home rather than where projects live, so walking all
    // the way up reaches the machine — which is a level above the root this
    // pane opens on.
    assert.equal(parentOf(ROOTS.home, ROOTS), null);
    assert.equal(parentOf(ROOTS.home + "/", ROOTS), null);
    assert.equal(parentOf(ROOT, ROOTS), ROOTS.home);
    // The route refuses anything that spells or resolves its way out of the
    // home, so a path outside it has no parent this pane could ask for.
    assert.equal(parentOf("/tmp/staged-credential", ROOTS), null);
    assert.equal(parentOf(ROOT + "/c/2026-09-18/ab12cd34", ROOTS), ROOT + "/c/2026-09-18");
    assert.equal(parentOf(ROOT + "/repos", ROOTS), ROOT);
});

test("the crumb starts at the machine and names every step", () => {
    assert.deepEqual(crumbs(ROOTS.home, ROOTS), [{ name: "Computer", path: ROOTS.home }]);
    assert.deepEqual(
        crumbs(ROOT + "/repos/acme/web", ROOTS).map((step) => step.name),
        ["Computer", "lemma", "repos", "acme", "web"],
    );
    assert.deepEqual(
        crumbs(ROOT + "/repos", ROOTS).map((step) => step.path),
        [ROOTS.home, ROOT, ROOT + "/repos"],
    );
    // A trailing slash is the same directory, not a nameless child.
    assert.deepEqual(crumbs(ROOT + "/repos/", ROOTS).map((step) => step.name), ["Computer", "lemma", "repos"]);
    // Outside the home there is nothing to walk back into.
    assert.deepEqual(crumbs("/tmp/elsewhere", ROOTS), [{ name: "Computer", path: ROOTS.home }]);
});

test("the agent's own layout is what makes these paths readable", () => {
    // {workspace}/c/{date}/{slug} and {workspace}/repos/{owner}/{repo} are
    // conventions the agent's tools resolve against. Without reading them the
    // pane shows a column of random slugs and leaves the reader to guess.
    assert.match(describe(ROOT + "/c/2026-09-18/ab12cd34", ROOTS) ?? "", /one conversation's shell/);
    assert.match(describe(ROOT + "/c", ROOTS) ?? "", /per conversation/);
    assert.match(describe(ROOT + "/c/2026-09-18", ROOTS) ?? "", /2026-09-18/);
    assert.match(describe(ROOT + "/repos/acme/web", ROOTS) ?? "", /acme\/web/);
    // The home and the project root are two different places and say so. They
    // were one path until the durable root moved, which is what made the whole
    // machine and the folder projects live in indistinguishable.
    assert.match(describe(ROOTS.home, ROOTS) ?? "", /whole machine/);
    assert.match(describe(ROOT, ROOTS) ?? "", /conversations and checkouts/);
    // Nothing invented for a folder somebody made themselves.
    assert.equal(describe(ROOT + "/scratch", ROOTS), null);
    assert.equal(describe(ROOT + "/repos/acme/web/src", ROOTS), null);
    // And nothing invented for the home's own neighbours: `.npm` and `.cargo`
    // live here now, and they are not conversations.
    assert.equal(describe(ROOTS.home + "/.cargo", ROOTS), null);
});

test("the listing decides whether the machine is asleep, and the browser does not", () => {
    // Both endpoints describe one sandbox. One of them has to win or the view
    // contradicts itself the moment they disagree — and the listing is the one
    // this view always has an answer from.
    const asleep: Listing = { path: ROOT, sleeping: true };
    const awake: Listing = { path: ROOT, entries: [] };

    assert.equal(machineState(asleep, "running", false), "asleep");
    assert.equal(machineState(awake, "running", false), "browsing");
    assert.equal(machineState(awake, "stopped", false), "awake");
    assert.equal(machineState(awake, undefined, false), "awake");
});

test("not knowing yet is not the same as asleep", () => {
    // This answered "asleep" before the first listing landed, so opening the
    // view flashed "Asleep" and offered to wake a machine that was already
    // running. Not knowing is its own state and offers nothing.
    assert.equal(machineState(undefined, undefined, true), "checking");
    assert.equal(machineState(undefined, "running", true), "checking");
    // And a listing that failed is a third thing again: it is not in flight,
    // and it is not an answer.
    assert.equal(machineState(undefined, undefined, false), "unreachable");

    assert.equal(screenSay("checking", undefined).action, null);
    assert.equal(screenSay("unreachable", undefined).action, null);
});

test("a browser that cannot be reached is not offered", () => {
    // `unavailable` is an image too old to carry the relay and `unsupported`
    // is a fabric that cannot reach a port at all. Neither is fixed by
    // clicking, and a button that cannot work is worse than no button.
    assert.equal(watchable("running"), true);
    assert.equal(watchable("stopped"), true);
    assert.equal(watchable("unavailable"), false);
    assert.equal(watchable("unsupported"), false);
    assert.equal(watchable(undefined), false);
});

test("folders come first, and the cursor is left alone", () => {
    const listed = [entry("report.md"), entry("src", "directory"), entry("Makefile"), entry("assets", "directory")];

    assert.deepEqual(ordered(listed).map((one) => one.name), ["assets", "src", "Makefile", "report.md"]);
    // Sorting for reading must not sort the array the server's `next_after`
    // came from, or the second page starts from a different place than the
    // cursor says.
    assert.deepEqual(listed.map((one) => one.name), ["report.md", "src", "Makefile", "assets"]);
});

test("build output is real and never what you came for", () => {
    assert.equal(isNoise(entry("node_modules", "directory")), true);
    assert.equal(isNoise(entry(".git", "directory")), true);
    assert.equal(isNoise(entry(".env")), true);
    assert.equal(isNoise(entry("report.md")), false);
});

test("a file with no extension is the commonest thing an agent writes", () => {
    // Dockerfile, Makefile, README. Treating "no dot" as binary left exactly
    // those unopenable.
    assert.equal(viewerFor("Dockerfile"), "text");
    assert.equal(viewerFor("README"), "text");
    assert.equal(viewerFor("notes.md"), "markdown");
    assert.equal(viewerFor("chart.PNG"), "image");
    assert.equal(viewerFor("run.log"), "text");
    assert.equal(viewerFor("archive.zip"), "other");
    // A dotfile is its own name, not an extension: `.env` is not a file of
    // type "env" that happens to start with a dot.
    assert.equal(viewerFor(".gitignore"), "text");
});

test("a size is rounded to something a person reads", () => {
    assert.equal(readableSize(0), "0 B");
    assert.equal(readableSize(900), "900 B");
    assert.equal(readableSize(2048), "2 KB");
    assert.equal(readableSize(5 * 1024 * 1024), "5.0 MB");
    assert.equal(readableSize(-1), "");
});

test("one verb for the display, because it is one click", () => {
    assert.equal(screenSay("asleep", "asleep").action, "wake");
    // Whether a browser is already up or not, attaching is the same act — the
    // socket starts one when there is none. Two labels for it was two names
    // for one thing, and "open a browser" was the worse of them.
    assert.equal(screenSay("browsing", "running").action, "show");
    assert.equal(screenSay("awake", "stopped").action, "show");
});

test("a screen that cannot exist is not offered, and does not pretend", () => {
    // A fabric that cannot reach a port has no screen at all. Offering to open
    // one would be a button whose only outcome is a refusal.
    const cannot = screenSay("awake", "unsupported");

    assert.equal(cannot.action, null);
    assert.match(cannot.note, /no screen/);
    assert.equal(cannot.headline, "Awake");
});

test("a stopped computer offers wake without inventing why it stopped", () => {
    const state = screenSay("asleep", undefined);
    assert.equal(state.action, "wake");
    assert.equal(state.headline, "Asleep");
    assert.doesNotMatch(state.note, /quarter|idle|still there/);
});

test("a close code is read, not collapsed into 'disconnected'", () => {
    // RFB's own disconnect event reports only whether the close was clean,
    // which cannot tell "no browser is running yet" — worth retrying, a
    // teammate may start one — from "your session ended", where retrying with
    // the same expired cookie can never succeed.
    assert.equal(stateFromClose(4401), "signed-out");
    assert.equal(stateFromClose(4403), "refused");
    assert.equal(stateFromClose(4409), "no-browser");
    assert.equal(stateFromClose(4422), "unsupported");
    assert.equal(stateFromClose(4426), "stale-image");
    assert.equal(stateFromClose(1006), "lost");
});

test("only the failures something else can fix stop retrying", () => {
    // An allowlist without this app on it, an ended session, a fabric that
    // cannot do this, an image with no relay: retrying any of them repeats the
    // same refusal while telling somebody otherwise.
    assert.equal(isSettled("refused"), true);
    assert.equal(isSettled("signed-out"), true);
    assert.equal(isSettled("unsupported"), true);
    assert.equal(isSettled("stale-image"), true);
    // A browser the agent has not started yet, and a dropped connection, are
    // both worth another go.
    assert.equal(isSettled("no-browser"), false);
    assert.equal(isSettled("lost"), false);
});

test("the backoff grows, holds a ceiling, and is never in lockstep", () => {
    // Jittered so a sandbox restart does not have every open pane retry on the
    // same tick; floored at half so a "delay" is always a delay.
    for (const attempt of [0, 1, 2, 3, 12]) {
        const delay = retryDelay(attempt);
        assert.ok(delay > 0, "attempt " + attempt + " waited " + delay);
        assert.ok(delay <= 30_000, "attempt " + attempt + " waited " + delay);
    }
    assert.ok(retryDelay(12) > retryDelay(0));
});

test("the socket names the conversation, and carries a token only when there is one", () => {
    const url = new URL(socketUrl("https://api.example.test", { mode: "view", conversationId: "c-1" }));

    assert.equal(url.protocol, "wss:");
    assert.equal(url.pathname, "/workspace/browser/view");
    assert.equal(url.searchParams.get("mode"), "view");
    assert.equal(url.searchParams.get("conversation"), "c-1");
    // A cookie session passes nothing: served beside the API, it rides the
    // handshake on its own and a credential in the URL would buy nothing.
    assert.equal(url.searchParams.get("access_token"), null);

    assert.equal(new URL(socketUrl("http://api.localhost", { mode: "view" })).protocol, "ws:");
    assert.equal(
        new URL(socketUrl("https://api.example.test/", { mode: "view" })).searchParams.get("conversation"),
        null,
    );
});

test("a bearer session reaches the screen too, because a handshake takes no header", () => {
    // The whole of the bug this covers. On `localhost` the API's cookie is
    // cross-site and never sent, which is why `/connect` and the bearer token
    // exist — and this socket was the one call in the app that used neither,
    // so the screen closed 4401 while every request beside it worked. A
    // browser cannot set a header on a WebSocket handshake, so the query is
    // the only place it can go; the API reads bearer, then cookie, then this.
    const url = new URL(socketUrl("https://api.example.test", {
        mode: "control",
        origin: "https://app.example.test",
        accessToken: "tok-abc",
    }));

    assert.equal(url.searchParams.get("access_token"), "tok-abc");
    assert.equal(url.searchParams.get("origin"), "https://app.example.test");
    // Empty is not a token. A blank slot would otherwise send `access_token=`
    // and be read as a credential that could not be resolved.
    assert.equal(
        new URL(socketUrl("https://api.example.test", { mode: "view", accessToken: "" }))
            .searchParams.get("access_token"),
        null,
    );
});

test("a sign-in asks to drive, and names the site rather than the conversation", () => {
    // Driving is the whole point: the person is going to type a password. The
    // relay enforces the mode itself, message by message, so this string is a
    // request rather than a promise — but a pane that asked to watch would be
    // asking for a screen that drops every keystroke.
    const url = new URL(socketUrl("https://api.example.test", {
        mode: "control",
        origin: "https://app.example.test",
        conversationId: "c-1",
    }));

    assert.equal(url.searchParams.get("mode"), "control");
    assert.equal(url.searchParams.get("origin"), "https://app.example.test");
    // One or the other, never both: the site is what the server resolves the
    // session from, and a conversation beside it would be a second answer to a
    // question that has one.
    assert.equal(url.searchParams.get("conversation"), null);
});

test("the host shown over a password field is the page, not the request", () => {
    // A sign-in is a chain of redirects by design. A header pinned to the
    // requested origin kept naming the first site, with its padlock, above a
    // page served by another one — on the one screen whose whole job is "type
    // your password here".
    const hop = whereabouts("https://app.example.test", "https://login.identity.test/authorize");

    assert.equal(hop.host, "login.identity.test");
    assert.equal(hop.elsewhere, true);
    assert.equal(hop.secure, true);
    // Somewhere else is not arrived, and an identity-provider hop is not a
    // fault — it is said plainly rather than hidden.
    assert.equal(hop.arrived, false);

    const there = whereabouts("https://app.example.test", "https://app.example.test/login");
    assert.equal(there.elsewhere, false);
    assert.equal(there.arrived, true);
});

test("a host is parsed, because that is what makes it worth trusting", () => {
    // Both of these read as the right site under a naive prefix strip, and
    // both are somebody else's server.
    assert.equal(whereabouts("https://app.example.test", "https://evil.test/#app.example.test").host, "evil.test");
    assert.equal(whereabouts("https://app.example.test", "https://app.example.test@evil.test/").host, "evil.test");
    // Not TLS is worth saying out loud on this screen and nowhere else.
    assert.equal(whereabouts("http://intranet.test", "http://intranet.test/login").secure, false);
});

test("a browser that has not answered has not arrived", () => {
    // No page yet is a browser that is still opening, not one that went
    // somewhere else. Reported as not arrived, which is what it is — and the
    // pane says "opening…" rather than painting a blank browser and leaving
    // somebody to guess whether it is working, finished or broken.
    const cold = whereabouts("https://app.example.test", null);

    assert.equal(cold.host, "app.example.test");
    assert.equal(cold.arrived, false);
    assert.equal(cold.elsewhere, false);
});

test("signing in and the site agreeing are two different claims", () => {
    // Whether the site stopped asking is a guess the server makes by looking.
    // Reporting it as the answer would call somebody a liar about their own
    // password, so the run carries on either way and the warning is only that
    // they may be asked again.
    assert.match(outcomeSay(true, true).note, /will not be asked again/);
    assert.match(outcomeSay(true, false).note, /may be asked again/);
    assert.equal(outcomeSay(true, false).headline, "Signed in");
    // And "I could not" is an answer too: it is what lets a run end rather
    // than sit waiting on somebody who has already given up.
    assert.match(outcomeSay(false, false).note, /will not wait/);
});

function login(over: Partial<WebLogin> = {}): WebLogin {
    return { site: "example.test", cookie_count: 3, expires: null, signed_in: true, ...over };
}

test("a session cookie is not a login about to lapse", () => {
    // No expiry means the browser drops them when it stops — and this browser
    // is not in the habit of stopping, because its profile lives in the durable
    // home. Reading null as "expiring now" would tell somebody to go and sign
    // in again for no reason.
    const now = new Date("2026-09-20T12:00:00Z");

    assert.equal(lastsFor(login({ expires: null }), now), "for as long as the browser runs");
    assert.equal(lastsFor(login({ expires: "2026-09-21T12:00:00Z" }), now), "until tomorrow");
    assert.equal(lastsFor(login({ expires: "2026-10-04T12:00:00Z" }), now), "for another 14 days");
    assert.match(lastsFor(login({ expires: "2026-12-20T12:00:00Z" }), now), /for another 3 months/);
    assert.equal(lastsFor(login({ expires: "2026-09-01T12:00:00Z" }), now), "already lapsed");
    // A date the browser could not parse says nothing rather than "Invalid
    // Date", which is the sort of thing that ships.
    assert.equal(lastsFor(login({ expires: "not a date" }), now), "");
});

test("how long it lasts leads, and the cookies are the footnote", () => {
    // Nobody came here to count cookies. The count is a rough sense of scale
    // and it is said second; a health check is what it is not.
    const now = new Date("2026-09-20T12:00:00Z");

    assert.equal(loginNote(login({ cookie_count: 1 }), now), "Kept for as long as the browser runs · 1 cookie");
    assert.equal(loginNote(login({ cookie_count: 9 }), now), "Kept for as long as the browser runs · 9 cookies");
    assert.equal(loginNote(login({ cookie_count: 2, expires: "nonsense" }), now), "2 cookies");
});

test("a cookie domain is not a login, and the list stops claiming it is", () => {
    // A browser collects a domain per site *visited*. Under one heading
    // reading "sites this browser is signed in to", the font CDN and the video
    // somebody watched once were being counted as logins beside the two sites
    // this pane exists for.
    const mixed = groupSites([
        login({ site: "github.test", signed_in: true }),
        login({ site: "doubleclick.test", signed_in: false }),
        login({ site: "mail.test", signed_in: true }),
    ]);

    assert.deepEqual(mixed.signedIn.map((site) => site.site), ["github.test", "mail.test"]);
    assert.deepEqual(mixed.other.map((site) => site.site), ["doubleclick.test"]);
    assert.equal(mixed.split, true);
});

test("the split only appears when it says something", () => {
    // The mark begins empty on every profile that predates it, so a list that
    // hid four real sites behind a collapsed "other" because nobody had
    // answered a sign-in yet would be worse than the flat list it replaced.
    const unmarked = groupSites([login({ signed_in: false }), login({ site: "b.test", signed_in: false })]);
    assert.equal(unmarked.split, false);
    assert.equal(unmarked.signedIn.length, 0);

    // And a profile where every site was answered for has nothing to divide
    // either, so it stays one list.
    assert.equal(groupSites([login(), login({ site: "b.test" })]).split, false);
    assert.equal(groupSites([]).split, false);
});

test("nobody saying so is not the same as not being signed in", () => {
    // `signed_in` records that somebody answered "yes, I signed in" to a
    // request for this site. The cookies cannot say it on their own — a real
    // profile held two for a site somebody was signed in to and six for one
    // that had merely had a video played on it — so the mark is shown where it
    // is true and nothing is claimed where it is not.
    assert.equal(saidSoFor(login({ signed_in: true })), true);
    assert.equal(saidSoFor(login({ signed_in: false })), false);
});
