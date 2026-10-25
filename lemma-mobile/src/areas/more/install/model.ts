import { routes } from "../../../data/routes";
import { moreRoutes } from "../routes";
import type { KeyValueStore } from "../../../web-shims/session-storage";
import { PUBLIC_TEMPLATES, findPublicTemplateBySource, templateCoverPath, templateGitHubSource, type PublicTemplate } from "@web/site/templates/catalog";
import type { ImportStatus, ImportStatusResponse, PlanStep, StepStatus, VariableSpec } from "@web/site/import-types";
import { WORKING, groupSteps, humanize, installedApp, statusLine, suggestedValue, teammateName, type StepGroup } from "@web/site/import/plan";
import { extractReadmePresentation, resolveReadmeAssetUrl, resolveReadmeLinkUrl } from "@web/site/github/public-repository";

/** The phone's own reading on top of the web's install modules (`web/`):
 *  what the templates list, the install page and its journey, and the remix
 *  screen say and where each opens. Pure, so `tests/more-install.test.ts`
 *  holds it. */

/* ── storage ─────────────────────────────────────────────────────────── */

/** A store that answers at once, over what was read from the keystore
 *  before: the web's `pending-install.ts` reads and writes synchronously,
 *  and the phone's keystore does not. Each write is handed on to `persist`,
 *  which keeps it for the next launch. */
export function heldStore(entries: Record<string, string>, persist: (name: string, value: string | null) => void = () => undefined): KeyValueStore {
    const held = new Map(Object.entries(entries));
    return {
        getItem: (name) => held.get(name) ?? null,
        setItem: (name, value) => {
            held.set(name, value);
            persist(name, value);
        },
        removeItem: (name) => {
            held.delete(name);
            persist(name, null);
        },
    };
}

/* ── the catalogue ───────────────────────────────────────────────────── */

export { PUBLIC_TEMPLATES };
export type { PublicTemplate };

/** Where a template opens: its install page, by its GitHub source. */
export function templateRoute(template: PublicTemplate): string {
    const { owner, repo } = templateGitHubSource(template);
    return moreRoutes.install(owner, repo);
}

/** A template's cover, from the web app that serves it. */
export function coverUrl(web: string, template: PublicTemplate): string {
    return web.replace(/\/+$/, "") + templateCoverPath(template);
}

/* ── the install page ────────────────────────────────────────────────── */

/** A GitHub owner or repository name, as the web's route accepts one. */
export function isRepositoryName(value: string): boolean {
    return /^[a-zA-Z0-9_.-]+$/.test(value);
}

export function githubHref(owner: string, repo: string): string {
    return "https://github.com/" + owner + "/" + repo;
}

export function templateOf(owner: string, repo: string): PublicTemplate | null {
    try {
        return findPublicTemplateBySource(owner, repo);
    } catch {
        return null;
    }
}

/** A README longer than this is cut at a paragraph, with the rest one tap
 *  away on GitHub: a phone scrolls a long page, but a whole manual drawn
 *  as one page is slow to lay out and nobody reads it there. */
export const README_LIMIT = 14_000;

export interface Shown {
    title: string;
    /** The line under the name. */
    lede: string;
    /** The README to draw, trimmed as the web trims it, its relative links
     *  pointing at GitHub; null when there is none to draw. */
    body: string | null;
    /** The body was cut short for the phone. */
    cut: boolean;
}

/** What the install page shows of a repository: the name, the line under
 *  it and the README, the way the web's page decides them
 *  (`app/import/github/[owner]/[repo]/page.tsx`). `readme` undefined while
 *  it is being read, null when it could not be. */
/** A catalogue line in the phone's words. The catalogue is the web's,
 *  copied unchanged, and some of its lines say "pod" where the phone says
 *  "Lemma" for the same thing. */
export function phoneWords(text: string): string {
    return text
        .replace(/\b(your|the|a) pod\b/gi, (_all, article: string) => article + " Lemma")
        .replace(/\bpods\b/g, "Lemmas")
        .replace(/\bpod\b/g, "Lemma");
}

export function shownOf(owner: string, repo: string, template: PublicTemplate | null, readme: { markdown: string; branch: string } | null | undefined): Shown {
    const shown = readme ? extractReadmePresentation(readme.markdown, repo) : null;
    const title = template?.name ?? shown?.title ?? teammateName(repo);
    const lede = template?.description ?? shown?.intro ?? "";
    if (!readme || !shown) return { title, lede, body: null, cut: false };
    /* The name and the line under it already carry the README's opening
       line; printing it again as the first paragraph reads as a mistake. */
    const at = lede ? shown.body.indexOf(lede) : -1;
    const body = at >= 0 && at < 600 ? (shown.body.slice(0, at) + shown.body.slice(at + lede.length)).trim() : shown.body;
    const { text, cut } = cutReadme(body, README_LIMIT);
    return { title, lede, body: withResolvedLinks(text, owner, repo, readme.branch), cut };
}

const FENCE = /(```[\s\S]*?```)/g;

/** A README cut to about `limit` characters at a paragraph, never inside a
 *  fenced block. */
export function cutReadme(body: string, limit: number): { text: string; cut: boolean } {
    if (body.length <= limit) return { text: body, cut: false };
    let kept = "";
    for (const part of body.split(FENCE)) {
        if (kept.length + part.length <= limit) {
            kept += part;
            continue;
        }
        if (!part.startsWith("```")) {
            const room = part.slice(0, limit - kept.length);
            const end = room.lastIndexOf("\n\n");
            if (end > 0) kept += room.slice(0, end);
        }
        break;
    }
    return { text: kept.trim(), cut: true };
}

/** Relative links and pictures in a README, pointed at the repository on
 *  GitHub (the web's `resolveReadmeLinkUrl`), so a tap on one opens the
 *  right page in the browser. A link to a heading opens the README there. */
export function withResolvedLinks(markdown: string, owner: string, repo: string, branch: string): string {
    const link = (target: string, picture: boolean) => {
        if (target.startsWith("#")) return githubHref(owner, repo) + target;
        return picture ? resolveReadmeAssetUrl(target, owner, repo, branch) : resolveReadmeLinkUrl(target, owner, repo, branch);
    };
    return markdown
        .split(FENCE)
        .map((part) => part.startsWith("```")
            ? part
            : part
                .replace(/(!?)(\[[^\]]*\]\()([^)\s]+)/g, (_all, bang: string, open: string, target: string) => bang + open + link(target, bang === "!"))
                .replace(/^(\s*\[[^\]]+\]:\s*)(\S+)/gm, (_all, open: string, target: string) => open + link(target, false)))
        .join("");
}

/* ── who it is for ───────────────────────────────────────────────────── */

/** The organization made for somebody with none: their first name's
 *  Personal, as the web's `personalNameFor` names it. An address's local
 *  part is not a name, so without a real one it is plain Personal. */
export function personalName(me: { name?: string | null; firstName?: string | null; email?: string | null } | null | undefined): string {
    const name = (me?.name ?? "").trim();
    const local = (me?.email ?? "").split("@")[0] ?? "";
    const real = name && name !== "You" && name !== local ? name : (me?.firstName ?? "").trim();
    const first = real.split(/\s+/)[0] ?? "";
    return first && first !== local ? first + "'s Personal" : "Personal";
}

/** "A new Lemma", or one of the organization's. */
export const NEW = "new";

/** The Lemma's name the journey speaks of: the new one's (as made, once
 *  made), or the chosen one's. */
export function whoOf(target: string, typed: string, created: { name: string } | null, lemmas: readonly { id: string; name: string }[]): string {
    if (target === NEW) return created?.name ?? typed.trim();
    return lemmas.find((one) => one.id === target)?.name ?? "your Lemma";
}

/* ── the journey ─────────────────────────────────────────────────────── */

export function isWorking(job: ImportStatusResponse | null): boolean {
    return Boolean(job && WORKING.includes(job.status));
}

export function isStopped(status: ImportStatus): boolean {
    return status === "FAILED" || status === "CANCELLED" || status === "PARTIALLY_CANCELLED";
}

export const STEP_NAMES = ["Choose", "Review", "Install"] as const;

/** Which of Choose, Review and Install the journey is at (3: all done), or
 *  null when it stopped and draws no steps. */
export function stepAt(job: ImportStatusResponse | null): 0 | 1 | 2 | 3 | null {
    if (!job) return 0;
    if (job.status === "COMPLETED") return 3;
    if (isStopped(job.status)) return null;
    return job.status === "APPLYING" ? 2 : 1;
}

/** The bar at the page's foot, while the journey is closed: what is
 *  happening, and the one button that opens it. */
export function footFor(job: ImportStatusResponse | null, title: string, who: string): { line: string; action: string } {
    if (!job) return { line: title, action: "Install" };
    if (job.status === "AWAITING_CONFIRMATION") return { line: statusLine(job.status), action: "Review" };
    if (job.status === "APPLYING") return { line: progressWords(job) ?? statusLine(job.status), action: "Show" };
    if (job.status === "COMPLETED") return { line: `${who} is ready`, action: "Show" };
    return { line: statusLine(job.status), action: "Show" };
}

/** "Installing… 3 of 7", when the server says how many. */
export function progressWords(job: ImportStatusResponse): string | null {
    const { done, total } = job.progress;
    return total > 0 ? `${statusLine(job.status)} ${done} of ${total}` : null;
}

/** The fill of the progress bar, 0..1, or null when the total is unknown. */
export function progressFill(progress: { done: number; total: number }): number | null {
    if (!(progress.total > 0)) return null;
    return Math.max(0, Math.min(1, progress.done / progress.total));
}

/** What a plan's step kind is called on the phone. The web calls a way in a
 *  channel; the phone says where people reach the Lemma. */
export function phoneGroups(steps: PlanStep[]): StepGroup[] {
    return groupSteps(steps).map((group) => group.kind === "SURFACE"
        ? { ...group, label: group.steps.length === 1 ? "Way to reach it" : "Ways to reach it" }
        : group);
}

/** What a bundle brings, as one line: "1 agent, 1 app and sample rows". */
export function contentsWords(groups: readonly StepGroup[]): string {
    const parts = groups.map((group) => group.kind === "TABLE_DATA" ? "sample rows" : `${group.steps.length} ${group.label.toLowerCase()}`);
    if (parts.length < 2) return parts.join("");
    return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
}

/** The line over the review: who gets what. */
export function includedLine(who: string, title: string, groups: readonly StepGroup[]): string {
    const head = (who === title ? "Everything" : `${who} gets everything`) + ` ${title} brings`;
    return groups.length ? `${head}: ${contentsWords(groups)}.` : `${head}.`;
}

/** How each step's mark reads while installing, and in which tone. */
export function stepStateOf(status: StepStatus | string): { label: string; tone: "done" | "running" | "bad" | "quiet" } {
    if (status === "DONE") return { label: "Done", tone: "done" };
    if (status === "RUNNING") return { label: "Installing", tone: "running" };
    if (status === "FAILED") return { label: "Failed", tone: "bad" };
    if (status === "SKIPPED") return { label: "Skipped", tone: "quiet" };
    return { label: "Waiting", tone: "quiet" };
}

/** A step's badge while reviewing: amber for what replaces something,
 *  grey for the rest. */
export function actionTone(step: PlanStep): "amber" | "grey" {
    return step.destructive ? "amber" : "grey";
}

/** What a variable is called: "App slug". A bundle's connectors are not
 *  variables; they come with their own choices (`ImportPlan.connectors`). */
export function variableLabel(variable: VariableSpec): string {
    return humanize(variable.name);
}

/** Every variable's value as it is sent: what the person typed or chose,
 *  else what it is worth untouched. */
export function answersOf(variables: readonly VariableSpec[], values: Readonly<Record<string, string>>, who: string): Record<string, string> {
    return Object.fromEntries(variables.map((one) => [one.name, values[one.name] ?? suggestedValue(one, who)]));
}

/** A required answer still empty. */
export function missingAnswer(asked: readonly VariableSpec[], values: Readonly<Record<string, string>>, who: string): boolean {
    return asked.some((one) => one.required && !(values[one.name] ?? suggestedValue(one, who)));
}

/** What the end of a stopped install says, in the server's words when it
 *  sent some. */
export function stoppedWords(job: ImportStatusResponse, who: string): string {
    if (job.status === "FAILED") return job.error?.trim() || "Something went wrong while installing.";
    if (job.status === "PARTIALLY_CANCELLED") return `What was already installed stays with ${who}.`;
    return "Nothing was added.";
}

/** The finished install: what it says, and where its two ways out go. The
 *  app it brought comes first, since the app is the work. */
export function doneOf(podId: string, steps: PlanStep[], who: string, title: string): {
    heading: string;
    line: string;
    open: { label: string; href: string };
    other: { label: string; href: string } | null;
} {
    const app = installedApp(steps);
    const lemma = routes.lemma(podId);
    return {
        heading: `${who} is ready`,
        line: (who !== title ? `${title} is installed. ` : "") + (app ? `It came with ${humanize(app)}.` : "Say hello and give it its first job."),
        open: app ? { label: `Open ${humanize(app)}`, href: routes.app(podId, app) } : { label: `Open ${who}`, href: lemma },
        other: app ? { label: `Or open ${who} instead`, href: lemma } : null,
    };
}

/* ── remix ───────────────────────────────────────────────────────────── */

export type RemixSource =
    | { kind: "missing" }
    | { kind: "bad"; said: string }
    | { kind: "ok"; href: string };

/** The app a remix link names: an http or https address, as the web takes it. */
export function remixSourceOf(raw: string | null | undefined): RemixSource {
    const said = (raw ?? "").trim();
    if (!said) return { kind: "missing" };
    try {
        const url = new URL(said);
        if (url.protocol !== "http:" && url.protocol !== "https:") return { kind: "bad", said };
        return { kind: "ok", href: url.href };
    } catch {
        return { kind: "bad", said };
    }
}

/** The words a remix leaves in the new chat's box, unsent. */
export function remixWords(href: string): string {
    return "Help me rebuild or adapt this app for us: " + href;
}

const PLAIN_HOST = new Set(["www", "app", "apps", "web", "localhost"]);

/* Where a repository lives, named by its path (`/{owner}/{repo}/…`), never
   by the host: remixing github.com/owner/frontdesk makes "Frontdesk". */
const CODE_HOST = /(^|\.)(github\.com|githubusercontent\.com|gitlab\.com|bitbucket\.org|codeberg\.org)$/i;

/** A name for the new Lemma a remix makes, from the app's address: a
 *  repository's own name, else the first word of its host
 *  (`deals.apps.example.com` → "Deals"), else the last part of its path,
 *  else "New Lemma". */
export function remixName(href: string): string {
    try {
        const url = new URL(href);
        if (CODE_HOST.test(url.hostname)) {
            const repo = url.pathname.split("/").filter(Boolean)[1]?.replace(/\.git$/i, "") ?? "";
            return /^[a-z][a-z0-9_.-]*$/i.test(repo) ? teammateName(repo) : "New Lemma";
        }
        const label = url.hostname.split(".")[0] ?? "";
        const numeric = /^[\d.]+$/.test(url.hostname) || url.hostname.startsWith("[");
        if (label && !numeric && !PLAIN_HOST.has(label.toLowerCase()) && url.hostname.includes(".")) return teammateName(label);
        const last = url.pathname.split("/").filter(Boolean).pop()?.replace(/\.[a-z0-9]+$/i, "") ?? "";
        if (/^[a-z][a-z0-9_-]*$/i.test(last)) return teammateName(last);
    } catch {
        /* no address: the plain name */
    }
    return "New Lemma";
}
