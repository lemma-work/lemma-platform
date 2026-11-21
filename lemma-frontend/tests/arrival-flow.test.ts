import test from "node:test";
import assert from "node:assert/strict";
import { nextStep, stepsFor } from "../src/arrival/steps.ts";
import { JOBS, OTHER_LIMIT, asksWorkOn, stillAsksWork, workAnswer, type JobId } from "../src/arrival/work.ts";

/** The way in is asked one thing at a time: your name, then the Lemma says
 *  hello, then what you do and its apps, setting them up, and ready. */

test("a first way in asks the name before the Lemma says hello, then the apps", () => {
    assert.deepEqual(stepsFor(), ["name", "lemma", "apps", "reach", "ready"]);
});

test("their company's organization is asked about after the name and before the Lemma", () => {
    assert.deepEqual(stepsFor({ team: true }), ["name", "team", "lemma", "apps", "reach", "ready"]);
});

test("an invitation accepted from Home skips the name, and the apps when nothing is asked", () => {
    assert.deepEqual(stepsFor({ name: false, apps: true }), ["lemma", "apps", "reach", "ready"]);
    assert.deepEqual(stepsFor({ name: false, apps: false }), ["lemma"]);
});

test("the step after the last is none", () => {
    const steps = stepsFor();
    assert.equal(nextStep(steps, "name"), "lemma");
    assert.equal(nextStep(steps, "apps"), "reach");
    assert.equal(nextStep(steps, "ready"), null);
});

test("the Lemma says what it is doing in a word, and a new one is just made", async () => {
    const { statusAt } = await import("../src/arrival/steps.ts");
    assert.equal(statusAt("name", { made: true }), "Listening");
    assert.equal(statusAt("lemma", { made: true }), "Just made");
    assert.equal(statusAt("lemma", { made: false }), "You're in");
    assert.equal(statusAt("reach", { made: true }), "Working");
});

test("each picked app is read from the server's queue by the folder it came from", async () => {
    const { runOf } = await import("../src/arrival/install-state.ts");
    const plugin = (slug: string, path: string | null) => ({ slug, install: path ? `/import/github/lemma-work/awesome-lemma?path=${path}` : null });
    const picks = [plugin("mail", "install/mail"), plugin("meetings", "install/meetings"), plugin("today", "install/today"), plugin("deck", null)];
    // Before the server answers, every pick waits; one with nowhere to install from asks for its page.
    assert.deepEqual(Object.values(runOf(picks, null, null)).map((one) => one.kind), ["waiting", "waiting", "waiting", "asks"]);
    const server = {
        install_id: "i1",
        finished: false,
        plugins: [
            { name: "Meetings", path: "install/meetings", position: 1, state: "INSTALLING", progress: { done: 3, total: 12 }, current_step: { kind: "APP", name: "meetings" } },
            { name: "Mail", path: "install/mail", position: 0, state: "INSTALLED", app: "inbox", progress: { done: 20, total: 20 } },
            { name: "Today", path: "install/today", position: 2, state: "FAILED", reason: "App build failed" },
        ],
    };
    const rows = runOf(picks, server, null);
    assert.deepEqual([rows.mail.kind, rows.mail.progress, rows.mail.app], ["added", 1, "inbox"]);
    assert.deepEqual([rows.meetings.kind, rows.meetings.progress, rows.meetings.step], ["adding", 0.25, "APP"]);
    assert.deepEqual([rows.today.kind, rows.today.reason], ["failed", "App build failed"]);
    // The request itself refused: every pick says why.
    assert.equal(runOf(picks, null, "Daily import limit reached").mail.reason, "Daily import limit reached");
});

test("what the app being set up is doing is said as the Lemma would say it", async () => {
    const { stepWords } = await import("../src/arrival/install-state.ts");
    assert.equal(stepWords("APP"), "Putting its app together");
    assert.equal(stepWords("table"), "Making space for its information");
    assert.equal(stepWords("FUNCTION_GRANTS"), "Learning how it works");
    assert.equal(stepWords(null), "Getting it ready");
    for (const kind of ["TABLE", "FUNCTION", "APP", "WORKFLOW", "CONNECTOR", "X"]) assert.doesNotMatch(stepWords(kind), /\bpod\b|teammate|bundle|import/i);
});

test("the Lemma says hello by name, and says what a Lemma is to a team or to one person", async () => {
    const { hello, lemmaIs } = await import("../src/arrival/words.ts");
    assert.equal(hello(" Rita "), "Nice to meet you, Rita.");
    assert.equal(hello(""), "Welcome.");
    assert.match(lemmaIs(true, "Northwind"), /shared place .* Everyone at Northwind will be in this one\./);
    assert.match(lemmaIs(false, "Sam’s workspace"), /yours alone/);
    assert.doesNotMatch(lemmaIs(false, "x"), /shared place/);
    for (const said of [lemmaIs(true, "Northwind"), lemmaIs(false, "x")]) assert.doesNotMatch(said, /\bpod\b|teammate/i);
});

test("nine jobs, in the order they are shown", () => {
    assert.deepEqual(JOBS.map((job) => job.id), [
        "SALES", "MARKETING", "PRODUCT", "FOUNDER", "OPERATIONS", "HR", "PROJECT_MANAGEMENT", "ENGINEERING", "SUPPORT",
    ]);
});

test("an answer keeps the screen's order and the person's own words only when there are some", () => {
    const picked = new Set<JobId>(["SUPPORT", "SALES"]);
    assert.deepEqual(workAnswer(picked, "  "), { jobs: ["SALES", "SUPPORT"], other: null });
    assert.deepEqual(workAnswer(new Set(), "  run a bakery "), { jobs: [], other: "run a bakery" });
    assert.equal(workAnswer(new Set(), "x".repeat(OTHER_LIMIT + 40)).other?.length, OTHER_LIMIT);
});

test("skipping is an answer: nothing picked, nothing written", () => {
    assert.deepEqual(workAnswer(new Set(), ""), { jobs: [], other: null });
});

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-11-17T12:00:00Z");

test("a new account that has not answered is asked", () => {
    assert.equal(stillAsksWork({ work: null, created_at: new Date(NOW - 2 * DAY).toISOString() }, NOW), true);
});

test("an answer of any kind, skipping included, ends the question", () => {
    assert.equal(stillAsksWork({ work: { jobs: [], other: null, answered_at: "2026-11-16T00:00:00Z" }, created_at: new Date(NOW - DAY).toISOString() }, NOW), false);
});

test("people who were here long before the question are not stopped by it", () => {
    assert.equal(stillAsksWork({ work: null, created_at: new Date(NOW - 60 * DAY).toISOString() }, NOW), false);
});

test("a server that does not ask the question never stops anybody", () => {
    assert.equal(stillAsksWork({ created_at: new Date(NOW - DAY).toISOString() }, NOW), false);
    assert.equal(stillAsksWork(undefined, NOW), false);
    assert.equal(stillAsksWork({ work: null, created_at: "not a date" }, NOW), false);
});

test("somebody who missed the question is asked it on Home, never over a link they followed", () => {
    assert.equal(asksWorkOn("today"), true);
    // A chat opened from WhatsApp, an app from an email, a Lemma a colleague
    // sent: what they came for opens, and the question waits for Home.
    for (const view of ["chats", "app", "outcome", "outcomes", "apps", "needs", "people"]) {
        assert.equal(asksWorkOn(view), false, view);
    }
});

test("every door asks the server for a first workspace the same way", async () => {
    const { firstWorkspaceWanted } = await import("../src/arrival/first-workspace.ts");
    assert.deepEqual(firstWorkspaceWanted({ local: false }), {});
    assert.deepEqual(firstWorkspaceWanted({ own: true, local: false }), { ownOrganization: true });
    // On a local install nobody else can reach the server yet, so it is never
    // opened to a whole email domain -- from the web or a plugin's page.
    assert.deepEqual(firstWorkspaceWanted({ local: true }), { team: false });
    assert.deepEqual(firstWorkspaceWanted({ own: true, local: true }), { ownOrganization: true, team: false });
});

test("the plugins offered after the job question fit what they said they do", async () => {
    const { suggestPlugins, SUGGESTED } = await import("../src/arrival/suggest.ts");
    const plugin = (slug: string, position: number, jobs: string[], install: string | null = `/import/x?path=install/${slug}`) => ({ slug, position, jobs, install });
    const built = [
        plugin("mail", 1, ["SALES", "FOUNDER", "SUPPORT"]),
        plugin("meetings", 2, ["SALES", "PRODUCT"]),
        plugin("tasks", 5, ["ENGINEERING", "PROJECT_MANAGEMENT"]),
        plugin("deals", 6, ["SALES", "FOUNDER"]),
        plugin("brand", 9, ["MARKETING"]),
        plugin("unpublished", 3, ["ENGINEERING"], null),
    ];
    // Most of their jobs first, then the catalog's own order.
    assert.deepEqual(suggestPlugins(["SALES", "FOUNDER"], built).map((one) => one.slug), ["mail", "deals", "meetings"]);
    // Only what fits, even when fewer than three do; never one that cannot be added yet.
    assert.deepEqual(suggestPlugins(["ENGINEERING"], built).map((one) => one.slug), ["tasks"]);
    // Skipped, or their own words only: the first few in the catalog's order.
    assert.deepEqual(suggestPlugins([], built).map((one) => one.slug), ["mail", "meetings", "tasks"]);
    assert.equal(suggestPlugins(["SALES"], built).length <= SUGGESTED, true);
});

test("each job lists every plugin for it, and picks survive moving between jobs", async () => {
    const { pluginsForJob, picksThatCount } = await import("../src/arrival/suggest.ts");
    const plugin = (slug: string, position: number, jobs: string[], install: string | null = `/import/x?path=install/${slug}`) => ({ slug, position, jobs, install });
    const built = [
        plugin("mail", 1, ["SALES", "FOUNDER"]),
        plugin("meetings", 2, ["SALES", "PRODUCT"]),
        plugin("leads", 8, ["SALES", "MARKETING"]),
        plugin("brand", 9, ["MARKETING"]),
        plugin("unpublished", 3, ["MARKETING"], null),
    ];
    // A job's tab holds all of its plugins, the ones fitting their other jobs too first.
    assert.deepEqual(pluginsForJob("SALES", ["SALES", "MARKETING"], built).map((one) => one.slug), ["leads", "mail", "meetings"]);
    assert.deepEqual(pluginsForJob("MARKETING", ["SALES", "MARKETING"], built).map((one) => one.slug), ["leads", "brand"]);
    // Picked under sales, then under marketing: both go in, bases first.
    const picked = new Set(["brand", "mail"]);
    assert.deepEqual(picksThatCount(picked, ["SALES", "MARKETING"], built).map((one) => one.slug), ["mail", "brand"]);
    // Marketing turned off: brand is no longer on screen, so it does not go in.
    assert.deepEqual(picksThatCount(picked, ["SALES"], built).map((one) => one.slug), ["mail"]);
    // No jobs (their own words only): everything is listed, so every pick counts.
    assert.deepEqual(picksThatCount(picked, [], built).map((one) => one.slug), ["mail", "brand"]);
    // Never one that cannot be added yet.
    assert.deepEqual(picksThatCount(new Set(["unpublished"]), ["MARKETING"], built), []);
});

test("adding a plugin from the way in opens its install page on the Lemma they land in", async () => {
    const { addHref } = await import("../src/arrival/suggest.ts");
    assert.equal(
        addHref({ slug: "mail", position: 1, jobs: [], install: "/import/github/lemma-work/awesome-lemma?path=install/mail" }),
        "/import/github/lemma-work/awesome-lemma?path=install/mail&destination=existing",
    );
    assert.equal(addHref({ slug: "x", position: 1, jobs: [], install: "/import/github/o/r" }), "/import/github/o/r?destination=existing");
    assert.equal(addHref({ slug: "x", position: 1, jobs: [], install: null }), null);
});

test("every job the question offers has a plugin in the catalog to suggest", async () => {
    const { BUILT } = await import("../src/site/plugins/catalog.ts");
    const { suggestPlugins } = await import("../src/arrival/suggest.ts");
    for (const job of JOBS) {
        assert.ok(suggestPlugins([job.id], BUILT).length > 0, `nothing to offer for ${job.id}`);
    }
});

test("a workspace the server left unnamed takes the name its person gives", async () => {
    const { workspaceNameFor, UNNAMED_WORKSPACE } = await import("../src/arrival/workspace-name.ts");
    assert.equal(workspaceNameFor(UNNAMED_WORKSPACE, " Sam "), "Sam’s workspace");
    // No name given, or a workspace that already has one: left alone.
    assert.equal(workspaceNameFor(UNNAMED_WORKSPACE, "  "), null);
    assert.equal(workspaceNameFor("Northwind", "Sam"), null);
});

test("a built plugin's install address is the source the import reads", async () => {
    const { sourceOfInstall } = await import("../src/arrival/install-plan.ts");
    assert.deepEqual(sourceOfInstall("/import/github/lemma-work/awesome-lemma?path=install/mail"), { owner: "lemma-work", repo: "awesome-lemma", path: "install/mail" });
    assert.deepEqual(sourceOfInstall("/import/github/o/r"), { owner: "o", repo: "r", path: null });
    assert.equal(sourceOfInstall("/templates/mail"), null);
    assert.equal(sourceOfInstall("/import/github/o/r?path=../secrets"), null);
});

test("Gmail or Outlook is one account to connect, shared by every app that reads it", async () => {
    const { accountRows } = await import("../src/arrival/accounts.ts");
    const pod = (slug: string, connectorId: string) => ({ slug, connectorId }) as never;
    const titles: Record<string, string> = { gmail: "Gmail", outlook: "Outlook", gcal: "Google Calendar", slack: "Slack" };
    const asked = [pod("gmail", "gmail"), pod("outlook", "outlook"), pod("calendar", "gcal"), pod("team-chat", "slack")];
    const rows = accountRows(asked, new Set(["gmail", "outlook", "calendar", "team-chat"]), [{ slug: "mail", name: "Mail" }, { slug: "meetings", name: "Meetings" }], (id) => titles[id] ?? "");
    assert.deepEqual(rows.map((row) => [row.called, row.forApps, row.connect.map((one: { slug: string }) => one.slug)]), [
        ["Gmail or Outlook", ["Mail"], ["gmail", "outlook"]],
        ["Google Calendar or Outlook", ["Meetings"], ["calendar", "outlook"]],
        ["Slack", [], ["team-chat"]],
    ]);
    // Connected once is connected for every app that reads it.
    const after = accountRows(asked, new Set(["outlook", "calendar"]), [{ slug: "mail", name: "Mail" }], (id) => titles[id] ?? "");
    assert.equal(after[0].done, true);
});

test("apps sit in an arc over the Lemma's head, and only as many as fit", async () => {
    const { slotAngles, emptySlots, pickedSlots, installSlots, MAX_SLOTS } = await import("../src/arrival/stage-model.ts");
    assert.deepEqual(slotAngles(0), []);
    assert.deepEqual(slotAngles(2), [222, 318]);
    for (const n of [1, 2, 3, 4]) assert.ok(slotAngles(n).every((deg) => deg > 180 && deg < 360), "over its head");
    assert.equal(slotAngles(9).length, MAX_SLOTS);
    // Just made: two empty spots. Picking fills them; none picked keeps them empty.
    assert.deepEqual(emptySlots().map((one) => one.state), ["empty", "empty"]);
    assert.deepEqual(pickedSlots([]).map((one) => one.state), ["empty", "empty"]);
    assert.deepEqual(pickedSlots([{ slug: "mail" }, { slug: "meetings", landing: true }]).map((one) => [one.slug, one.state, Boolean(one.landing)]), [["mail", "picked", false], ["meetings", "picked", true]]);
    // Going in: in, under way, next, and one that stopped.
    assert.deepEqual(
        installSlots([{ slug: "mail", kind: "added", progress: .2 }, { slug: "meetings", kind: "adding", progress: .4 }, { slug: "today", kind: "waiting", progress: 0 }, { slug: "tasks", kind: "failed", progress: .6 }])
            .map((one) => [one.state, one.progress]),
        [["in", 1], ["going", .4], ["next", 0], ["stopped", .6]],
    );
});

test("every place is shown: what works here first, then what is missing and why", async () => {
    const { placesFrom } = await import("../src/arrival/reach.ts");
    const connectable = [
        { platform: "WHATSAPP", effort: "instant" },
        { platform: "TELEGRAM", effort: "account" },
        { platform: "SLACK", effort: "unavailable" },
        { platform: "RESEND", effort: "unavailable" },
    ];
    const surfaces = [{ platform: "RESEND", handle: "personal-k3m4", email: "personal-k3m4@lemma.email", active: true }];
    // One person: WhatsApp a tap away, the mailbox on; Telegram only with a bot of their own later; no Slack app here.
    assert.deepEqual(placesFrom({ connectable, surfaces, slack: false, team: false }), [
        { key: "WHATSAPP", state: "off", handle: null },
        { key: "EMAIL", state: "on", handle: "personal-k3m4@lemma.email" },
        { key: "TELEGRAM", state: "missing", handle: null, why: "later" },
        { key: "SLACK", state: "missing", handle: null, why: "server" },
    ]);
    // A team leads with Slack where its app can be installed.
    assert.deepEqual(placesFrom({ connectable, surfaces, slack: true, team: true }).map((one) => [one.key, one.state]), [["SLACK", "off"], ["EMAIL", "on"], ["WHATSAPP", "off"], ["TELEGRAM", "missing"]]);
    // Already connected counts as on, with where to write to it.
    assert.deepEqual(placesFrom({ connectable, surfaces: [{ platform: "WHATSAPP", handle: "+1 555 014 2288" }], slack: false, team: false })[0], { key: "WHATSAPP", state: "on", handle: "+1 555 014 2288" });
    // Nothing set up on this server: all four still shown, each saying so.
    assert.deepEqual(placesFrom({ connectable: [], surfaces: [], slack: false, team: false }).map((one) => [one.key, one.state, one.why]), [
        ["WHATSAPP", "missing", "server"], ["EMAIL", "missing", "server"], ["TELEGRAM", "missing", "server"], ["SLACK", "missing", "server"],
    ]);
});

test("a hello opens the chat with the Lemma, and the first one heard is the one that counts", async () => {
    const { helloLink, heardOn } = await import("../src/arrival/reach.ts");
    assert.equal(helloLink("WHATSAPP", "+1 555 014 2288", "Personal"), "https://wa.me/15550142288?text=Hi%20Personal");
    assert.equal(helloLink("TELEGRAM", "@LemmaHelloBot", "Personal"), "https://t.me/LemmaHelloBot");
    assert.equal(helloLink("EMAIL", "personal@lemma.email", "Personal"), null);
    const since = Date.parse("2026-11-20T10:00:00Z");
    const chats = [
        { origin: { kind: "chat" }, activityAt: "2026-11-20T10:05:00Z" },
        { origin: { kind: "channel", platform: "TELEGRAM" }, activityAt: "2026-11-20T10:04:00Z" },
        { origin: { kind: "channel", platform: "WHATSAPP" }, activityAt: "2026-11-20T09:59:00Z" },
    ];
    // Telegram is not on, and the WhatsApp chat is older than the step.
    assert.equal(heardOn(chats, ["WHATSAPP"], since), null);
    assert.equal(heardOn(chats, ["WHATSAPP", "TELEGRAM"], since), "TELEGRAM");
});
