import { isNoise, machineState, MAX_READ_BYTES, MAX_TEXT_BYTES, ordered, readableSize, screenSay, viewerFor, describe, type BrowserState, type Entry, type Listing, type MachineState, type Roots, type ScreenSay, type Viewer } from "@web/computer/machine";
import { isSettled, type LiveState } from "@web/computer/live";
import { groupSites, loginNote } from "@web/computer/logins";
import { outcomeSay } from "@web/computer/sign-in";
import { startupProgress, type WorkspaceStatus } from "@web/computer/startup";
import type { WebLogin } from "./source";

export { base64Of, bytesOf, keysymOf, typedKeys } from "./wire";

/** The phone's reading of the computer, on top of the web's own modules
 *  (`web/`, copied unchanged): what each state says in the phone's words,
 *  what opening a file does, the socket's address, the display's size.
 *  Pure, so the tests hold it. */

/** The one line under the title: whose computer this is. */
export const WHOSE = "Your own computer, shared by every Lemma you work with.";

/** What the sample says where a live screen would be. */
export const SAMPLE_SCREEN = "This is sample data, so there is no computer behind this screen.";

/* ── the state line ──────────────────────────────────────────────────── */

export type Dot = "violet" | "amber" | "on" | "off";

export interface StateLine extends ScreenSay {
    state: MachineState;
    /** Violet only while a browser is open on it; amber when it could not
     *  be reached. */
    dot: Dot;
}

/** The dot, the headline and the note, from the two ambient answers. */
export function stateLine(listing: Listing | undefined, browser: BrowserState | undefined, pending: boolean): StateLine {
    const state = machineState(listing, browser, pending);
    const said = screenSay(state, browser);
    return { ...said, note: phoneNote(said.note), state, dot: dotOf(state) };
}

function dotOf(state: MachineState): Dot {
    if (state === "browsing") return "violet";
    if (state === "unreachable") return "amber";
    if (state === "awake") return "on";
    return "off";
}

/** The web says "machine" where the phone says "computer". */
function phoneNote(note: string): string {
    return note.replace(/\bmachine\b/g, "computer");
}

/** How often the status is read: often while it starts, else now and then. */
export function statusEvery(status: WorkspaceStatus | undefined): number {
    return startupProgress(status) ? 3_000 : 15_000;
}

/** The startup progress, in the phone's words: a title, the server's detail,
 *  a bar (null when it cannot be measured) and "x of y MB". */
export function startupLine(status: WorkspaceStatus | undefined): { title: string; detail: string | null; fraction: number | null; downloaded: string | null } | null {
    const progress = startupProgress(status);
    if (!progress) return null;
    return { ...progress, title: status?.state === "downloading" ? "Setting up your computer" : "Starting your computer" };
}

/* ── the live screen ─────────────────────────────────────────────────── */

/** What the live screen says under the picture, for every state. */
export function liveWords(state: LiveState): string {
    switch (state) {
        case "connecting": return "Connecting to the screen…";
        case "live": return "Live. Tap to click, drag with two fingers to scroll.";
        case "no-browser": return "Nothing is open on it yet. This picks up once something starts.";
        case "signed-out": return "Your session ended. Sign in to Lemma again to watch.";
        case "unsupported": return "This kind of computer has no screen to show.";
        case "stale-image": return "This computer runs software too old to show its screen here.";
        case "refused": return "The server won’t let the app carry the picture.";
        case "lost": return "The connection dropped. Trying again…";
    }
}

/** What the sign-in screen says when the picture is not there. */
export function signInTrouble(state: LiveState): string {
    switch (state) {
        case "connecting": return "Connecting to the browser…";
        case "no-browser": return "The browser hasn’t started yet. This picks up when it does.";
        case "signed-out": return "Your session ended. Sign in to Lemma again to carry on.";
        case "unsupported": return "This kind of computer has no screen to show.";
        case "stale-image": return "This computer runs software too old to show its screen here.";
        case "refused": return "The server won’t let the app carry the picture.";
        case "live": return "";
        case "lost": return "The connection dropped. Trying again…";
    }
}

/** Where the same browser can still be reached: the phone's own browser,
 *  through a signed, expiring address. Only for the states retrying cannot
 *  fix and the server's grant can. */
export function opensInBrowser(state: LiveState): boolean {
    return state === "refused" || state === "unsupported" || state === "stale-image";
}

/** Whether to try the socket again after it closed this way. */
export function retries(state: LiveState): boolean {
    return state !== "live" && state !== "connecting" && !isSettled(state);
}

/** The live screen's socket. One or the other: a sign-in names its site,
 *  watching names the chat whose browser it is (or nothing, for whatever the
 *  computer has open). Never a credential: the app sends its token in the
 *  handshake's `Authorization` header, which the server reads first. */
export function screenSocketUrl(api: string, target: { origin?: string | null; conversationId?: string | null }): string {
    const base = api.replace(/^http/, "ws").replace(/\/+$/, "");
    const query = ["mode=control"];
    if (target.origin) query.push("origin=" + encodeURIComponent(target.origin));
    else if (target.conversationId) query.push("conversation=" + encodeURIComponent(target.conversationId));
    return `${base}/workspace/browser/view?${query.join("&")}`;
}

/** What the display-size route accepts, as the web clamps it: the nearest
 *  size the computer can have rather than a refusal. */
export function displaySize(box: { width: number; height: number }): { width: number; height: number } {
    const clamp = (value: number, least: number) => Math.round(Math.min(Math.max(Number.isFinite(value) ? value : least, least), 4096));
    return { width: clamp(box.width, 320), height: clamp(box.height, 240) };
}

/** How long a box has to stop changing before the display is asked to
 *  match it (the web's `RESIZE_SETTLE_MS`). */
export const RESIZE_SETTLE_MS = 250;

/* ── files ───────────────────────────────────────────────────────────── */

/** Where the files open: the chat's folder when one was named, else the
 *  server's own default (null). */
export function startingFolder(asked: string | null, chatFolder: string | null | undefined): string | null {
    return asked ?? chatFolder ?? null;
}

/** What a folder is for, when the layout says so, in the phone's words: a
 *  chat, not a conversation, and no dashes. */
export function whereSay(path: string, roots: Roots): string | null {
    const said = describe(path, roots);
    if (!said) return null;
    if (said.startsWith("The whole machine")) return "The whole computer. It’s yours: all your work happens on it.";
    return said
        .replace(" — ", ": ")
        .replace("Where one conversation's shell starts.", "Where one chat’s commands run.")
        .replace(/\bConversations\b/g, "Chats")
        .replace(/\bconversations\b/g, "chats")
        .replace(/\bconversation\b/g, "chat");
}

/** A folder's rows as they are read: folders first, then by name, with
 *  build output and dotfiles held back unless asked for. */
export function folderRows(entries: readonly Entry[], showHidden: boolean): { shown: Entry[]; hidden: number } {
    const shown = ordered(showHidden ? [...entries] : entries.filter((entry) => !isNoise(entry)));
    return { shown, hidden: entries.length - shown.length };
}

/** The quiet end of a row: a folder says so, a file its size. */
export function entryNote(entry: Entry): string {
    if (entry.kind === "directory") return "Folder";
    const size = readableSize(entry.size_bytes);
    const said = entry.size_bytes > 0 && size ? size : "Empty";
    return entry.kind === "symlink" ? said + ", a link" : said;
}

/** "12 items · 3 hidden". */
export function countLine(shown: number, hidden: number): string {
    const items = shown === 1 ? "1 item" : `${shown} items`;
    return hidden > 0 ? `${items} · ${hidden} hidden` : items;
}

/** How a file opens in place, decided before any of it is read. */
export function viewerOf(name: string): Viewer {
    return viewerFor(name);
}

/** How much of a file to ask for to show it: one byte past the reading
 *  ceiling for text, so a cut file can be told from a whole one; the
 *  server's single read for an image. */
export function readLimit(viewer: Viewer): number {
    return viewer === "image" ? MAX_READ_BYTES : MAX_TEXT_BYTES + 1;
}

/** Whether what came back is more than a phone should show. */
export function tooLargeToShow(viewer: Viewer, bytes: number): boolean {
    return viewer === "image" ? bytes >= MAX_READ_BYTES : bytes > MAX_TEXT_BYTES;
}

/** What Save does with a file of this size: download it whole (the server's
 *  one read), or say a phone cannot. An unknown size is tried. */
export function saving(sizeBytes: number | null): "save" | "too-large" {
    return sizeBytes !== null && sizeBytes > MAX_READ_BYTES ? "too-large" : "save";
}

export const SAVE_LIMIT = MAX_READ_BYTES;

export const TOO_LARGE_TO_SAVE = "This file is larger than 8 MB, too large to save on a phone. Ask in the chat for what you need from it.";

/** The line under an open file: its real size, when it is known. */
export function sizeLine(bytes: number | null): string {
    return bytes === null ? "" : readableSize(bytes) || "Empty";
}

/* ── signed-in sites ─────────────────────────────────────────────────── */

export interface SiteGroup {
    label: string | null;
    sites: WebLogin[];
}

/** The sites as the web groups them: the ones somebody signed in to, and
 *  the others the browser picked up cookies from, but only split when both
 *  are there. */
export function siteGroups(items: readonly WebLogin[]): SiteGroup[] {
    const groups = groupSites(items);
    if (!groups.split) return items.length ? [{ label: null, sites: [...items] }] : [];
    return [
        { label: "Signed in", sites: groups.signedIn },
        { label: "Other sites with cookies", sites: groups.other },
    ];
}

/** One site's quiet line: how long it lasts, and its cookies. */
export function siteNote(login: WebLogin, now: Date): string {
    return loginNote(login, now);
}

/** What "Sign out" asks first, naming the site and what follows. */
export function signOutAsk(site: string): { title: string; body: string } {
    return {
        title: `Sign the browser out of ${site}?`,
        body: "Any work there will ask you to sign in again.",
    };
}

/* ── a sign-in ───────────────────────────────────────────────────────── */

/** What to say once a sign-in is answered, in the phone's punctuation. */
export function outcomeWords(signedIn: boolean, working: boolean): { headline: string; note: string } {
    const said = outcomeSay(signedIn, working);
    return { headline: said.headline, note: said.note.replace(" — ", ", ") };
}

export const SIGN_IN_FINE = "Sign in below as you normally would. The browser keeps the session so the work can carry on, and your password never reaches Lemma.";

export const SIGN_IN_NOT_YOURS = "This sign-in isn’t for the account you’re signed in with here.";

/** The reason, quoted as the Lemma's words rather than the app's. */
export function reasonLine(reason: string): string | null {
    const said = reason.trim();
    return said ? `It says: “${said}”` : null;
}
