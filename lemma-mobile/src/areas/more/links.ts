/** Links from outside the app: an invitation in an email, an install or a
 *  remix button on a page, a Lemma's address somebody sent, a sign-in a
 *  Lemma asked for. Pure, so `tests/more-links.test.ts` holds it.
 *
 *  The phone's screens sit at the web's own paths (`moreRoutes`), so a link
 *  in the web's grammar needs only its host dropped: `https://lemma.work/
 *  invitations/<id>/accept` and `lemma://invitations/<id>/accept` both open
 *  `/invitations/<id>/accept`. The few the phone keeps elsewhere are said
 *  here. An address inside the app (`/home`, `/chats`, `/l/<id>/…`, and the
 *  older `/t/…` ones) is read by the web's own grammar
 *  (`@web/outcomes/address`, and the workspace's through
 *  `@web/shell/address`), and the place it names opens at the phone's own
 *  path for it (`phonePathOf`): a chat opens the chat, a table the table. */

import { routes } from "../../data/routes";
import { PRESETS } from "../../session/server";
import { adminRoutes } from "../admin/routes";
import { chatsRoutes } from "../chats/routes";
import { filesRoutes } from "../files/routes";
import { lemmaTab } from "../paths";
import { tablesRoutes } from "../tables/routes";
import { RUNS_VIEWS, workRoutes, type RunsView } from "../work/routes";
import { moreRoutes } from "./routes";
import { readTrialView, readView, type View } from "@web/outcomes/address";
import { viewOfAddress } from "@web/outcomes/model";
import { readAddress } from "@web/shell/address";

/** The web hosts whose links the app opens: lemma.work's and those of the
 *  servers the build names (`android.intentFilters`, app.json and
 *  app.config.js). A custom server's links open in the browser. */
export const LINK_HOSTS = PRESETS.flatMap((server) => {
    const host = /^https?:\/\/([^/:]+)/i.exec(server.web)?.[1]?.toLowerCase();
    return host ? [host, "www." + host] : [];
});

const SCHEME = /^lemma:\/\//i;

/** The path and query a link names, or null for one that is not ours. */
function pathOf(url: string): string | null {
    const said = url.trim();
    if (SCHEME.test(said)) return "/" + said.replace(SCHEME, "").replace(/^\/+/, "");
    const web = /^https?:\/\/([^/?#]+)(\/[^#]*)?/i.exec(said);
    if (web) return LINK_HOSTS.includes(web[1].toLowerCase()) ? web[2] || "/" : null;
    return said.startsWith("/") ? said : null;
}

/** One id-like segment: never "." or "..", never an escaped character. */
const SAFE = /^(?!\.{1,2}$)[A-Za-z0-9._~-]+$/;

/** Where on the phone a link opens, or null when it is not one of the links
 *  this file knows (the router is then left to read it as it is). */
export function appPathOf(url: string): string | null {
    const path = pathOf(url);
    if (path === null) return null;
    const [bare, query = ""] = path.split("?", 2);
    const parts = bare.split("/").filter(Boolean);
    const [head, one, two, three] = parts;
    const search = query ? "?" + query : "";
    if (head === "invitations" && one && SAFE.test(one) && (two === "accept" || two === "reject") && parts.length === 3) return `/invitations/${one}/${two}`;
    if (head === "import" && one === "github" && two && three && SAFE.test(two) && SAFE.test(three) && parts.length === 4) return `/import/github/${two}/${three}`;
    if (head === "remix" && parts.length === 1) return "/remix" + search;
    if (head === "templates") return "/templates";
    if (head === "sign-in-to-site" && one && two && SAFE.test(one) && SAFE.test(two) && parts.length === 3) return `/sign-in-to-site/${one}/${two}`;
    if (head === "join" && one && SAFE.test(one) && parts.length === 2) return `/join/${one}`;
    const view = viewOfLink(bare, head);
    return view ? phonePathOf(view) : null;
}

/** The place an address inside the app names, by the web's grammar: its
 *  own (`/home`, `/l/<id>/chats/<chat>`), the frame's first under `/t` and
 *  `/o`, and the workspace's (`/t/<pod>/conversation/<id>`, or the same
 *  under `/l`). */
function viewOfLink(bare: string, head: string | undefined): View | null {
    const view = readView(bare) ?? readTrialView(bare);
    if (view) return view;
    if (head !== "t" && head !== "l") return null;
    const address = readAddress(bare);
    if (!address.podId) return null;
    return viewOfAddress(address) ?? { name: "outcome", podId: address.podId, tab: "home" };
}

/** Where the phone keeps the place a web address names. */
export function phonePathOf(view: View): string {
    switch (view.name) {
        case "today": return routes.home;
        case "outcomes": return routes.lemmas;
        case "needs": return routes.needs;
        case "apps": return routes.apps;
        case "connectors": return adminRoutes.section("connectors");
        case "people": return adminRoutes.section("people");
        case "hire": return moreRoutes.newLemma;
        case "chats": {
            const open = view.open;
            if (open.kind === "chat") return routes.chat(open.podId, open.id);
            if (open.kind === "shared") return chatsRoutes.shared(open.podId, open.id);
            if (open.kind === "contact") return chatsRoutes.contact(open.podId, open.id);
            return chatsRoutes.all;
        }
        case "outcome": {
            const { podId, section } = view;
            switch (view.tab) {
                case "home": return routes.lemma(podId);
                case "contacts": return chatsRoutes.contacts(podId);
                case "automations": return workRoutes.runs(podId, RUNS_VIEWS.includes(section as RunsView) ? section as RunsView : undefined);
                case "settings": return adminRoutes.lemma(podId);
                /* What it made: its files, with its tables a tab away. */
                case "made": return lemmaTab(podId, "files");
                default: return lemmaTab(podId, view.tab);
            }
        }
        case "run": return workRoutes.run(view.podId, view.runId);
        case "app": return routes.app(view.podId, view.app);
        case "file": return filesRoutes.file(view.podId, view.path);
        case "table": return tablesRoutes.table(view.podId, view.table);
        case "record": return tablesRoutes.record(view.podId, view.table, view.recordId);
        case "workflow": return workRoutes.workflow(view.podId, view.workflow);
        case "agent": return workRoutes.agent(view.podId, view.agent);
        case "computer": return moreRoutes.computer({ podId: view.podId, conversationId: view.conversationId });
    }
}

/** Whether a path is one a person reaches by a link, and so one worth
 *  keeping across a sign-in. */
export function isLinkPath(path: string): boolean {
    return /^\/(invitations|import|remix|templates|sign-in-to-site|lemma|lemmas|join|chat|chats|shared|app|apps|needs|settings|computer|new-lemma)(\/|\?|$)/.test(path);
}

/* ── a link that arrived while signed out ─────────────────────────────── */

/** How long a link waits for somebody to sign in. */
export const LINK_WAIT_MS = 15 * 60_000;

let waiting: { path: string; at: number } | null = null;

/** Hold a link until its screen is open. Signed out, the router shows the
 *  sign-in screen instead, and the link would be gone once they are in. */
export function rememberLink(path: string, now = Date.now()): void {
    if (isLinkPath(path)) waiting = { path, at: now };
}

/** The link's screen opened: nothing is waiting any more. */
export function linkArrived(pathname: string): void {
    if (waiting && waiting.path.split("?")[0] === pathname.split("?")[0]) waiting = null;
}

/** The link still waiting, if it is recent, taken so it opens once. */
export function takeWaitingLink(now = Date.now()): string | null {
    const held = waiting;
    waiting = null;
    return held && now - held.at < LINK_WAIT_MS ? held.path : null;
}
