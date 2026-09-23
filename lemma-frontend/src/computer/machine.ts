/** The computer your teammates work on, and what to call what is on it.
 *
 *  One sandbox per person, not per pod — every teammate you talk to runs its
 *  shell on the same machine. That is the fact this file exists to make
 *  legible, because the paths give it away and nothing else does: a
 *  conversation's scratch directory and a checked-out project sit side by side
 *  and look identical until something names them.
 *
 *  Everything here is a pure reading of a path or a listing. The rule the
 *  queries enforce — that looking must not start a machine — is theirs; this
 *  file only says what the answer means.
 */

/** Where a sandbox keeps things, until the server says otherwise.
 *
 *  Two paths, and they were one until the durable root moved. `home` is what
 *  *survives* — the sandbox user's home, so everything a tool writes to `~`
 *  outlives a suspend along with it — and it is as far up as the files route
 *  will answer. `workspace` is where conversations and checkouts *go*, one
 *  level inside it.
 */
export interface Roots {
    home: string;
    workspace: string;
}

/** A first guess, and never the answer.
 *
 *  Every listing carries `home_root` and `workspace_root`, and `rootsOf` reads
 *  them. This pair exists only so the first request has somewhere to start,
 *  and it is deliberately not exported as a browsing root: a constant here can
 *  only ever be as current as the last person to remember it, and the last
 *  time these moved the hardcoded copy stayed behind. This pane asked
 *  `/workspace` for a fortnight after that path stopped existing — which the
 *  route answers 422 for, so the machine read as unreachable rather than as a
 *  client looking in the wrong place.
 */
const FIRST_GUESS: Roots = { home: "/home/user", workspace: "/home/user/lemma" };

/** The roots this listing came back with, falling back to the guess.
 *
 *  Falsy rather than absent, because a deployment old enough to omit them and
 *  one that sends empty strings are the same thing to a reader: no answer.
 */
export function rootsOf(listing: Listing | undefined): Roots {
    return {
        home: listing?.home_root || FIRST_GUESS.home,
        workspace: listing?.workspace_root || FIRST_GUESS.workspace,
    };
}

/** One entry, exactly as `GET /workspace/files` sends it. */
export interface Entry {
    path: string;
    name: string;
    kind: "file" | "directory" | "symlink";
    size_bytes: number;
    modified_at: string;
}

/** One directory, exactly as `GET /workspace/files` sends it.
 *
 *  `sleeping` and `exists` are both false-by-absence in the schema and both
 *  mean something specific, which is why neither is folded into "no entries":
 *  a paused machine was never asked, and a directory that is not there is a
 *  different answer from one that is empty.
 */
export interface Listing {
    path: string;
    /** The durable root, and the ceiling on browsing. Served rather than
     *  assumed — see `FIRST_GUESS`. */
    home_root?: string;
    /** Where projects and conversation directories live, inside `home_root`. */
    workspace_root?: string;
    sleeping?: boolean;
    truncated?: boolean;
    next_after?: string | null;
    exists?: boolean;
    entries?: Entry[];
}

/** What `GET /workspace/browser/status` answers without waking anything. */
export type BrowserState = "asleep" | "stopped" | "running" | "unavailable" | "unsupported";

/** What the machine is doing, from the two ambient answers together.
 *
 *  Both endpoints describe one sandbox, so one of them has to be the authority
 *  or the view contradicts itself the moment they disagree. The file listing
 *  wins because it is the one this view always asks for; the browser is only
 *  consulted for the thing the listing cannot know.
 *
 *  `checking` and `unreachable` are states rather than a spinner and an error
 *  because the alternative is a lie. Folding "no listing yet" into `asleep`
 *  makes every open of this view flash "Asleep" and offer to wake a machine
 *  that is already running. Not knowing is its own answer, and neither of
 *  these offers an action — there is nothing to act on until one of them
 *  resolves.
 */
export type MachineState = "checking" | "unreachable" | "asleep" | "awake" | "browsing";

export function machineState(
    listing: Listing | undefined,
    browser: BrowserState | undefined,
    /** Whether the listing is still in flight. Told rather than inferred: a
     *  listing that failed and one that has not landed both have no data, and
     *  they are not the same thing to say. */
    pending: boolean,
): MachineState {
    if (!listing) return pending ? "checking" : "unreachable";
    if (listing.sleeping) return "asleep";
    return browser === "running" ? "browsing" : "awake";
}

/** Whether there is a browser worth offering to watch.
 *
 *  `unavailable` and `unsupported` are deliberately not offered rather than
 *  offered-and-refused: one is an image too old to have the relay and the
 *  other is a fabric that cannot reach a port at all, and neither is fixed by
 *  clicking. A button that cannot work is worse than no button.
 */
export function watchable(browser: BrowserState | undefined): boolean {
    return browser === "running" || browser === "stopped";
}

/** The directory above this one, or null at the ceiling.
 *
 *  The ceiling is the home rather than the project root, because that is what
 *  the files route will answer for — and it is a level above where projects
 *  live, so walking all the way up really does reach the machine.
 */
export function parentOf(path: string, roots: Roots): string | null {
    const trimmed = path.replace(/\/+$/, "") || roots.home;
    if (trimmed === roots.home || !trimmed.startsWith(roots.home + "/")) return null;
    const cut = trimmed.lastIndexOf("/");
    return cut <= roots.home.length - 1 ? roots.home : trimmed.slice(0, cut);
}

/** The path as clickable segments, the machine first.
 *
 *  A path outside the home cannot be walked back into it — the route refuses
 *  anything that spells or resolves its way out — so it reduces to the ceiling
 *  rather than producing crumbs that lead somewhere no request can go.
 */
export function crumbs(path: string, roots: Roots): { name: string; path: string }[] {
    const head = { name: "Computer", path: roots.home };
    const trimmed = path.replace(/\/+$/, "") || roots.home;
    if (trimmed === roots.home || !trimmed.startsWith(roots.home + "/")) return [head];
    const trail: { name: string; path: string }[] = [head];
    let walked = roots.home;
    for (const segment of trimmed.slice(roots.home.length + 1).split("/")) {
        if (!segment) continue;
        walked += "/" + segment;
        trail.push({ name: segment, path: walked });
    }
    return trail;
}

/** What this directory is for, when the layout says so.
 *
 *  The agent's own conventions are the only thing that makes these paths
 *  readable: `{workspace}/c/{date}/{slug}` is where one conversation's shell
 *  starts, `{workspace}/repos/{owner}/{repo}` is a checkout shared by every
 *  conversation working on that project. Without this the pane shows eight
 *  random slugs and leaves the reader to guess which one they were just
 *  talking in. Null where there is nothing to add — an ordinary folder is
 *  already described by its name.
 */
export function describe(path: string, roots: Roots): string | null {
    const trimmed = path.replace(/\/+$/, "") || roots.home;
    if (trimmed === roots.home) return "The whole machine. Yours — every teammate you talk to works on it.";
    if (trimmed === roots.workspace) return "Where conversations and checkouts go.";
    const parts = trimmed.startsWith(roots.workspace + "/")
        ? trimmed.slice(roots.workspace.length + 1).split("/")
        : [];
    if (parts[0] === "c") {
        if (parts.length === 1) return "One folder per conversation.";
        if (parts.length === 2) return "Conversations started on " + parts[1] + ".";
        return "Where one conversation's shell starts.";
    }
    if (parts[0] === "repos") {
        if (parts.length === 1) return "Projects checked out on this computer.";
        if (parts.length === 2) return "Projects belonging to " + parts[1] + ".";
        if (parts.length === 3) return parts[1] + "/" + parts[2] + " — one checkout, shared by every conversation working on it.";
    }
    return null;
}

/** Folders first, then by name.
 *
 *  The server orders by path so that paging means the same thing twice, which
 *  is the right order for a cursor and the wrong one for reading. Reordering
 *  here is safe only because `next_after` is taken from the response rather
 *  than from this list — the cursor stays in the server's order while the eye
 *  gets ours.
 */
export function ordered(entries: Entry[]): Entry[] {
    const rank = (entry: Entry) => (entry.kind === "directory" ? 0 : 1);
    return [...entries].sort(
        (a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );
}

/** Build output, caches, version control: real, and never what you came for. */
const NOISE = new Set(["node_modules", "__pycache__", ".git", ".venv", ".cache", "dist", ".next"]);

export function isNoise(entry: Entry): boolean {
    return entry.name.startsWith(".") || NOISE.has(entry.name);
}

export function readableSize(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes < 0) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
}

/** How to show a file, decided before any of it is fetched.
 *
 *  By extension rather than by sniffing the bytes, because the decision has to
 *  be made to *ask* — an image has to stay a blob and text has to be decoded,
 *  and doing both to find out costs the transfer twice.
 */
export type Viewer = "markdown" | "text" | "image" | "other";

const TEXT = new Set([
    "txt", "log", "json", "jsonl", "yaml", "yml", "toml", "ini", "cfg", "conf", "env",
    "csv", "tsv", "sql", "sh", "bash", "zsh", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx",
    "css", "scss", "html", "xml", "rs", "go", "rb", "java", "kt", "swift", "c", "h", "cpp",
    "gitignore", "dockerfile", "lock", "diff", "patch",
]);
const IMAGE = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "avif", "bmp", "ico"]);

export function viewerFor(name: string): Viewer {
    const base = name.toLowerCase();
    const dot = base.lastIndexOf(".");
    /* A file with no dot is the interesting case: `Dockerfile`, `Makefile`,
       `README` are all readable, and treating them as binaries left the
       commonest thing an agent writes unopenable. */
    const extension = dot <= 0 ? base : base.slice(dot + 1);
    if (extension === "md" || extension === "markdown") return "markdown";
    if (IMAGE.has(extension)) return "image";
    if (TEXT.has(extension) || dot <= 0) return "text";
    return "other";
}

export const MAX_TEXT_BYTES = 1_000_000;

/** The most the server will return from one read, whatever is asked for.
 *
 *  `_MAX_CONTENT_BYTES` in the files controller. Here so the chunked reader
 *  clamps to it rather than discovering it a slice at a time: asking for more
 *  and advancing the cursor by what was *asked* is how a stitched download
 *  ends up the right length and full of holes.
 */
export const MAX_READ_BYTES = 8 * 1024 * 1024;

/** What the screen says, and what it offers.
 *
 *  Kept out of the component because these are the only three things this app
 *  can truthfully claim about a machine it cannot see: it is stopped, it is
 *  running with nothing on screen, or something is on screen. There is no
 *  fourth state hiding here — `current-page-url` answers only for a named
 *  sign-in session, so "what page is it on" is not a question this app can ask
 *  in general, and the screen does not pretend to know.
 *
 *  Both strings land under the glass rather than on it. They started on it,
 *  and the screensaver wandered straight through the headline — which is the
 *  sort of thing only a running pixel tells you.
 */
export interface ScreenSay {
    /** One word for the state, leading the line under the screen. */
    headline: string;
    note: string;
    /** `wake` starts the machine; `show` puts its display in this pane. One
     *  verb for the display rather than two, because attaching starts a
     *  browser when none is running — so "open a browser" and "watch the one
     *  that is open" were two names for the same click. Null when there is
     *  nothing honest to offer, which is most of the time. */
    action: "wake" | "show" | null;
}

export function screenSay(state: MachineState, browser: BrowserState | undefined): ScreenSay {
    if (state === "checking") {
        return { headline: "Checking", note: "Checking computer status…", action: null };
    }
    if (state === "unreachable") {
        return { headline: "Unknown", note: "This computer could not be reached just now.", action: null };
    }
    if (state === "asleep") {
        return {
            headline: "Asleep",
            /* The second sentence is said here rather than left to be
               discovered, because "my computer turned itself off" reads as
               lost work and is not: releasing stops the compute and keeps the
               disk. */
            note: "This computer is stopped. Wake it to continue.",
            action: "wake",
        };
    }
    if (state === "browsing") {
        return { headline: "In use", note: "A browser is open on it.", action: "show" };
    }
    return {
        headline: "Awake",
        note: watchable(browser) ? "Nothing on screen right now." : "This machine has no screen to show.",
        action: watchable(browser) ? "show" : null,
    };
}
