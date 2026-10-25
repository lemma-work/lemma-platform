import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { installArrival } from "../src/areas/more/install/arrival";
import { key } from "../src/web-shims/session-storage";
import {
    NEW, PUBLIC_TEMPLATES, README_LIMIT, actionTone, answersOf, contentsWords, coverUrl, cutReadme, doneOf,
    footFor, githubHref, includedLine, isRepositoryName, isStopped, isWorking, heldStore, missingAnswer, personalName,
    phoneGroups, progressFill, progressWords, remixName, remixSourceOf, remixWords, shownOf, stepAt, stepStateOf,
    stoppedWords, templateOf, templateRoute, variableLabel, whoOf, withResolvedLinks,
} from "../src/areas/more/install/model";
import { templateGitHubSource } from "@web/site/templates/catalog";
import type { ImportStatus, ImportStatusResponse, PlanStep, VariableSpec } from "@web/site/import-types";
import { PENDING_MS, forgetInstall, pendingInstall, rememberInstall } from "@web/site/import/pending-install";
import { statusLine } from "@web/site/import/plan";

/** Templates, installing from GitHub and remixing (PARITY.md, G): the web's
 *  modules copied under `install/web/`, held to the web, and the phone's own
 *  reading on top (`install/model.ts`). */

const mobile = fileURLToPath(new URL("..", import.meta.url));
const here = mobile + "src/areas/more/install/";

/* ── fixtures ────────────────────────────────────────────────────────── */

function step(index: number, kind: string, name: string, extra: Partial<PlanStep> = {}): PlanStep {
    return { index, kind, name, action: "CREATE", destructive: false, detail: {}, status: "PENDING", error: null, ...extra };
}

function job(status: ImportStatus, extra: Partial<ImportStatusResponse> = {}): ImportStatusResponse {
    return {
        import_id: "imp-1",
        pod_id: "pod-1",
        status,
        source_kind: "GITHUB",
        plan: null,
        progress: { done: 0, total: 0 },
        events_url: "",
        error: null,
        error_code: null,
        retryable: false,
        warnings: [],
        connect_url: null,
        ...extra,
    };
}

const free: VariableSpec = { name: "app_slug", kind: "free", description: null, required: true, default: null };
const member: VariableSpec = { name: "owner", kind: "pod_member", description: null, required: true, default: null };

/* ── pending install ─────────────────────────────────────────────────── */

describe("the install waiting to be finished", () => {
    it("keeps the web's rules over the phone's keystore", () => {
        const written: [string, string | null][] = [];
        const store = heldStore({}, (name, value) => written.push([name, value]));
        const now = 1_000_000_000;
        rememberInstall(store, { path: "/import/github/acme/frontdesk", title: "Frontdesk" }, now);
        expect(pendingInstall(store, now + 60_000)).toEqual({ path: "/import/github/acme/frontdesk", title: "Frontdesk" });
        /* A keystore key allows only [A-Za-z0-9._-]. */
        expect(written[0][0]).toBe("lemma.install-pending");
        expect(written[0][0]).toMatch(/^[A-Za-z0-9._-]+$/);
        /* Twenty-four hours, and never from the future. */
        expect(pendingInstall(store, now + PENDING_MS + 1)).toBeNull();
        expect(pendingInstall(store, now - 1)).toBeNull();
        forgetInstall(store);
        expect(pendingInstall(store, now)).toBeNull();
        expect(written[written.length - 1]).toEqual(["lemma.install-pending", null]);
    });

    it("keeps only an install's own path", () => {
        const store = heldStore({});
        rememberInstall(store, { path: "/remix?source=https://example.com", title: "x" }, 5);
        expect(pendingInstall(store, 5)).toBeNull();
        rememberInstall(store, { path: "/import/github/acme/frontdesk", title: "Frontdesk" }, 5);
        expect(pendingInstall(store, 6)?.title).toBe("Frontdesk");
    });

    it("keeps the visit an install opens quiet, for as long as the app is open", () => {
        expect(installArrival.held()).toBe(false);
        installArrival.mark();
        expect(installArrival.held()).toBe(true);
    });

    it("reads what the keystore held at launch", () => {
        const store = heldStore({ [key("install-pending")]: JSON.stringify({ path: "/import/github/a/b", title: "B", at: 10 }) });
        expect(pendingInstall(store, 20)).toEqual({ path: "/import/github/a/b", title: "B" });
        expect(pendingInstall(heldStore({ [key("install-pending")]: "{not json" }), 20)).toBeNull();
    });
});

/* ── the catalogue ───────────────────────────────────────────────────── */

describe("the templates", () => {
    it("open their own install page, at the web's path", () => {
        for (const template of PUBLIC_TEMPLATES) {
            const { owner, repo } = templateGitHubSource(template);
            expect(templateRoute(template)).toBe(`/import/github/${owner}/${repo}`);
            expect(templateOf(owner.toUpperCase(), repo)).toBe(template);
        }
    });

    it("take their cover from the web app", () => {
        const template = PUBLIC_TEMPLATES[0];
        expect(coverUrl("https://lemma.example/", template)).toBe(`https://lemma.example/templates/${template.slug}/social-preview.jpg`);
    });

    it("say which repositories are outside the catalogue", () => {
        expect(templateOf("acme", "notes")).toBeNull();
        expect(isRepositoryName("frontdesk")).toBe(true);
        expect(isRepositoryName("a/b")).toBe(false);
        expect(isRepositoryName("")).toBe(false);
        expect(githubHref("acme", "notes")).toBe("https://github.com/acme/notes");
    });
});

/* ── the install page ────────────────────────────────────────────────── */

describe("what the install page shows", () => {
    const readme = [
        "# Notes",
        "",
        "A small place to keep notes and find them again later.",
        "",
        "## Use",
        "",
        "See [the guide](./docs/guide.md), [setup](#setup) and ![shot](img/shot.png).",
        "",
        "```bash",
        "# not a heading",
        "cat ./local.md",
        "```",
        "",
        "[ref]: ./docs/ref.md",
    ].join("\n");

    it("names a repository outside the catalogue from its README", () => {
        const shown = shownOf("acme", "notes", null, { markdown: readme, branch: "HEAD" });
        expect(shown.title).toBe("Notes");
        expect(shown.lede).toBe("A small place to keep notes and find them again later.");
        /* The line under the name is not printed again as the first paragraph. */
        expect(shown.body).not.toContain("A small place");
        expect(shown.body?.startsWith("## Use")).toBe(true);
        expect(shown.cut).toBe(false);
    });

    it("points relative links at GitHub, and leaves code alone", () => {
        const body = shownOf("acme", "notes", null, { markdown: readme, branch: "HEAD" }).body ?? "";
        expect(body).toContain("[the guide](https://github.com/acme/notes/blob/HEAD/docs/guide.md)");
        expect(body).toContain("[setup](https://github.com/acme/notes#setup)");
        expect(body).toContain("![shot](https://raw.githubusercontent.com/acme/notes/HEAD/img/shot.png)");
        expect(body).toContain("[ref]: https://github.com/acme/notes/blob/HEAD/docs/ref.md");
        expect(body).toContain("cat ./local.md");
        expect(withResolvedLinks("[x](https://example.com/a)", "o", "r", "HEAD")).toBe("[x](https://example.com/a)");
    });

    it("prefers the catalogue's name and line, before the README and without it", () => {
        const template = PUBLIC_TEMPLATES.find((one) => one.slug === "frontdesk")!;
        const { owner, repo } = templateGitHubSource(template);
        expect(shownOf(owner, repo, template, undefined)).toEqual({ title: "Frontdesk", lede: template.description, body: null, cut: false });
        expect(shownOf(owner, repo, template, null).body).toBeNull();
        /* Nothing read, nothing in the catalogue: the slug as a name. */
        expect(shownOf("acme", "smart-notes", null, null).title).toBe("Smart Notes");
    });

    it("cuts a long README at a paragraph, never inside code", () => {
        const long = Array.from({ length: 40 }, (_, index) => `Paragraph ${index} ` + "words ".repeat(80)).join("\n\n");
        const { text, cut } = cutReadme(long, README_LIMIT);
        expect(cut).toBe(true);
        expect(text.length).toBeLessThanOrEqual(README_LIMIT);
        expect(text.endsWith("words")).toBe(true);
        const fenced = "Intro\n\n```\n" + "x\n".repeat(200) + "```\n\nAfter";
        expect(cutReadme(fenced, 100)).toEqual({ text: "Intro", cut: true });
        expect(cutReadme("short", 100)).toEqual({ text: "short", cut: false });
    });
});

/* ── who it is for ───────────────────────────────────────────────────── */

describe("who it is for", () => {
    it("names a first organization after the person, never their address", () => {
        expect(personalName({ name: "Ada Park", firstName: "Ada", email: "ada@acme.example" })).toBe("Ada's Personal");
        expect(personalName({ name: "ada.park", firstName: "ada.park", email: "ada.park@acme.example" })).toBe("Personal");
        expect(personalName({ name: "You", firstName: null, email: null })).toBe("Personal");
        expect(personalName(null)).toBe("Personal");
    });

    it("speaks of the new Lemma by its name, and an existing one by its own", () => {
        const lemmas = [{ id: "l1", name: "Support" }];
        expect(whoOf(NEW, "  Frontdesk ", null, lemmas)).toBe("Frontdesk");
        expect(whoOf(NEW, "Typed later", { name: "Frontdesk" }, lemmas)).toBe("Frontdesk");
        expect(whoOf("l1", "Frontdesk", null, lemmas)).toBe("Support");
        expect(whoOf("gone", "Frontdesk", null, lemmas)).toBe("your Lemma");
    });
});

/* ── the journey ─────────────────────────────────────────────────────── */

describe("the journey", () => {
    const steps = [
        step(0, "AGENT", "desk"),
        step(1, "APP", "ticket-queue"),
        step(2, "WORKFLOW", "sort_new_messages"),
        step(3, "SCHEDULE", "chase_unanswered"),
        step(4, "TABLE", "tickets", { action: "UPDATE", destructive: true }),
        step(5, "TABLE_DATA", "tickets"),
        step(6, "SURFACE", "whatsapp"),
        step(7, "AGENT_GRANTS", "desk"),
    ];

    it("is at Choose, Review or Install, and draws no steps once stopped", () => {
        expect(stepAt(null)).toBe(0);
        for (const status of ["QUEUED", "FETCHING", "PLANNING", "AWAITING_CONFIRMATION"] as const) expect(stepAt(job(status))).toBe(1);
        expect(stepAt(job("APPLYING"))).toBe(2);
        expect(stepAt(job("COMPLETED"))).toBe(3);
        for (const status of ["FAILED", "CANCELLED", "PARTIALLY_CANCELLED"] as const) {
            expect(stepAt(job(status))).toBeNull();
            expect(isStopped(status)).toBe(true);
        }
        expect(isWorking(job("APPLYING"))).toBe(true);
        expect(isWorking(job("AWAITING_CONFIRMATION"))).toBe(false);
        expect(isWorking(null)).toBe(false);
    });

    it("says at the page's foot where it is", () => {
        expect(footFor(null, "Frontdesk", "Frontdesk")).toEqual({ line: "Frontdesk", action: "Install" });
        expect(footFor(job("AWAITING_CONFIRMATION"), "Frontdesk", "Support")).toEqual({ line: "Ready for your review", action: "Review" });
        expect(footFor(job("APPLYING", { progress: { done: 3, total: 7 } }), "Frontdesk", "Support")).toEqual({ line: "Installing… 3 of 7", action: "Show" });
        expect(footFor(job("APPLYING"), "Frontdesk", "Support").line).toBe("Installing…");
        expect(footFor(job("COMPLETED"), "Frontdesk", "Support").line).toBe("Support is ready");
        expect(footFor(job("FAILED"), "Frontdesk", "Support").line).toBe("The installation stopped");
    });

    it("fills the bar only when the total is known", () => {
        expect(progressFill({ done: 0, total: 0 })).toBeNull();
        expect(progressFill({ done: 3, total: 6 })).toBe(0.5);
        expect(progressFill({ done: 9, total: 6 })).toBe(1);
        expect(progressWords(job("APPLYING", { progress: { done: 0, total: 0 } }))).toBeNull();
    });

    it("lists the parts by kind, the phone's way, grants folded in", () => {
        const groups = phoneGroups(steps);
        expect(groups.map((group) => group.label)).toEqual(["Agent", "App", "Workflow", "Automation", "Table", "Sample rows", "Way to reach it"]);
        expect(phoneGroups([step(0, "SURFACE", "a"), step(1, "SURFACE", "b")])[0].label).toBe("Ways to reach it");
        expect(contentsWords(groups)).toBe("1 agent, 1 app, 1 workflow, 1 automation, 1 table, sample rows and 1 way to reach it");
        expect(contentsWords(phoneGroups([step(0, "AGENT", "a")]))).toBe("1 agent");
        expect(includedLine("Frontdesk", "Frontdesk", phoneGroups([step(0, "AGENT", "a"), step(1, "APP", "b")]))).toBe("Everything Frontdesk brings: 1 agent and 1 app.");
        expect(includedLine("Support", "Frontdesk", [])).toBe("Support gets everything Frontdesk brings.");
    });

    it("marks each part as it installs, and what replaces something in amber", () => {
        expect(stepStateOf("DONE")).toEqual({ label: "Done", tone: "done" });
        expect(stepStateOf("RUNNING")).toEqual({ label: "Installing", tone: "running" });
        expect(stepStateOf("FAILED").tone).toBe("bad");
        expect(stepStateOf("SKIPPED").tone).toBe("quiet");
        expect(stepStateOf("PENDING").label).toBe("Waiting");
        expect(actionTone(steps[4])).toBe("amber");
        expect(actionTone(steps[0])).toBe("grey");
    });

    it("names a variable by its name: connectors are not variables any more", () => {
        expect(variableLabel(free)).toBe("App slug");
        expect(variableLabel(member)).toBe("Owner");
    });

    it("sends what the person did not touch as it is worth untouched", () => {
        const variables = [free, member, { ...free, name: "region", default: "eu" }];
        expect(answersOf(variables, {}, "Smart Inbox")).toEqual({ app_slug: "smart-inbox", owner: "", region: "eu" });
        expect(answersOf(variables, { app_slug: "mine" }, "Smart Inbox")).toMatchObject({ app_slug: "mine" });
        expect(missingAnswer([free], {}, "Smart Inbox")).toBe(false);
        /* A name that slugs to nothing has to be answered. */
        expect(missingAnswer([free], {}, "!!!")).toBe(true);
    });

    it("ends in the server's own words, or plainly", () => {
        expect(stoppedWords(job("FAILED", { error: "Repository acme/notes was not found or requires a connected GitHub account." }), "Support")).toBe("Repository acme/notes was not found or requires a connected GitHub account.");
        expect(stoppedWords(job("FAILED"), "Support")).toBe("Something went wrong while installing.");
        expect(stoppedWords(job("PARTIALLY_CANCELLED"), "Support")).toBe("What was already installed stays with Support.");
        expect(stoppedWords(job("CANCELLED"), "Support")).toBe("Nothing was added.");
    });

    it("opens the app it brought first, and the Lemma as the other way", () => {
        const done = doneOf("pod-1", steps, "Support", "Frontdesk");
        expect(done.heading).toBe("Support is ready");
        expect(done.line).toBe("Frontdesk is installed. It came with Ticket queue.");
        expect(done.open).toEqual({ label: "Open Ticket queue", href: "/app/pod-1/ticket-queue" });
        expect(done.other).toEqual({ label: "Or open Support instead", href: "/lemma/pod-1" });
        const plain = doneOf("pod-1", [step(0, "AGENT", "desk")], "Frontdesk", "Frontdesk");
        expect(plain.line).toBe("Say hello and give it its first job.");
        expect(plain.open).toEqual({ label: "Open Frontdesk", href: "/lemma/pod-1" });
        expect(plain.other).toBeNull();
    });
});

/* ── remix ───────────────────────────────────────────────────────────── */

describe("remix", () => {
    it("takes only an http or https address", () => {
        expect(remixSourceOf(null)).toEqual({ kind: "missing" });
        expect(remixSourceOf("  ")).toEqual({ kind: "missing" });
        expect(remixSourceOf("javascript:alert(1)")).toEqual({ kind: "bad", said: "javascript:alert(1)" });
        expect(remixSourceOf("not an address")).toEqual({ kind: "bad", said: "not an address" });
        expect(remixSourceOf("https://deals.apps.example.com/board")).toEqual({ kind: "ok", href: "https://deals.apps.example.com/board" });
    });

    it("leaves the web's sentence in the box, said the phone's way", () => {
        expect(remixWords("https://example.com/")).toBe("Help me rebuild or adapt this app for us: https://example.com/");
    });

    it("names the new Lemma after the app", () => {
        expect(remixName("https://deals.apps.example.com/")).toBe("Deals");
        expect(remixName("https://www.example.com/ticket-queue")).toBe("Ticket Queue");
        expect(remixName("https://app.example.com/")).toBe("New Lemma");
        expect(remixName("http://127.0.0.1:3000/")).toBe("New Lemma");
        expect(remixName("http://localhost:3000/index.html")).toBe("Index");
        expect(remixName("https://github.com/acme/frontdesk")).toBe("Frontdesk");
        expect(remixName("https://github.com/owner/ticket-queue.git")).toBe("Ticket Queue");
        expect(remixName("https://raw.githubusercontent.com/owner/drop/main/index.html")).toBe("Drop");
        expect(remixName("https://github.com/owner")).toBe("New Lemma");
    });
});

/* ── copy ────────────────────────────────────────────────────────────── */

describe("the words on these screens", () => {
    const BANNED = /teammate|\bpods?\b|workspace|\bchannels?\b|\bsurface|\bbot\b|assistant|\bmembers?\b|\busers?\b|—|!\s*$|\bOops/i;

    /** Every string literal and JSX text in the phone's own files, comments
     *  and code that is not words taken out. */
    function wordsIn(file: string): string[] {
        const text = readFileSync(file, "utf8")
            .replace(/\/\*[\s\S]*?\*\//g, "")
            .replace(/^\s*\/\/.*$/gm, "")
            .replace(/^import[^;]*;\n/gm, "");
        const literals = [...text.matchAll(/"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`|>([^<>{}\n]*[A-Za-z][^<>{}\n]*)</g)]
            .map((match) => match[1] ?? match[2] ?? match[3] ?? "");
        /* Words a person reads have a space in them; keys and paths do not. */
        return literals.filter((one) => /[A-Za-z]{2,} [A-Za-z]/.test(one));
    }

    for (const name of ["model.ts", "hooks.ts", "journey.tsx", "screens.tsx", "resume.tsx", "sample.ts"]) {
        it(`${name} says Lemma and people, plainly`, () => {
            const words = wordsIn(here + name);
            expect(words.length).toBeGreaterThan(0);
            for (const said of words) expect(said, `${name}: ${said}`).not.toMatch(BANNED);
        });
    }

    it("the web's lines the phone shows are plain too", () => {
        const statuses: ImportStatus[] = ["QUEUED", "FETCHING", "PLANNING", "AWAITING_CONFIRMATION", "APPLYING", "COMPLETED", "FAILED", "CANCELLED", "PARTIALLY_CANCELLED"];
        for (const status of statuses) expect(statusLine(status)).not.toMatch(BANNED);
        const kinds = ["AGENT", "APP", "WORKFLOW", "SCHEDULE", "TABLE", "TABLE_DATA", "FUNCTION", "SURFACE", "FILE"];
        for (const group of phoneGroups(kinds.map((kind, index) => step(index, kind, "x")))) expect(group.label).not.toMatch(BANNED);
    });
});

describe("catalogue lines in the phone's words", () => {
    it("says Lemma where the web's catalogue says pod", async () => {
        const { phoneWords } = await import("../src/areas/more/install/model");
        expect(phoneWords("installs as a complete campaign surface in your pod.")).toBe("installs as a complete campaign surface in your Lemma.");
        expect(phoneWords("asks before it acts outside the pod.")).toBe("asks before it acts outside the Lemma.");
        expect(phoneWords("the ten pods we stand behind")).toBe("the ten Lemmas we stand behind");
        expect(phoneWords("A shared task board")).toBe("A shared task board");
    });
});
