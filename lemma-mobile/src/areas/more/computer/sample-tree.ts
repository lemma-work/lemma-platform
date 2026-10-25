import type { Entry } from "@web/computer/machine";
import type { PendingSignIn, WebLogin } from "./source";

/** The sample computer, as data: Ada's machine, with the two chats the
 *  sample Lemmas worked in, one project checked out, and what a browser
 *  picks up. Pure, so the tests can hold it to the sample chats. Shaped like
 *  a real one, never a real one. */

export const SAMPLE_HOME = "/home/user";
export const SAMPLE_WORKSPACE = SAMPLE_HOME + "/lemma";

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export interface SampleNode extends Entry {
    /** What a text file holds. */
    text?: string;
}

/** A day as the agent names its folders: `2026-10-24`. */
export function dayOf(ms: number): string {
    const at = new Date(ms);
    return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
}

/** The sample chats' folders, by conversation id. A chat not named here has
 *  no folder yet, which is its own state ("nothing has been written here"). */
export function sampleChatFolders(now: number): Record<string, string> {
    return {
        "launch-press": `${SAMPLE_WORKSPACE}/c/${dayOf(now - 2 * DAY)}/press-list-follow-ups`,
        "support-refund": `${SAMPLE_WORKSPACE}/c/${dayOf(now)}/refund-for-a-broken-order`,
    };
}

/** The one sign-in waiting in the sample: Launch's press-list chat, stopped
 *  at the tracker's login (`areas/chat/sample-chats.ts`, `launchPress`). */
export const SAMPLE_SIGN_IN: { conversationId: string; request: PendingSignIn } = {
    conversationId: "launch-press",
    request: { tool_call_id: "call-s2", origin: "https://app.prtracker.example", reason: "The press list is behind a login" },
};

export function sampleLogins(now: number): WebLogin[] {
    const inDays = (days: number) => new Date(now + days * DAY).toISOString();
    return [
        { site: "stripe.com", cookie_count: 6, expires: inDays(12), signed_in: true },
        { site: "github.com", cookie_count: 4, expires: inDays(64), signed_in: true },
        { site: "google.com", cookie_count: 9, expires: inDays(180), signed_in: false },
        { site: "youtube.com", cookie_count: 5, expires: inDays(20), signed_in: false },
        { site: "gstatic.com", cookie_count: 2, expires: null, signed_in: false },
    ];
}

const FOLLOW_UPS = `# Press list follow-ups

Spring launch, second round, sent from the press inbox.

| Outlet | Contact | Sent | Answer |
|---|---|---|---|
| The Lamp Review | Omar | Monday | Wants samples |
| Home & Light | Priya | Monday | No answer yet |
| Studio Weekly | Dev | Tuesday | Interview on Thursday |

## Next

- Send Home & Light the brief again, one page this time.
- Book the Studio Weekly call for Thursday at 10:00.
- The tracker's spring list is behind a login, so this waits on a person to sign in.
`;

const CONTACTS = `outlet,contact,email,last_sent,answer
The Lamp Review,Omar,omar@example.com,Monday,wants samples
Home & Light,Priya,priya@example.com,Monday,
Studio Weekly,Dev,dev@example.com,Tuesday,interview Thursday
`;

const TRACKER_NOTES = `Tracker: app.prtracker.example
List: spring
Opened /lists/spring and was sent to /login.
Asked for a person to sign in. The browser keeps the session afterwards.
`;

const REFUND = `# Refund check, order 4471

- Customer: Northwind
- Item: desk lamp, arrived broken (1 of 4 items)
- Amount: $240
- Stripe: refund drafted, waiting on approval

Once approved, check the dashboard shows it as sent, then tell the customer
the new lamp ships today.
`;

const REFUND_LOG = `09:41:02 stripe  GET /v1/charges/ch_4471 200
09:41:03 stripe  charge found: $960.00, 4 items, captured
09:41:05 refund  drafting partial refund: $240.00 (1 item)
09:41:06 refund  needs approval: amount over $100
09:41:06 wait    asked Ada to approve
`;

const README = `# acme/site

The Acme website. \`npm run dev\` serves it on port 3000.

## Pages

- \`/\` home
- \`/returns\` returns and refunds
- \`/press\` press kit

## Deploying

Merges to \`main\` deploy on their own. Ask before changing \`/returns\`:
Support links to it from every refund email.
`;

const PACKAGE = `{
  "name": "acme-site",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build"
  }
}
`;

const INDEX = `export const pages = ["/", "/returns", "/press"];

export function title(path: string): string {
    if (path === "/returns") return "Returns and refunds";
    if (path === "/press") return "Press kit";
    return "Acme";
}
`;

const BASHRC = `export PATH="$HOME/.local/bin:$PATH"
alias ll="ls -la"
`;

const PACKAGES = ["@next", "busboy", "caniuse-lite", "client-only", "js-tokens", "loose-envify", "nanoid", "next", "picocolors", "postcss", "react", "react-dom", "scheduler", "source-map-js", "styled-jsx"];

/** Every file and folder on the sample computer, by path. */
export function sampleTree(now: number): Map<string, SampleNode> {
    const nodes = new Map<string, SampleNode>();
    const at = (ago: number) => new Date(now - ago).toISOString();
    const folder = (path: string, ago: number) => nodes.set(path, { path, name: path.split("/").pop() ?? path, kind: "directory", size_bytes: 0, modified_at: at(ago) });
    const file = (path: string, ago: number, text: string | number) => nodes.set(path, {
        path,
        name: path.split("/").pop() ?? path,
        kind: "file",
        size_bytes: typeof text === "number" ? text : utf8Length(text),
        modified_at: at(ago),
        ...(typeof text === "string" ? { text } : {}),
    });
    const chats = sampleChatFolders(now);
    const press = chats["launch-press"];
    const refund = chats["support-refund"];
    const site = `${SAMPLE_WORKSPACE}/repos/acme/site`;

    folder(SAMPLE_HOME, 9 * DAY);
    file(`${SAMPLE_HOME}/.bashrc`, 30 * DAY, BASHRC);
    folder(`${SAMPLE_HOME}/.cache`, 2 * HOUR);
    folder(SAMPLE_WORKSPACE, 9 * DAY);
    folder(`${SAMPLE_WORKSPACE}/c`, 12 * MIN);
    folder(press.slice(0, press.lastIndexOf("/")), 2 * DAY);
    folder(press, 2 * DAY);
    file(`${press}/follow-ups.md`, 2 * DAY - 9 * MIN, FOLLOW_UPS);
    file(`${press}/press-contacts.csv`, 2 * DAY - 7 * MIN, CONTACTS);
    file(`${press}/tracker-notes.txt`, 2 * DAY - 5 * MIN, TRACKER_NOTES);
    file(`${press}/tracker-login.png`, 2 * DAY - 5 * MIN, 48_210);
    folder(refund.slice(0, refund.lastIndexOf("/")), 12 * MIN);
    folder(refund, 12 * MIN);
    file(`${refund}/refund-check.md`, 12 * MIN, REFUND);
    file(`${refund}/stripe-refund.log`, 12 * MIN, REFUND_LOG);
    folder(`${SAMPLE_WORKSPACE}/repos`, 9 * DAY);
    folder(`${SAMPLE_WORKSPACE}/repos/acme`, 9 * DAY);
    folder(site, 3 * DAY);
    folder(`${site}/.git`, 3 * DAY);
    file(`${site}/.gitignore`, 9 * DAY, "node_modules\n.next\n");
    file(`${site}/README.md`, 3 * DAY, README);
    file(`${site}/package.json`, 9 * DAY, PACKAGE);
    folder(`${site}/src`, 3 * DAY);
    file(`${site}/src/index.ts`, 3 * DAY, INDEX);
    file(`${site}/server.log`, 40 * MIN, 2_480_000);
    file(`${site}/launch-teaser.mp4`, 3 * DAY, 46_200_000);
    folder(`${site}/node_modules`, 9 * DAY);
    for (const name of PACKAGES) folder(`${site}/node_modules/${name}`, 9 * DAY);
    return nodes;
}

/** Bytes in a string's UTF-8, without a TextEncoder. */
export function utf8Length(text: string): number {
    let bytes = 0;
    for (const character of text) {
        const point = character.codePointAt(0) ?? 0;
        bytes += point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4;
    }
    return bytes;
}

/** The entries directly inside `path`, in the server's order (by path). */
export function childrenOf(nodes: Map<string, SampleNode>, path: string): SampleNode[] {
    const prefix = path.replace(/\/+$/, "") + "/";
    return [...nodes.values()]
        .filter((node) => node.path.startsWith(prefix) && !node.path.slice(prefix.length).includes("/"))
        .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}
