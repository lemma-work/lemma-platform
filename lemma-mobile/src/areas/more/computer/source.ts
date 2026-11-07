import type { BrowserState, Listing } from "@web/computer/machine";
import type { WorkspaceStatus } from "@web/computer/startup";

/** The computer the Lemmas work on (PARITY.md, G): its state, its files,
 *  the sites its browser is signed in to, and a site waiting on a person to
 *  sign in.
 *
 *  One computer per person, shared by every Lemma they work with, so nothing
 *  here takes a Lemma: the routes are keyed by the session's own person. The
 *  rule every read keeps is the web's: **looking must not start the
 *  machine**. Listing and the logins are ambient until `wake` is asked for,
 *  and the browser's status never provisions; reading a file, the browser
 *  grant and the live screen are the only things that cost anything, and each
 *  is somebody's press. */
export interface ComputerSource {
    /** Whether a real machine stands behind this source. Sample data has
     *  none, so its screen says so rather than open a socket to nothing. */
    machine: boolean;
    /** `GET /workspace/status`: ready, downloading, starting, asleep. */
    status(): Promise<WorkspaceStatus>;
    /** `GET /workspace/files`, one page. `path` null is the server's own
     *  default; `after` is the server's cursor. */
    files(path: string | null, wake: boolean, after?: string): Promise<Listing>;
    /** `GET /workspace/browser/status`. Never starts anything. */
    browser(): Promise<BrowserState>;
    /** The folder a chat works in (the conversation's `workspace_cwd`). */
    chatFolder(podId: string, conversationId: string): Promise<string | null>;
    /** `GET /workspace/files:stat`: the file's real size. */
    stat(path: string): Promise<{ size_bytes: number }>;
    /** Up to `limit` bytes of a file, read as text. `bytes` is how much came
     *  back, so a caller that asked for one past its ceiling can tell. */
    text(path: string, limit: number): Promise<{ text: string; bytes: number }>;
    /** A file (up to `limit` bytes) downloaded into the phone's cache, for
     *  an image to draw or a file to save. */
    download(path: string, limit: number): Promise<{ uri: string; bytes: number }>;
    /** `POST /workspace/apps/browser/access`: a signed, expiring address for
     *  the browser, to open in the phone's own browser. Starts the machine. */
    browserAccess(): Promise<{ url: string; expires_at: string }>;
    /** `GET /web-logins`. */
    logins(wake: boolean): Promise<{ items: WebLogin[]; sleeping: boolean }>;
    /** `DELETE /web-logins?origin=`: really signs the browser out. */
    signOutOf(origin: string): Promise<{ site: string; forgotten: boolean }>;
    /** `GET /web-logins/sign-ins/{conversation}/{call}`. */
    signIn(conversationId: string, toolCallId: string): Promise<PendingSignIn>;
    /** `POST /web-logins/sign-ins/{conversation}/{call}/answer`. */
    answerSignIn(conversationId: string, toolCallId: string, signedIn: boolean): Promise<SignInOutcome>;
    /** `GET /workspace/browser/current-page-url?origin=`: where a sign-in's
     *  browser really is. */
    currentPage(origin: string): Promise<string | null>;
    /** `POST /workspace/browser/display-size`, CSS pixels. */
    fitDisplay(size: { width: number; height: number }): Promise<{ size: string | null }>;
}

/** One site the computer's browser holds cookies for, as `GET /web-logins`
 *  sends it. */
export interface WebLogin {
    /** The site, as a person would name it: grouped by registrable domain. */
    site: string;
    cookie_count: number;
    /** When the soonest of them lapses; null when all are session cookies. */
    expires: string | null;
    /** Whether somebody answered "yes, I signed in" for this site. False
     *  means nobody said so, not that there is no session. */
    signed_in: boolean;
}

/** What a sign-in is asking for, by the paused call. */
export interface PendingSignIn {
    tool_call_id: string;
    origin: string;
    /** What the Lemma is doing, in its own words. */
    reason: string;
}

/** What came of an answer. `working` is the server's look at whether the
 *  site stopped asking, a guess rather than a verdict. */
export interface SignInOutcome {
    origin: string;
    signed_in: boolean;
    working: boolean;
}
