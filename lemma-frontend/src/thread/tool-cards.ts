/** Reading the tool calls that deserve to be seen.
 *
 *  An agent here has around forty tools. Five of them had a rendering and every
 *  other one collapsed to the same grey line — the tool's name and a
 *  hundred-character summary of its arguments — which is how a terminal session,
 *  a page of search results and a run that is deliberately asleep all ended up
 *  looking identical to each other and to nothing.
 *
 *  Everything here is a read of a **wire value**. The arguments are whatever the
 *  model emitted and the result is whatever the backend serialised, so every
 *  field is guarded and a shape that does not fit returns `null` — which puts
 *  the call back on the generic grey line rather than taking the transcript
 *  down with it.
 *
 *  The return shapes are the backend's, in `app/modules/agent/tools/`:
 *  `browser/models.py`, `workspace_cli/models.py`, `web/models.py`,
 *  `connectors/pydantic_adapter.py` and `snooze/models.py`. A local coding
 *  agent's calls arrive through the Agent Host already in the canonical
 *  vocabulary of `docs/architecture/agent-host-events.md` ("Canonical tools"),
 *  which is where the file, search and sub-agent cards read their shapes. */

import { toolKey, toolTitle } from "./tool-name";

export type ToolCard =
    | SignInAsk
    | BrowserStep
    | TerminalRun
    | SourceList
    | ConnectorRun
    | SnoozeWait
    | ImageLook
    | FileRead
    | FileChange
    | FileSearch
    | SubTask;

/** A paused `browser_sign_in`: the run is stopped until somebody goes and signs
 *  in to a site, in the agent's own browser. */
export interface SignInAsk {
    kind: "sign-in";
    /** As the agent wrote it. Not necessarily a URL — see `host`. */
    origin: string;
    /** The host when `origin` parses, the raw string when it does not. */
    host: string;
    /** Why it is asking, in the agent's words. */
    reason: string;
    /** A tool return landed. Read off the return's *presence*, never off a
     *  decision key: this return carries `outcome`, and a card that waited for
     *  a `decision` that never comes would offer a live link over a pause that
     *  was answered an hour ago. */
    resolved: boolean;
    /** `signed_in`, `declined`, `expired` or `error`. Empty while open. */
    outcome: string;
    signedIn: boolean;
    /** The login was kept, so the next run will not ask. */
    kept: boolean;
}

/** One move of the browser: `browser_open`, `browser_act`, `browser_read`,
 *  `browser_snapshot` or `browser_screenshot`.
 *
 *  Five tools and one card, because four of them return the same model —
 *  `BrowserResult` is `{url, title, snapshot, output, truncated}` — and the
 *  fifth returns a near-sibling of it. Where the page ended up is therefore the
 *  one thing every browser step can always say, and it is said the same way
 *  each time. What differs is the field the call actually went for, which is
 *  what `did` selects: `browser_read` means its `output`, `browser_snapshot`
 *  means its `snapshot`, and a screenshot means neither, for the reason under
 *  `seen`. */
export interface BrowserStep {
    kind: "browser";
    did: "open" | "act" | "read" | "snapshot" | "shot";
    /** The agent's own line about why this call happened. */
    comment: string;
    /** The call as a sentence, in the reader's words rather than the wire's:
     *  the address an open was given, "Clicked @e3", "the console log". */
    what: string;
    /** Where the page was left, off the return. Falls back to the address an
     *  open was pointed at, so a call still in flight can name a host. */
    url: string;
    host: string;
    /** `url` without its host, so the host can carry the head on its own and
     *  the rest can be dropped at a narrow width without losing the site. */
    trail: string;
    /** The page's own name, after the call. */
    title: string;
    /** What the call said it would wait for before reading — `wait_for_url` as
     *  written, or the `wait_for_text` in quotes. Open and act only. */
    awaited: string;
    /** An open that did not land on the address it was given. A redirect is
     *  ordinary and worth saying: a snapshot of a login page when the call
     *  asked for a dashboard is the commonest confusing result there is. */
    moved: boolean;
    /** What a read or a snapshot brought back. The backend has already cut it
     *  at the call's own token ceiling; nothing is re-cut here. */
    body: string;
    /** What `body` amounts to, or what a screenshot weighed — said rather than
     *  printed, so the closed card still answers "how much". */
    size: string;
    /** The backend cut `body` at the limit the call asked for. */
    truncated: boolean;
    /** A screenshot's `instructions`: the question the capture was taken to
     *  answer. */
    asked: string;
    /** A screenshot in words.
     *
     *  There is no image to draw here, and that is the backend's shape rather
     *  than a gap in this app: `screenshot_internal` hands the picture back as
     *  `ToolReturn(content=[BinaryContent(...)])`, which pydantic-ai sends to
     *  the model as a separate prompt part, so what lands in `tool_result` is
     *  only the metadata beside it. The one case with something to read is a
     *  run whose model cannot see — then the tool delegates to
     *  `describe_single_image` and returns a `ViewImageResponse` whose
     *  `message` IS the picture, described. */
    seen: string;
    /** Whether the whole scroll height was captured rather than the viewport. */
    fullPage: boolean;
    pending: boolean;
    failed: boolean;
    error: string;
}

/** One `exec_command` or `execute_python`, with what it printed. */
export interface TerminalRun {
    kind: "terminal";
    language: "shell" | "python";
    /** The command, or the code. Never truncated here — the card decides. */
    command: string;
    /** Where it ran, when the call said so. */
    workdir: string;
    /** The one line the agent wrote about why it ran this. */
    comment: string;
    /** stdout. Already tail-truncated server side at 30k characters, with the
     *  earlier part marked; nothing is re-cut here. */
    output: string;
    /** stderr, or a rendered Python traceback. Kept apart from `output`
     *  because "it printed nothing and failed" and "it printed and failed" are
     *  different facts. */
    errorOutput: string;
    /** `execute_python` only: the last expression's value. */
    value: string;
    /** Lines across both streams, for the closed state. */
    lines: number;
    exitCode?: number;
    /** The call outlived its wait window. The command was not cancelled. */
    running: boolean;
    processId: string;
    /** No tool return yet. */
    pending: boolean;
    failed: boolean;
}

/** One page a search or a fetch turned up. */
export interface Source {
    title: string;
    url: string;
    /** The host, for a reader deciding whether to follow it. */
    host: string;
    snippet: string;
    /** `web_fetch` writes the page into the workspace; this is where. */
    savedAs: string;
    publisher: string;
    published: string;
    failed: boolean;
    error: string;
}

/** `web_search` or `web_fetch`: sources, as things a person can follow. */
export interface SourceList {
    kind: "sources";
    action: "search" | "fetch";
    /** The query, for a search. Empty for a fetch. */
    query: string;
    sources: Source[];
    /** The provider could not run it exactly as asked. */
    note: string;
    /** Whether the return listed its results at all. A local agent's search
     *  answers in prose, or with nothing, and "0 results" over that would
     *  be a count nobody took. */
    listed: boolean;
    /** What came back as text rather than as a list — a local agent's
     *  canonical `{output}`, and the answer to a fetch's `prompt`. */
    text: string;
    /** A fetch's `prompt`: what it went to the page to find out. */
    asked: string;
    /** The whole call failed. */
    error: string;
    pending: boolean;
}

/** A `read_file`: the path, and what was in it. */
export interface FileRead {
    kind: "file-read";
    path: string;
    /** The last segment, which is all a one-line head can fit. */
    name: string;
    /** "from line 40", "lines 40–60", when the call read a window. */
    range: string;
    /** As the return had it; the backend has already bounded it. */
    content: string;
    lines: number;
    pending: boolean;
    failed: boolean;
    error: string;
}

/** One line of a change. `gap` stands for unchanged lines left out between
 *  two hunks, so a one-line fix in a long file is not the whole file. */
export interface DiffLine {
    sign: "+" | "-" | " " | "gap";
    text: string;
}

export interface FileDiff {
    path: string;
    change: "add" | "update" | "delete";
    lines: DiffLine[];
    added: number;
    removed: number;
}

/** `write_file`, `edit_file`, `delete_file` or `move_file`. */
export interface FileChange {
    kind: "file-change";
    action: "write" | "edit" | "delete" | "move";
    path: string;
    name: string;
    /** Where a move put it. */
    destination: string;
    /** One per file touched. An `apply_patch` touches several. */
    files: FileDiff[];
    added: number;
    removed: number;
    /** The tool's own sentence, when it is all the return has. */
    message: string;
    pending: boolean;
    failed: boolean;
    error: string;
}

/** `list_files`, `glob` or `grep`: what was looked for, and what turned up. */
export interface FileSearch {
    kind: "file-search";
    action: "list" | "glob" | "grep";
    /** The glob or the regular expression. Empty for a listing. */
    pattern: string;
    path: string;
    /** A grep's file filter. */
    filter: string;
    /** The adapter's own words, for a call whose arguments are empty — Codex
     *  names a search only in its title. */
    title: string;
    output: string;
    /** Non-empty lines in `output`, which is a match or a file each. */
    count: number;
    pending: boolean;
    failed: boolean;
    error: string;
}

/** A `task`: a sub-agent sent off to do part of the work. */
export interface SubTask {
    kind: "task";
    description: string;
    /** Which kind of sub-agent, when the harness has several. */
    agentType: string;
    prompt: string;
    /** What it came back with. */
    output: string;
    pending: boolean;
    failed: boolean;
    error: string;
}

/** One `run_connector_operation`. */
export interface ConnectorRun {
    kind: "connector";
    /** The install, by its auth-config name — `gmail`, `outlook`. */
    connector: string;
    /** The operation, verbatim: it is an identifier a reader may need to match
     *  against the connector's own list. */
    operation: string;
    account: string;
    params: { name: string; value: string }[];
    /** Where a file result was asked to land in the pod. */
    savedTo: string;
    pending: boolean;
    failed: boolean;
    error: string;
    /** What came back, as one line. The result is arbitrary provider JSON, so
     *  it is described rather than printed. */
    summary: string;
}

/** A `snooze`: the run put itself to sleep. */
export interface SnoozeWait {
    kind: "snooze";
    /** What it is waiting for, in the agent's words. */
    reason: string;
    seconds: number;
    /** Handed back to the agent on wake. */
    note: string;
    /** No return yet — it is still asleep. */
    sleeping: boolean;
    /** `TIMER`, `ANSWERED` or `CANCELLED`. */
    wokeBecause: string;
    sleptSeconds?: number;
    /** When it is due back. Derived from the call's own timestamp and
     *  `seconds`, because no field on either side of the wire carries a wake
     *  time. */
    wakeAtMs?: number;
}

/** A `view_image`: a picture the teammate stopped to look at.
 *
 *  The exception to what the screenshot card says under `seen`. A screenshot's
 *  picture is *made* by the call and leaves as binary tool content, so there is
 *  nothing in the transcript to draw. This one names **a file that already
 *  exists** — `pod_file_path` or `workspace_file_path`, exactly one of them —
 *  and both stores are ones this app can read. So the card can show the same
 *  image the teammate was looking at.
 *
 *  Which store is the whole difficulty, and it is settled off the return:
 *  `ViewImageResponse.source` is `datastore` or `workspace`. The arguments are
 *  the fallback while the call is in flight, and they are only a fallback
 *  because the "exactly one path" rule is enforced in `view_image_internal`
 *  rather than by a validator (`models.py:192`) — a call can arrive with both
 *  set or neither, and neither of those is a file this app can name. */
export interface ImageLook {
    kind: "image";
    /** Empty when the call named two files or none. Nothing is fetched then:
     *  the backend refuses that call, and guessing which of two paths it meant
     *  would put the wrong picture under the agent's own question. */
    store: "pod" | "workspace" | "";
    /** The path, preferring the return's `file_path`. */
    path: string;
    name: string;
    /** The path has no leading slash, so it cannot be fetched as written.
     *
     *  A relative `workspace_file_path` resolves against the *conversation's*
     *  directory — `WorkspaceFileManager._workspace_path` joins it onto its own
     *  `cwd`, which `test_workspace_path_resolution.py` pins — while this app's
     *  route joins the same string onto `/workspace`
     *  (`files_controller._workspace_path`). One string, two different files.
     *  The card carries the fact; resolving it needs a query, so the view does
     *  it. */
    relative: boolean;
    /** `instructions`: what the teammate was looking for in the picture. */
    asked: string;
    /** The picture in words, when the run's own model cannot see.
     *
     *  Same delegation the screenshot card describes: `view_image_internal`
     *  hands the bytes to `describe_single_image`, whose `message` IS the
     *  picture described. A run that *can* see gets the boilerplate
     *  `Successfully read image <path>` instead, which is worth nobody's time
     *  and is dropped. */
    described: string;
    /** What it weighed, said rather than printed. */
    weight: string;
    /** `image/png`, when the return said. */
    mediaType: string;
    pending: boolean;
    failed: boolean;
    error: string;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function asNumber(value: unknown): number | undefined {
    return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** A field off a tool return, flat or wrapped.
 *
 *  Returns arrive at the top level from the agent host and nested under
 *  `output` when the backend replays a resolved pause — `approval.ts` reads its
 *  decision both ways for exactly this reason. */
function resultField(result: unknown, key: string): unknown {
    const top = asRecord(result);
    if (top[key] !== undefined) return top[key];
    return asRecord(top.output)[key];
}

/** A value a person can read, for the places a payload is arbitrary JSON.
 *  A wall of it is the same failure as printing nothing. */
function describe(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) return value.length + (value.length === 1 ? " item" : " items");
    const keys = Object.keys(asRecord(value));
    return keys.length ? "{ " + keys.slice(0, 3).join(", ") + (keys.length > 3 ? ", …" : "") + " }" : "{}";
}

/** The host of a URL the agent wrote. `new URL` throws on anything that is not
 *  absolute, and `origin` comes straight off the model. */
export function hostOf(url: string): string {
    try {
        return new URL(url).host || url;
    } catch {
        return url;
    }
}

/** Everything after the host — path, query, fragment.
 *
 *  Split out so a browser card can put the host first and the rest second: at
 *  375px a head showing one truncated URL says
 *  `https://app.northfield.co/settings/bill…`, and the part it cut is the only
 *  part that was not already obvious. A bare `/` is dropped, because "the
 *  site's front page" is what an empty trail already means. */
export function trailOf(url: string): string {
    try {
        const parsed = new URL(url);
        const rest = parsed.pathname + parsed.search + parsed.hash;
        return rest === "/" ? "" : rest;
    } catch {
        return "";
    }
}

/** Whether two addresses are the same place. Trailing slashes and case differ
 *  between what a model types and what a browser reports for the identical
 *  page, and calling that a redirect would put a note on nearly every open. */
function sameAddress(one: string, other: string): boolean {
    const plain = (value: string) => value.trim().replace(/\/+$/, "").toLowerCase();
    return plain(one) === plain(other);
}

/** How much text came back, in a unit the reader can check against the block
 *  underneath it.
 *
 *  A snapshot is counted in elements when its refs show, because that is the
 *  number that decides whether the agent could act on the page at all — the
 *  plain listing writes them as `@e1` and `agent-browser snapshot --json`
 *  writes them as a `ref` key, and this app sees both. Characters otherwise:
 *  less useful, still true. */
export function bulkOf(text: string, elements = false): string {
    if (!text) return "";
    if (elements) {
        const refs = (text.match(/@e\d+/g) ?? []).length || (text.match(/"ref"\s*:/g) ?? []).length;
        if (refs) return refs + (refs === 1 ? " element" : " elements");
    }
    if (text.length < 1000) return text.length + " characters";
    return (text.length / 1000).toFixed(1).replace(/\.0$/, "") + "k characters";
}

/** What an image weighed. `size_bytes` is the capture before downscaling, so
 *  this is the page's own weight rather than what the model was shown. */
export function weightOf(bytes?: number): string {
    if (bytes === undefined || bytes <= 0) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** "30 seconds", "9 minutes", "2 hours". Rounded on purpose: a sleep is an
 *  intention, and "8m 20s" invites a reader to time it. */
export function restLength(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds <= 0) return "";
    if (seconds < 90) return Math.round(seconds) + (Math.round(seconds) === 1 ? " second" : " seconds");
    const minutes = Math.round(seconds / 60);
    if (minutes < 90) return minutes + (minutes === 1 ? " minute" : " minutes");
    const hours = Math.round(seconds / 360) / 10;
    return hours + (hours === 1 ? " hour" : " hours");
}

function signInCard(args: unknown, result: unknown, answered: boolean): SignInAsk | null {
    const record = asRecord(args);
    const origin = asString(record.origin);
    /* No site means no card: the whole thing it says is "sign in to X", and
       there is no honest X to put in the sentence. */
    if (!origin) return null;
    const outcome = answered ? asString(resultField(result, "outcome")) : "";
    return {
        kind: "sign-in",
        origin,
        host: hostOf(origin),
        reason: asString(record.reason),
        resolved: answered,
        outcome,
        signedIn: outcome === "signed_in",
        kept: resultField(result, "saved") === true,
    };
}

/** A Python traceback as the terminal would have shown it.
 *
 *  `PythonExecutionResult` keeps the exception out of `stderr` and in
 *  `error_in_exec` — `{ ename, evalue, traceback }` — so a run that raised has
 *  two empty streams and all of its news in a field nothing was reading. */
function tracebackOf(value: unknown): string {
    const record = asRecord(value);
    if (!Object.keys(record).length) return "";
    const name = asString(record.ename);
    const detail = asString(record.evalue);
    const trace = Array.isArray(record.traceback) ? record.traceback.filter((line) => typeof line === "string") : [];
    const head = [name, detail].filter(Boolean).join(": ");
    return trace.length ? [head, ...trace].filter(Boolean).join("\n") : head;
}

function countLines(text: string): number {
    if (!text) return 0;
    return text.split("\n").filter((line) => line.trim()).length;
}

function terminalCard(tool: string, args: unknown, result: unknown, answered: boolean): TerminalRun | null {
    const record = asRecord(args);
    const python = tool === "execute_python";
    const command = python ? asString(record.code) : asString(record.cmd);
    /* Without the command there is nothing to collapse *behind* — the card
       would be a box around an exit code. */
    if (!command) return null;

    const output = asString(resultField(result, "stdout"));
    const stderr = asString(resultField(result, "stderr"));
    const traceback = python ? tracebackOf(resultField(result, "error_in_exec")) : "";
    const errorOutput = [stderr, traceback].filter(Boolean).join("\n");
    const failureNote = answered ? asString(resultField(result, "error")) : "";
    const exitCode = asNumber(resultField(result, "exit_code"));
    /* `completed` is false whenever the command outlived the call's wait
       window — a long build as much as an interactive session. It was not
       cancelled, and saying "failed" over a build still running is the worst
       reading of the two. */
    const running = answered && resultField(result, "completed") === false;
    /* Positive evidence only. A missing `success` is a result shape this app
       has not seen, not a command that went wrong, and painting every
       unfamiliar return red would make the one colour that means something
       mean nothing. */
    const broke = exitCode !== undefined ? exitCode !== 0 : resultField(result, "success") === false;

    return {
        kind: "terminal",
        language: python ? "python" : "shell",
        command,
        workdir: asString(record.workdir),
        comment: asString(record.comment),
        output,
        errorOutput: [errorOutput, failureNote].filter(Boolean).join("\n"),
        value: python ? asString(resultField(result, "result")) : "",
        lines: countLines(output) + countLines(errorOutput),
        exitCode,
        running,
        processId: asString(resultField(result, "process_id")),
        pending: !answered,
        failed: answered && !running && broke,
    };
}

function searchSources(result: unknown): Source[] {
    const raw = resultField(result, "results");
    if (!Array.isArray(raw)) return [];
    return raw
        .map((entry): Source => {
            const record = asRecord(entry);
            const url = asString(record.url);
            return {
                title: asString(record.title) || hostOf(url),
                url,
                host: hostOf(url),
                snippet: asString(record.snippet),
                savedAs: "",
                publisher: asString(record.publisher) || asString(record.source),
                published: asString(record.published_at),
                failed: false,
                error: "",
            };
        })
        .filter((source) => source.url);
}

/** The saved markdown if there is one, else whatever format did land. A fetch
 *  can be asked for a PDF and a screenshot as well, and naming only the one
 *  this app happens to prefer would hide the files the agent actually has. */
function savedPath(files: unknown): string {
    const record = asRecord(files);
    const markdown = asString(record.markdown);
    if (markdown) return markdown;
    for (const value of Object.values(record)) {
        const path = asString(value);
        if (path) return path;
    }
    return "";
}

function fetchedSources(args: unknown, result: unknown, answered: boolean): Source[] {
    const raw = resultField(result, "pages");
    if (answered && Array.isArray(raw)) {
        return raw
            .map((entry): Source => {
                const record = asRecord(entry);
                const url = asString(record.url);
                return {
                    title: asString(record.title) || hostOf(url),
                    url,
                    host: hostOf(url),
                    snippet: asString(record.preview),
                    savedAs: savedPath(record.files),
                    publisher: "",
                    published: "",
                    failed: record.success === false,
                    error: asString(record.error),
                };
            })
            .filter((source) => source.url);
    }
    /* Still in flight, or a local agent's fetch, which returns text rather
       than pages. The urls asked for are the honest stand-in: a fetch of
       five pages takes minutes, and an empty card for the length of it says
       less than the generic grey line would. A local agent names one `url`. */
    const record = asRecord(args);
    const urls = Array.isArray(record.urls) ? record.urls : asString(record.url) ? [record.url] : null;
    if (!urls) return [];
    return urls
        .map((value) => asString(value))
        .filter(Boolean)
        .map((url) => ({
            title: hostOf(url),
            url,
            host: hostOf(url),
            snippet: "",
            savedAs: "",
            publisher: "",
            published: "",
            failed: false,
            error: "",
        }));
}

/** A return's text, for the canonical tools that answer `{output}` — or a
 *  bare string, which a bounded value can be. */
function outputText(result: unknown): string {
    if (typeof result === "string") return result.trim();
    const output = resultField(result, "output");
    return typeof output === "string" ? output.trim() : "";
}

/** Results a search wrote as JSON text. Some search tools answer the model
 *  with the JSON rather than with an object, and the list inside is worth
 *  more than the brace-soup around it. */
function resultsInText(text: string): unknown {
    if (!text.startsWith("{")) return undefined;
    try {
        return asRecord(JSON.parse(text)).results;
    } catch {
        return undefined;
    }
}

function sourcesCard(tool: string, args: unknown, result: unknown, answered: boolean): SourceList | null {
    const record = asRecord(args);
    const search = tool === "web_search";
    const query = asString(record.query);
    const text = answered ? outputText(result) : "";
    const embedded = search && text ? resultsInText(text) : undefined;
    const listing = embedded !== undefined ? { results: embedded } : result;
    const listed = search ? Array.isArray(resultField(listing, "results")) : true;
    const sources = search ? (answered ? searchSources(listing) : []) : fetchedSources(args, result, answered);

    /* A search with no query and a fetch with no urls are both calls this
       cannot name, and a card headed "Searched the web" over nothing is worse
       than the line it replaced. */
    if (search && !query) return null;
    if (!search && !sources.length && !answered) return null;

    return {
        kind: "sources",
        action: search ? "search" : "fetch",
        query,
        sources,
        note: answered ? asString(resultField(result, "note")) : "",
        listed,
        text: embedded !== undefined ? "" : text,
        asked: search ? "" : asString(record.prompt),
        error: answered ? asString(resultField(result, "error")) : "",
        pending: !answered,
    };
}

function connectorCard(args: unknown, result: unknown, answered: boolean): ConnectorRun | null {
    const record = asRecord(args);
    const connector = asString(record.auth_config);
    const operation = asString(record.operation);
    if (!connector || !operation) return null;

    const params = Object.entries(asRecord(record.arguments))
        .map(([name, value]) => ({ name, value: describe(value) }))
        .filter((param) => param.value !== "")
        .slice(0, 5);

    /* Failure here is a dictionary with `error` and `message` in it, not an
       exception — a connector that refuses an argument is information for the
       model rather than the end of the run. */
    const error = answered ? asString(resultField(result, "error")) : "";
    const message = answered ? asString(resultField(result, "message")) : "";

    return {
        kind: "connector",
        connector,
        operation,
        account: asString(record.account_id),
        params,
        savedTo: asString(record.output_path),
        pending: !answered,
        failed: Boolean(error),
        error: message || error,
        summary: error ? "" : describe(resultField(result, "result")),
    };
}

function snoozeCard(args: unknown, result: unknown, answered: boolean, atMs?: number): SnoozeWait | null {
    const record = asRecord(args);
    const seconds = asNumber(record.seconds);
    const reason = asString(record.reason);
    /* Both are required by the request model, so a call missing either is not
       a snooze this app can describe. */
    if (seconds === undefined || !reason) return null;

    return {
        kind: "snooze",
        reason,
        seconds,
        note: asString(record.note_to_self) || asString(resultField(result, "note_to_self")),
        sleeping: !answered,
        wokeBecause: answered ? asString(resultField(result, "woke_because")) : "",
        sleptSeconds: answered ? asNumber(resultField(result, "slept_seconds")) : undefined,
        wakeAtMs: atMs === undefined ? undefined : atMs + seconds * 1000,
    };
}

/* ── a picture the teammate stopped to look at ───────────────────────── */

/** What `view_image` says when the model could see the image itself.
 *
 *  `view_image_internal` writes `f"Successfully read image {file_path}"` on the
 *  direct-vision return and the vision model's own prose on the delegated one,
 *  and there is no flag distinguishing them — so the boilerplate is matched and
 *  dropped, and everything else is taken as a description. Matched at the head
 *  only, because the rest of that string is the path and the path is already on
 *  the card. */
function describedBy(message: string): string {
    return message && !/^Successfully read image\b/.test(message) ? message : "";
}

function imageCard(args: unknown, result: unknown, answered: boolean): ImageLook | null {
    const record = asRecord(args);
    const podPath = asString(record.pod_file_path);
    const workspacePath = asString(record.workspace_file_path);

    /* The store, off the return first. `view_image_internal` picks it from
       whichever argument the agent set and echoes the choice back as `source`,
       so once the return has landed there is nothing left to infer — and no
       inference is attempted from the path's shape, because the backend
       explicitly refuses to do that ("no path-shape inference"). */
    const told = asString(resultField(result, "source"));
    const store: ImageLook["store"] =
        told === "datastore"
            ? "pod"
            : told === "workspace"
              ? "workspace"
              : podPath && !workspacePath
                ? "pod"
                : workspacePath && !podPath
                  ? "workspace"
                  : "";

    /* The return's path over the argument, because the argument is what the
       model typed and this is what the tool went and read.
     *
     *  It is a weaker preference than `ViewImageResponse.file_path`'s own
     *  description ("Resolved file path of the image that was loaded") makes it
     *  sound: `view_image_internal` assigns `file_path = workspace_path` and
     *  hands it straight back, so a relative argument comes back relative. That
     *  is what `relative` below is for. */
    const path =
        asString(resultField(result, "file_path")) ||
        (store === "pod" ? podPath : store === "workspace" ? workspacePath : podPath || workspacePath);
    /* Neither path set: the backend answers `success=False` and there is no
       file to name, so this drops to the grey step like any other call this
       cannot read. */
    if (!path) return null;

    const error = answered ? asString(resultField(result, "error")) : "";

    return {
        kind: "image",
        store,
        path,
        name: path.split("/").filter(Boolean).pop() ?? path,
        relative: !path.startsWith("/"),
        asked: asString(record.instructions),
        described: answered ? describedBy(asString(resultField(result, "message"))) : "",
        /* `size_bytes` is the file as it sits on disk on the direct-vision
           return and the downscaled payload on the delegated one, so this is
           "about this big" rather than a figure to check a directory listing
           against. */
        weight: weightOf(asNumber(resultField(result, "size_bytes"))),
        mediaType: asString(resultField(result, "media_type")),
        pending: !answered,
        /* Positive evidence, the rule the terminal and browser cards follow.
           A `view_image` that was refused a grant comes back through
           `approval_error_result` and still writes a sentence into `error`. */
        failed: answered && (Boolean(error) || resultField(result, "success") === false),
        error,
    };
}

/* ── the browser, in five moves ──────────────────────────────────────── */

/** A string that will sit on one line of a head. */
function short(text: string, limit: number): string {
    return text.length > limit ? text.slice(0, limit - 1).trimEnd() + "…" : text;
}

/** One `browser_act` as something a person reads.
 *
 *  The nine actions are a closed `Literal` on `BrowserActRequest`, and each
 *  reads off a different argument — a fill has `text`, a press has `key`, a
 *  scroll has neither — so there is no one field to print. An action outside
 *  the nine returns empty, which drops the call back to the grey step: the
 *  backend would have rejected it too. */
function actLine(record: Record<string, unknown>): string {
    const target = asString(record.target);
    const text = short(asString(record.text), 60);
    switch (asString(record.action).toLowerCase()) {
        case "click":
            return "Clicked " + (target || "the page");
        case "fill":
            return "Filled " + target + " with “" + text + "”";
        case "type":
            return "Typed “" + text + "” into " + target;
        case "press":
            return "Pressed " + (asString(record.key) || "a key");
        case "select":
            return "Chose “" + text + "” in " + target;
        case "check":
            return "Ticked " + target;
        case "uncheck":
            return "Unticked " + target;
        case "hover":
            return "Hovered " + target;
        case "scroll": {
            const amount = asNumber(record.scroll_amount);
            return "Scrolled " + (asString(record.scroll_direction) || "down") + (amount ? " " + amount + "px" : "");
        }
        default:
            return "";
    }
}

/** What a `browser_read` went for, named as the thing rather than as the enum.
 *
 *  `what` is seven values on `BrowserReadRequest` and two of them are not the
 *  page at all: `console` and `network` are the logs behind a blank screen,
 *  and a card that called those "read the page" would hide the one call in a
 *  debugging run that explains the others. */
function readLine(record: Record<string, unknown>): string {
    const target = asString(record.target);
    switch (asString(record.what).toLowerCase()) {
        case "text":
            return target ? "the text of " + target : "the text";
        case "html":
            return target ? "the HTML of " + target : "the page’s HTML";
        case "url":
            return "the address";
        case "title":
            return "the title";
        case "attr":
            return (asString(record.attribute) || "an attribute") + " of " + (target || "an element");
        case "console":
            return "the console log";
        case "network":
            return "the network log";
        default:
            return "";
    }
}

/** `wait_for_url` as written, or `wait_for_text` in quotes. Both say the same
 *  thing — the agent knew the page was about to change and named what it was
 *  waiting for — and a card that showed only the first would go quiet on the
 *  half of the calls that wait on a success message instead. */
function awaitedBy(record: Record<string, unknown>): string {
    const url = asString(record.wait_for_url);
    if (url) return url;
    const text = asString(record.wait_for_text);
    return text ? "the text “" + short(text, 60) + "”" : "";
}

/** The five browser calls, read against the return each one actually has.
 *
 *  Everything below the switch is shared because it is genuinely the same
 *  field on the same model: `url`, `title`, `error` and `success` come from
 *  `BrowserResult` for four of these and from `BrowserScreenshotResponse` for
 *  the fifth, which extends the same `BaseToolResponse`. */
function browserCard(did: BrowserStep["did"], args: unknown, result: unknown, answered: boolean): BrowserStep | null {
    const record = asRecord(args);

    /* A screenshot has two possible shapes, and which one arrived decides
       whether `message` is worth a reader's time. With vision it is the
       boilerplate "Screenshot of <url>."; without it, it is the description a
       vision model wrote. `file_path` is the tell — `BrowserScreenshotResponse`
       has no such field, and `ViewImageResponse` always sets it. */
    const describedAt = did === "shot" ? asString(resultField(result, "file_path")) : "";
    /* …and that same field is the only address a described screenshot carries,
       because `ViewImageResponse` has no `url`. `describe_single_image` is
       handed `parsed.url or path`, so it is the page when there was one and a
       `/tmp` capture path when there was not. */
    const landed =
        asString(resultField(result, "url")) || (describedAt.startsWith("http") ? describedAt : "");

    let what = "";
    let body = "";
    let size = "";
    switch (did) {
        case "open":
            what = asString(record.url);
            break;
        case "act":
            what = actLine(record);
            break;
        case "read":
            what = readLine(record);
            body = asString(resultField(result, "output"));
            /* A read of the address or the title comes back in a dozen
               characters, and "14 characters" above fourteen characters of
               text is a label longer than the thing it measures. */
            size = body.length > 120 ? bulkOf(body) : "";
            break;
        case "snapshot":
            what = record.interactive_only === false ? "the whole page tree" : "the elements it can act on";
            body = asString(resultField(result, "snapshot"));
            size = bulkOf(body, true);
            break;
        case "shot":
            what = record.full_page === true ? "the whole page" : "the viewport";
            size = weightOf(asNumber(resultField(result, "size_bytes")));
            break;
    }

    /* An act whose action is not one of the nine, or a read with no `what`,
       is a call this cannot name — and a card headed "Did something to the
       page" is worse than the grey line it would replace. An open with no
       address is the same case. */
    if (!what) return null;

    const asked = did === "open" ? what : "";
    const url = landed || asked;
    const error = answered ? asString(resultField(result, "error")) : "";

    return {
        kind: "browser",
        did,
        comment: asString(record.comment),
        what,
        url,
        host: hostOf(url),
        trail: trailOf(url),
        title: asString(resultField(result, "title")),
        awaited: did === "open" || did === "act" ? awaitedBy(record) : "",
        moved: Boolean(asked && landed && !sameAddress(asked, landed)),
        body,
        size,
        truncated: resultField(result, "truncated") === true,
        asked: did === "shot" ? asString(record.instructions) : "",
        seen: describedAt ? asString(resultField(result, "message")) : "",
        fullPage: record.full_page === true || resultField(result, "full_page") === true,
        pending: !answered,
        /* Positive evidence, the same rule the terminal card follows: these
           tools all extend `BaseToolResponse`, so a genuine failure sets
           `success` false and writes a sentence into `error`. */
        failed: answered && (Boolean(error) || resultField(result, "success") === false),
        error,
    };
}

/* ── a local agent's own tools, in the canonical vocabulary ────────── */

/** Whether a canonical call failed, and in whose words. The backend keeps a
 *  failed call's output and adds `success: false` and a sentence in `error`
 *  (`tool_events.tool_result_value`), so both are positive evidence. */
function failureOf(result: unknown, answered: boolean): { failed: boolean; error: string } {
    if (!answered) return { failed: false, error: "" };
    const error = asString(resultField(result, "error"));
    return { failed: Boolean(error) || resultField(result, "success") === false, error };
}

function nameOf(path: string): string {
    return path.split("/").filter(Boolean).pop() ?? path;
}

function readCard(args: unknown, result: unknown, answered: boolean): FileRead | null {
    const record = asRecord(args);
    const path = asString(record.file_path) || asString(record.path);
    if (!path) return null;
    const offset = asNumber(record.offset);
    const limit = asNumber(record.limit);
    const range =
        offset !== undefined && limit !== undefined
            ? "lines " + offset + "–" + (offset + limit)
            : offset !== undefined
              ? "from line " + offset
              : limit !== undefined
                ? "first " + limit + " lines"
                : "";
    const raw = typeof result === "string" ? result : resultField(result, "content");
    const content = answered && typeof raw === "string" ? raw : "";
    return {
        kind: "file-read",
        path,
        name: nameOf(path),
        range,
        content,
        lines: content ? content.replace(/\n$/, "").split("\n").length : 0,
        pending: !answered,
        ...failureOf(result, answered),
    };
}

function linesOf(text: string): string[] {
    return text ? text.replace(/\n$/, "").split("\n") : [];
}

/** Past this many line pairs a change is shown as everything out and
 *  everything in, rather than aligned. The alignment is quadratic, and a
 *  rewrite of a long file is a rewrite whichever way it is drawn. */
const ALIGN_LIMIT = 250_000;
/** Unchanged lines kept either side of a change. */
const CONTEXT = 3;

/** A line diff of two texts.
 *
 *  Common head and tail first, which is nearly all of an ordinary edit, then
 *  a longest-common-subsequence alignment of what is left. Long unchanged
 *  runs are folded to a gap, so the card shows what changed and not the
 *  file. */
export function diffLines(before: string, after: string): DiffLine[] {
    const old = linesOf(before);
    const next = linesOf(after);
    let head = 0;
    while (head < old.length && head < next.length && old[head] === next[head]) head += 1;
    let tail = 0;
    while (
        tail < old.length - head &&
        tail < next.length - head &&
        old[old.length - 1 - tail] === next[next.length - 1 - tail]
    ) {
        tail += 1;
    }
    const a = old.slice(head, old.length - tail);
    const b = next.slice(head, next.length - tail);

    const middle: DiffLine[] = [];
    if (a.length * b.length > ALIGN_LIMIT) {
        middle.push(...a.map((text) => ({ sign: "-" as const, text })), ...b.map((text) => ({ sign: "+" as const, text })));
    } else {
        const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
        for (let i = a.length - 1; i >= 0; i -= 1) {
            for (let j = b.length - 1; j >= 0; j -= 1) {
                table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
            }
        }
        let i = 0;
        let j = 0;
        while (i < a.length || j < b.length) {
            if (i < a.length && j < b.length && a[i] === b[j]) {
                middle.push({ sign: " ", text: a[i] });
                i += 1;
                j += 1;
            } else if (j < b.length && (i >= a.length || table[i][j + 1] > table[i + 1][j])) {
                /* Strictly greater, so a replaced line reads as the old one
                   out and then the new one in, the order a reviewer expects. */
                middle.push({ sign: "+", text: b[j] });
                j += 1;
            } else {
                middle.push({ sign: "-", text: a[i] });
                i += 1;
            }
        }
    }

    const all: DiffLine[] = [
        ...old.slice(0, head).map((text) => ({ sign: " " as const, text })),
        ...middle,
        ...old.slice(old.length - tail).map((text) => ({ sign: " " as const, text })),
    ];
    const near = all.map((_line, index) =>
        all.slice(Math.max(0, index - CONTEXT), index + CONTEXT + 1).some((other) => other.sign === "+" || other.sign === "-"),
    );
    const shown: DiffLine[] = [];
    all.forEach((line, index) => {
        if (near[index]) shown.push(line);
        else if (shown.at(-1)?.sign !== "gap") shown.push({ sign: "gap", text: "" });
    });
    return shown;
}

function fileDiff(path: string, change: FileDiff["change"], before: string, after: string): FileDiff {
    const lines = diffLines(before, after);
    return {
        path,
        change,
        lines,
        added: lines.filter((line) => line.sign === "+").length,
        removed: lines.filter((line) => line.sign === "-").length,
    };
}

/** `changes: [{file_path, kind, old_text, new_text}]`, off the return first
 *  (what was applied) and the arguments second (what was asked for). */
function diffsOf(value: unknown): FileDiff[] {
    if (!Array.isArray(value)) return [];
    return value
        .map((entry): FileDiff | null => {
            const record = asRecord(entry);
            const path = asString(record.file_path) || asString(record.path);
            if (!path) return null;
            const kind = asString(record.kind);
            const change: FileDiff["change"] = kind === "add" || kind === "delete" ? kind : "update";
            const before = typeof record.old_text === "string" ? record.old_text : "";
            const after = typeof record.new_text === "string" ? record.new_text : "";
            return fileDiff(path, change, change === "add" ? "" : before, change === "delete" ? "" : after);
        })
        .filter((diff): diff is FileDiff => diff !== null);
}

function changeCard(
    action: FileChange["action"],
    args: unknown,
    result: unknown,
    answered: boolean,
): FileChange | null {
    const record = asRecord(args);
    const source = asString(record.source);
    const path = action === "move" ? source : asString(record.file_path) || asString(record.path);

    let files = diffsOf(resultField(result, "changes"));
    if (!files.length) files = diffsOf(record.changes);
    if (!files.length && action === "write" && typeof record.content === "string") {
        files = [fileDiff(path, "add", "", record.content)];
    }
    if (!files.length && action === "edit" && (typeof record.old_string === "string" || typeof record.new_string === "string")) {
        const raw = (value: unknown) => (typeof value === "string" ? value : "");
        files = [fileDiff(path, "update", raw(record.old_string), raw(record.new_string))];
    }

    const named = path || files[0]?.path || "";
    if (!named) return null;
    return {
        kind: "file-change",
        action,
        path: named,
        name: files.length > 1 ? files.length + " files" : nameOf(named),
        destination: action === "move" ? asString(record.destination) : "",
        files,
        added: files.reduce((sum, file) => sum + file.added, 0),
        removed: files.reduce((sum, file) => sum + file.removed, 0),
        message: answered ? asString(resultField(result, "message")) : "",
        pending: !answered,
        ...failureOf(result, answered),
    };
}

function searchCard(
    action: FileSearch["action"],
    args: unknown,
    result: unknown,
    answered: boolean,
    title: string,
): FileSearch {
    const record = asRecord(args);
    const output = answered ? outputText(result) : "";
    return {
        kind: "file-search",
        action,
        pattern: action === "list" ? "" : asString(record.pattern),
        path: asString(record.path),
        filter: action === "grep" ? asString(record.glob) : "",
        title,
        output,
        count: output ? output.split("\n").filter((line) => line.trim()).length : 0,
        pending: !answered,
        ...failureOf(result, answered),
    };
}

function taskCard(args: unknown, result: unknown, answered: boolean): SubTask | null {
    const record = asRecord(args);
    const description = asString(record.description);
    const prompt = asString(record.prompt);
    if (!description && !prompt) return null;
    return {
        kind: "task",
        description: description || prompt.split("\n")[0],
        agentType: asString(record.subagent_type),
        prompt,
        output: answered ? outputText(result) : "",
        pending: !answered,
        ...failureOf(result, answered),
    };
}

/** The one entry point: a tool call, and the card it deserves — or `null`,
 *  which is the existing grey note and has to stay reachable for every one of
 *  the thirty-odd tools nothing here claims. */
export function parseToolCard({
    toolName,
    args,
    result,
    answered,
    atMs,
    metadata,
}: {
    toolName: unknown;
    args: unknown;
    /** The matching TOOL_RETURN's `tool_result`, when one has landed. */
    result?: unknown;
    /** Whether a TOOL_RETURN landed at all. Distinct from `result` being
     *  empty: a tool can return `{}` and be finished. */
    answered: boolean;
    /** The call's own timestamp, which is the only clock a snooze has. */
    atMs?: number;
    /** The call message's metadata. It says whose tool this is
     *  (`tool_source`), which is what keeps somebody's MCP `web_search` off
     *  Lemma's card, and carries the adapter's own title. */
    metadata?: Record<string, unknown> | null;
}): ToolCard | null {
    switch (toolKey(toolName, metadata)) {
        case "browser_sign_in":
            return signInCard(args, result, answered);
        case "browser_open":
            return browserCard("open", args, result, answered);
        case "browser_act":
            return browserCard("act", args, result, answered);
        case "browser_read":
            return browserCard("read", args, result, answered);
        case "browser_snapshot":
            return browserCard("snapshot", args, result, answered);
        case "browser_screenshot":
            return browserCard("shot", args, result, answered);
        case "view_image":
            return imageCard(args, result, answered);
        case "exec_command":
            return terminalCard("exec_command", args, result, answered);
        case "execute_python":
            return terminalCard("execute_python", args, result, answered);
        case "web_search":
            return sourcesCard("web_search", args, result, answered);
        case "web_fetch":
            return sourcesCard("web_fetch", args, result, answered);
        case "run_connector_operation":
            return connectorCard(args, result, answered);
        case "snooze":
            return snoozeCard(args, result, answered, atMs);
        case "read_file":
            return readCard(args, result, answered);
        case "write_file":
            return changeCard("write", args, result, answered);
        case "edit_file":
            return changeCard("edit", args, result, answered);
        case "delete_file":
            return changeCard("delete", args, result, answered);
        case "move_file":
            return changeCard("move", args, result, answered);
        case "list_files":
            return searchCard("list", args, result, answered, toolTitle(metadata));
        case "glob":
            return searchCard("glob", args, result, answered, toolTitle(metadata));
        case "grep":
            return searchCard("grep", args, result, answered, toolTitle(metadata));
        case "task":
            return taskCard(args, result, answered);
        default:
            return null;
    }
}
