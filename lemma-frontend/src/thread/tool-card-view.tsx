import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
    BrowserIcon,
    ChevronDownIcon,
    ChevronRightIcon,
    ClockIcon,
    AgentIcon,
    CodeIcon,
    ConnectorIcon,
    DeleteIcon,
    EditIcon,
    FileIcon,
    FolderIcon,
    MoveIcon,
    SearchIcon,
    ExternalIcon,
    GlobeIcon,
    ImageIcon,
    PointerIcon,
    TerminalIcon,
    TextIcon,
    TreeIcon,
} from "@/ui/icons";
import { Mark } from "@/shell/mark";
import { source } from "@/data";
import { live } from "@/usage/queries";
import { Modal } from "@/shell/modal";
import { SignInPane } from "@/computer/sign-in-pane";
import { useConversationDirectory, useFileBody } from "@/computer/queries";
import { clockOf } from "./turns";
import {
    restLength,
    type BrowserStep,
    type ConnectorRun,
    type FileChange,
    type FileRead,
    type FileSearch,
    type ImageLook,
    type SignInAsk,
    type SnoozeWait,
    type SourceList,
    type SubTask,
    type TerminalRun,
    type ToolCard,
} from "./tool-cards";

/** The tools this app reads instead of summarising.
 *
 *  One shell for all of them, on purpose. These are six unrelated tools and
 *  they were six unrelated grey lines; six unrelated cards would only move the
 *  problem, because the thing a reader is doing is scanning past work to find
 *  the answer. So they share a head — a glyph, what happened, and a status on
 *  the right — and differ only below it, where the actual content is.
 *
 *  Closed is the default everywhere the content can be long. A run that greps
 *  a repository forty times is normal, and forty open terminal blocks is not a
 *  transcript any more. */

/** The status on the right of a head. `tone` is only ever set on something a
 *  reader would want to find by scanning: a failure, or a wait. */
function Status({ text, tone }: { text: string; tone?: "bad" | "wait" | "ok" }) {
    if (!text) return null;
    return <span className="toolcard__status" data-tone={tone}>{text}</span>;
}

function Head({
    icon,
    what,
    meta,
    status,
    tone,
    open,
    onToggle,
}: {
    icon: ReactNode;
    what: ReactNode;
    meta?: string;
    status: string;
    tone?: "bad" | "wait" | "ok";
    open?: boolean;
    onToggle?: () => void;
}) {
    const inside = (
        <>
            <span className="toolcard__glyph">{icon}</span>
            <span className="toolcard__what">{what}</span>
            {meta && <span className="toolcard__meta">{meta}</span>}
            <Status text={status} tone={tone} />
            {onToggle && (
                <span className="toolcard__chev">
                    {open ? <ChevronDownIcon size={12} /> : <ChevronRightIcon size={12} />}
                </span>
            )}
        </>
    );
    if (!onToggle) return <div className="toolcard__head">{inside}</div>;
    return (
        <button type="button" className="toolcard__head toolcard__head--act" aria-expanded={open} onClick={onToggle}>
            {inside}
        </button>
    );
}

/** The site's own icon, built from the host and nothing else.
 *
 *  Host alone is the requirement, not a simplification: the URL has to be
 *  byte-identical at every mention of a domain or the browser's cache cannot
 *  collapse them, and a search card with eight results across three domains
 *  should make three requests rather than eight.
 *
 *  Guarded because `host` is whatever `hostOf` could make of a string a model
 *  wrote, and that is not always a host. A sandbox app at
 *  `http://127.0.0.1:5173` — which `browser_open` is explicitly told to use —
 *  has no favicon worth asking for, and neither does a bare `localhost`. */
function faviconOf(host: string): string | null {
    return /^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(:\d+)?$/i.test(host) ? "https://" + host + "/favicon.ico" : null;
}

/** A host, wearing something you can recognise it by.
 *
 *  The site's real favicon when it has one at `/favicon.ico`, and a mark
 *  generated from the host when it does not — which is often, because plenty
 *  of sites declare their icon elsewhere with `<link rel="icon">` and we
 *  cannot see that without fetching and parsing the page.
 *
 *  `Mark` is the right primitive for exactly that reason: it already holds a
 *  `broken` state, so the miss is not a failure mode but the other half of one
 *  code path. Either way the reader gets something stable and distinctive to
 *  scan by — the second time northfield.co appears in a run, they know it
 *  without reading it. */
function Site({ host, size = 14 }: { host: string; size?: number }) {
    if (!host) return null;
    return <Mark seed={host} name={host} icon={faviconOf(host)} size={size} />;
}

/* ── the browser, in five moves ──────────────────────────────────────── */

const BROWSER_GLYPH: Record<BrowserStep["did"], ReactNode> = {
    open: <BrowserIcon size={14} />,
    act: <PointerIcon size={14} />,
    read: <TextIcon size={14} />,
    snapshot: <TreeIcon size={14} />,
    shot: <ImageIcon size={14} />,
};

/** The head's line.
 *
 *  An open is *the page it reached*, so the site is the headline and gets the
 *  mark in the glyph slot. The other four are things done to a page that has
 *  already been named a step above, so the move is the headline and the site
 *  moves down into the body. */
function browserLine(step: BrowserStep): ReactNode {
    switch (step.did) {
        case "open":
            return (
                <>
                    <span className="toolcard__host">{step.host || step.what}</span>
                    {step.trail && <span className="toolcard__trail">{step.trail}</span>}
                </>
            );
        case "read":
            return "Read " + step.what;
        case "snapshot":
            return "Mapped " + step.what;
        case "shot":
            return "Screenshot of " + (step.host || "the page");
        default:
            return step.what;
    }
}

function browserMeta(step: BrowserStep): string | undefined {
    if (step.did === "open") return step.title || undefined;
    /* Where the act left the page. The host rather than the whole address:
       this line is already the second label on the row and truncating it to
       `…/settings/billing?ta` would name nothing. */
    if (step.did === "act") return step.host || undefined;
    if (step.did === "shot") return [step.what, step.size].filter(Boolean).join(" · ") || undefined;
    return step.size || undefined;
}

function BrowserCard({ step }: { step: BrowserStep }) {
    const [open, setOpen] = useState(false);
    const status = step.pending ? "running" : step.failed ? "failed" : step.truncated ? "cut short" : "";
    const tone = step.failed ? "bad" : step.pending ? "wait" : undefined;
    /* Whether the body would draw anything. A chevron that opens onto an empty
       bordered strip is the same broken promise the sign-in card avoids. */
    const more =
        Boolean(step.comment || step.error || step.body || step.asked || step.seen || step.awaited || step.title) ||
        step.did === "shot" ||
        (step.did !== "open" && Boolean(step.url));

    return (
        <section className="toolcard toolcard--web">
            <Head
                icon={step.did === "open" && step.host ? <Site host={step.host} /> : BROWSER_GLYPH[step.did]}
                what={browserLine(step)}
                meta={browserMeta(step)}
                status={status}
                tone={tone}
                open={more ? open : undefined}
                onToggle={more ? () => setOpen((was) => !was) : undefined}
            />
            {more && open && (
                <div className="toolcard__body">
                    {step.comment && <p className="toolcard__note">{step.comment}</p>}
                    {step.error && <p className="toolcard__note" data-tone="bad">{step.error}</p>}
                    {/* The address whole. The head carries only the host, and
                        at 375px it drops the path as well. */}
                    {step.url && (
                        <p className="toolcard__page">
                            <Site host={step.host} size={14} />
                            <span>{step.url}</span>
                        </p>
                    )}
                    {step.title && <p className="toolcard__note">{step.title}</p>}
                    {step.awaited && <p className="toolcard__where">waited for {step.awaited}</p>}
                    {/* A redirect, named. A snapshot of a login page when the
                        call asked for a dashboard is the most confusing result
                        this tool produces, and the payload can prove it. */}
                    {step.moved && <p className="toolcard__note">Asked for {step.what}, and ended up here.</p>}
                    {step.did === "shot" && (
                        <>
                            {step.asked && <p className="toolcard__note">Looking for: {step.asked}</p>}
                            {step.seen ? (
                                /* The capture in words. This only exists when
                                   the run's own model cannot see images, and
                                   the tool handed the picture to one that can. */
                                <p className="toolcard__note">{step.seen}</p>
                            ) : (
                                !step.failed &&
                                !step.pending && (
                                    <p className="toolcard__fine">
                                        Captured {step.fullPage ? "the whole page" : "the viewport"}
                                        {step.size ? ", " + step.size : ""}. Preview unavailable in this conversation.
                                    </p>
                                )
                            )}
                        </>
                    )}
                    {step.body && <pre className="toolcard__out">{step.body}</pre>}
                    {step.truncated && <p className="toolcard__fine">Cut at the size this call asked for.</p>}
                    {step.pending && <p className="toolcard__note">Nothing has come back yet.</p>}
                </div>
            )}
        </section>
    );
}

/* ── a picture the teammate stopped to look at ───────────────────────── */

/** A pod image, by signed URL.
 *
 *  `source.readFile` answers with a short-lived `rawUrl` and the browser
 *  fetches the bytes itself — the same route `file-view.tsx` takes for a
 *  datastore image, and for its reason: pulling a blob in would mean owning an
 *  object URL with a shorter life than the cache entry holding it, which is a
 *  bug that file already carries the scar of.
 *
 *  Same query key as `FileView`, so a picture already open on the stage costs
 *  nothing here. */
function PodImage({ podId, path, name }: { podId: string; path: string; name: string }) {
    /* A signed URL expires, and an expired one fails as an image rather than
       as a request — the same reason `FileView` keeps this flag. */
    const [broke, setBroke] = useState(false);
    const file = useQuery({
        queryKey: ["file", podId, path],
        queryFn: () => source.readFile(podId, path),
        staleTime: 5 * 60_000,
    });

    if (file.isPending) return <p className="toolcard__note" role="status">Fetching it…</p>;
    const url = file.data?.rawUrl;
    if (file.isError || !url || broke) {
        return (
            <p className="toolcard__fine">
                That file could not be fetched. It may have moved, or you may not have access to it.
            </p>
        );
    }
    return <img className="toolcard__shot" src={url} alt={name} onError={() => setBroke(true)} />;
}

/** A workspace image, as bytes.
 *
 *  The sandbox mints no signed URLs, so this goes through `useFileBody` — which
 *  streams `/workspace/files:content` and hands back a blob — and therefore
 *  owns an object URL and revokes it, the pattern `computer-view.tsx` uses on
 *  the same hook.
 *
 *  This is also the call that **wakes the machine**, deliberately and by
 *  documented design (`computer/queries.ts`: looking must not start a machine,
 *  reading is asking for something). Which is why nothing in this card is
 *  fetched until somebody clicks: a transcript holding thirty of these would
 *  otherwise wake a sandbox and pull thirty images on render. */
function WorkspaceImage({ path, name }: { path: string; name: string }) {
    const body = useFileBody(path);
    const blob = body.data?.blob;
    const href = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
    useEffect(() => () => { if (href) URL.revokeObjectURL(href); }, [href]);

    if (body.isPending) return <p className="toolcard__note" role="status">Reading it off the computer…</p>;
    if (body.isError || !href) {
        return (
            <p className="toolcard__fine">
                That file could not be read. It may have been written over since the teammate looked at it.{" "}
                <button type="button" className="toolcard__more" onClick={() => void body.refetch()}>
                    Try again
                </button>
            </p>
        );
    }
    return <img className="toolcard__shot" src={href} alt={name} />;
}

/** The workspace path, made absolute before anything is fetched.
 *
 *  A relative `workspace_file_path` means two different files depending on who
 *  reads it. The agent's own manager joins it onto the conversation's working
 *  directory — `/workspace/c/{date}/{slug}` — which
 *  `test_workspace_path_resolution.py` pins; this app's route joins the same
 *  string onto `/workspace` (`files_controller._workspace_path`). So
 *  `images/output.png` fetched as written is either a 404 or, worse, somebody
 *  else's `images/output.png`.
 *
 *  The return does not settle it: `ViewImageResponse.file_path` is described as
 *  the resolved path, but `view_image_internal` assigns it the argument
 *  verbatim and never absolutises it. So the directory is read from the
 *  conversation — the same query the computer pane opens on — and when it
 *  cannot be had, the card says so instead of showing a file it cannot vouch
 *  for. */
function WorkspaceShot({
    look,
    podId,
    conversationId,
}: {
    look: ImageLook;
    podId: string;
    conversationId?: string | null;
}) {
    /* Asked for only when it is both needed and answerable. A disabled query
       is permanently pending, and a card that sat on "working out where that
       is…" forever would be the worse half of the same bug. */
    const askable = look.relative && live() && Boolean(conversationId);
    const home = useConversationDirectory(podId, askable ? conversationId! : null);

    const lost = (
        <p className="toolcard__fine">
            Couldn’t locate this file because the conversation’s working folder is unavailable.
        </p>
    );

    if (!live()) {
        return <p className="toolcard__fine">The sample has no computer behind it, so there is nothing to read this from.</p>;
    }
    if (!look.relative) return <WorkspaceImage path={look.path} name={look.name} />;
    if (!askable) return lost;
    if (home.isPending) return <p className="toolcard__note" role="status">Working out where that is…</p>;
    if (!home.data) return lost;
    return <WorkspaceImage path={home.data.replace(/\/+$/, "") + "/" + look.path.replace(/^\.\//, "")} name={look.name} />;
}

/** Where the file lives, in the words this app uses for the two stores. */
const STORE_WORD: Record<ImageLook["store"], string> = {
    pod: "in teammate files",
    workspace: "on the computer",
    "": "",
};

/** `view_image`, with the image.
 *
 *  Handled here rather than by the generic path, which draws a grey step
 *  reading `instructions, workspace_file_path` — the *names of its arguments*.
 *  `ViewImageRequest` is the one tool here without the shared `comment` field,
 *  so `commentOf` has nothing and `argSummary` falls through to the key list.
 *  Both of those arguments are worth reading: one is a question, and the other
 *  is a file this app can fetch.
 *
 *  Closed by default and the picture behind a second click, which is not
 *  timidity: a run that looks at a chart, a crop of it and then the fixed
 *  version is three of these in a row, and three pictures opening themselves
 *  inside the fold is the wall of output the fold exists to prevent. */
function ImageCard({
    look,
    podId,
    conversationId,
}: {
    look: ImageLook;
    podId?: string;
    conversationId?: string | null;
}) {
    const [open, setOpen] = useState(false);
    /* Once shown, shown. The state lives on the card rather than in a query,
       so closing and reopening the same one does not ask the sandbox again. */
    const [shown, setShown] = useState(false);

    const status = look.pending ? "running" : look.failed ? "failed" : "";
    /* A call that named two files or none has no single image to show, and one
       the backend refused has none to show either — the file was missing, was
       not an image, or the grant was not there. Guessing between two paths
       would put the wrong picture under the teammate's own question. */
    const fetchable = Boolean(look.store) && !look.failed && Boolean(podId);

    return (
        <section className="toolcard toolcard--look">
            <Head
                icon={<ImageIcon size={14} />}
                what={look.name}
                meta={look.weight || undefined}
                status={status}
                tone={look.failed ? "bad" : look.pending ? "wait" : undefined}
                open={open}
                onToggle={() => setOpen((was) => !was)}
            />
            {open && (
                <div className="toolcard__body">
                    {/* The agent's question first. It is the one thing on this
                        card nothing else can supply — the picture is on disk,
                        and what somebody wanted from it is not. */}
                    {look.asked && <p className="toolcard__note">Looking for: {look.asked}</p>}
                    {look.error && <p className="toolcard__note" data-tone="bad">{look.error}</p>}
                    <p className="toolcard__where">{[STORE_WORD[look.store], look.path].filter(Boolean).join(" · ")}</p>
                    {/* The picture in words, when the run's own model could not
                        see it and a vision model answered on its behalf. */}
                    {look.described && <p className="toolcard__note">{look.described}</p>}
                    {/* Above the control rather than below it. The file named
                        here already exists — that is the premise of the whole
                        card — so a look still in flight can still be shown,
                        and "nothing has come back yet" reads as a caveat
                        before the offer and as a contradiction after it. */}
                    {look.pending && !shown && <p className="toolcard__note">Nothing has come back yet.</p>}
                    {shown && podId ? (
                        look.store === "pod" ? (
                            <PodImage podId={podId} path={look.path} name={look.name} />
                        ) : (
                            <WorkspaceShot look={look} podId={podId} conversationId={conversationId} />
                        )
                    ) : (
                        fetchable && (
                            <>
                                {/* Said before the button, not under it — the
                                    lesson the sign-in card's stylesheet
                                    records. It is what pressing it will do. */}
                                {look.store === "workspace" && (
                                    <p className="toolcard__fine">Reading it starts the computer it is on.</p>
                                )}
                                <button type="button" className="toolcard__show" onClick={() => setShown(true)}>
                                    Show the image
                                </button>
                            </>
                        )
                    )}
                </div>
            )}
        </section>
    );
}

/* ── a command, and what it printed ──────────────────────────────────── */

function terminalStatus(run: TerminalRun): { text: string; tone?: "bad" | "wait" } {
    if (run.pending) return { text: "running", tone: "wait" };
    if (run.running) return { text: "still running", tone: "wait" };
    if (run.exitCode !== undefined) return { text: "exit " + run.exitCode, tone: run.exitCode === 0 ? undefined : "bad" };
    if (run.failed) return { text: "failed", tone: "bad" };
    return { text: "done" };
}

function TerminalCard({ run }: { run: TerminalRun }) {
    const [open, setOpen] = useState(false);
    const status = terminalStatus(run);
    const nothing = !run.output && !run.errorOutput && !run.value;

    return (
        <section className="toolcard toolcard--term">
            <Head
                icon={run.language === "python" ? <CodeIcon size={14} /> : <TerminalIcon size={14} />}
                what={<code className="toolcard__cmd">{run.command}</code>}
                meta={run.lines ? run.lines + (run.lines === 1 ? " line" : " lines") : undefined}
                status={status.text}
                tone={status.tone}
                open={open}
                onToggle={() => setOpen((was) => !was)}
            />
            {open && (
                <div className="toolcard__body">
                    {run.comment && <p className="toolcard__note">{run.comment}</p>}
                    {run.workdir && <p className="toolcard__where">in {run.workdir}</p>}
                    {run.output && <pre className="toolcard__out">{run.output}</pre>}
                    {run.errorOutput && <pre className="toolcard__out" data-stream="err">{run.errorOutput}</pre>}
                    {/* The last expression's value, which `execute_python`
                        returns beside the streams rather than inside them. */}
                    {run.value && (
                        <pre className="toolcard__out" data-stream="value">{run.value}</pre>
                    )}
                    {nothing && !run.pending && <p className="toolcard__note">It printed nothing.</p>}
                    {run.pending && <p className="toolcard__note">Nothing has come back yet.</p>}
                    {/* Not a failure, and the word for it matters: the wait
                        window ended, the command did not. */}
                    {run.running && (
                        <p className="toolcard__note">
                            Still running{run.processId ? " as " + run.processId : ""}. Nothing was cancelled.
                        </p>
                    )}
                </div>
            )}
        </section>
    );
}

/* ── where an answer came from ───────────────────────────────────────── */

/** How many sources show before the card asks. Four is about the point where
 *  a citation list stops being a reference and starts being the page. */
const SOURCES_SHOWN = 4;

function SourcesCard({ list }: { list: SourceList }) {
    const [all, setAll] = useState(false);
    const shown = all ? list.sources : list.sources.slice(0, SOURCES_SHOWN);
    const rest = list.sources.length - shown.length;
    const count = list.sources.length;

    const what =
        list.action === "search"
            ? "Searched the web for “" + list.query + "”"
            : count === 1
              ? "Read one page"
              : "Read " + count + " pages";
    const status = list.error
        ? "failed"
        : list.pending
          ? "running"
          : list.action === "search" && list.listed
            ? count + (count === 1 ? " result" : " results")
            : "";

    return (
        <section className="toolcard toolcard--src">
            <Head
                icon={<GlobeIcon size={14} />}
                what={what}
                status={status}
                tone={list.error ? "bad" : list.pending ? "wait" : undefined}
            />
            {/* A search still running has a head and nothing under it; an
                empty box while it waits reads as a result of none. */}
            {(count > 0 || (!list.pending && (list.listed || list.text)) || list.error || list.note || list.asked) && (
                <div className="toolcard__body">
                    {list.error && <p className="toolcard__note" data-tone="bad">{list.error}</p>}
                    {list.note && <p className="toolcard__note">{list.note}</p>}
                    {list.asked && <p className="toolcard__note">Looking for: {list.asked}</p>}
                    {!list.error && list.listed && count === 0 && !list.pending && !list.text && (
                        <p className="toolcard__note">Nothing came back.</p>
                    )}
                    <ol className="sources">
                        {shown.map((source) => (
                            <li key={source.url} className="sources__row" data-failed={source.failed ? "" : undefined}>
                                {/* The site, as a shape. Eight results is
                                    eight domains, and a column of identical
                                    grey lines is where the reader stops
                                    telling them apart. */}
                                <Site host={source.host} size={16} />
                                <div className="sources__of">
                                    <a className="sources__link" href={source.url} target="_blank" rel="noreferrer noopener">
                                        {source.title}
                                        <ExternalIcon size={11} />
                                    </a>
                                    <span className="sources__from">
                                        {[source.host, source.publisher !== source.host ? source.publisher : "", source.published]
                                            .filter(Boolean)
                                            .join(" · ")}
                                    </span>
                                    {source.snippet && <p className="sources__snip">{source.snippet}</p>}
                                    {/* Where the page landed. A fetch does not
                                        return the text, so the file is the only
                                        way the agent — or the reader — gets at
                                        what it read. */}
                                    {source.savedAs && <span className="sources__saved">{source.savedAs}</span>}
                                    {source.error && <span className="sources__err">{source.error}</span>}
                                </div>
                            </li>
                        ))}
                    </ol>
                    {rest > 0 && (
                        <button type="button" className="toolcard__more" onClick={() => setAll(true)}>
                            {rest} more
                        </button>
                    )}
                    {/* A local agent's search or fetch answers in prose, and
                        the prose is the result. */}
                    {list.text && <pre className="toolcard__out">{list.text}</pre>}
                </div>
            )}
        </section>
    );
}

/* ── a local agent's files ───────────────────────────────────────────── */

function outcome(card: { pending: boolean; failed: boolean }): { text: string; tone?: "bad" | "wait" } {
    if (card.pending) return { text: "running", tone: "wait" };
    if (card.failed) return { text: "failed", tone: "bad" };
    return { text: "" };
}

function ReadCard({ read }: { read: FileRead }) {
    const [open, setOpen] = useState(false);
    const status = outcome(read);
    return (
        <section className="toolcard toolcard--file">
            <Head
                icon={<FileIcon size={14} />}
                what={<>Read <code className="toolcard__cmd">{read.name}</code></>}
                meta={[read.range, read.lines ? read.lines + (read.lines === 1 ? " line" : " lines") : ""].filter(Boolean).join(" · ") || undefined}
                status={status.text}
                tone={status.tone}
                open={open}
                onToggle={() => setOpen((was) => !was)}
            />
            {open && (
                <div className="toolcard__body">
                    <p className="toolcard__where">{read.path}</p>
                    {read.error && <p className="toolcard__note" data-tone="bad">{read.error}</p>}
                    {read.content && <pre className="toolcard__out">{read.content}</pre>}
                    {!read.content && !read.pending && !read.failed && <p className="toolcard__note">It was empty.</p>}
                    {read.pending && <p className="toolcard__note">Nothing has come back yet.</p>}
                </div>
            )}
        </section>
    );
}

const CHANGE_VERB: Record<FileChange["action"], string> = {
    write: "Wrote",
    edit: "Edited",
    delete: "Deleted",
    move: "Moved",
};

const CHANGE_GLYPH: Record<FileChange["action"], ReactNode> = {
    write: <EditIcon size={14} />,
    edit: <EditIcon size={14} />,
    delete: <DeleteIcon size={14} />,
    move: <MoveIcon size={14} />,
};

/** "+12 −3", the size of a change in the unit a reviewer counts in. */
function tally(added: number, removed: number): string {
    return [added ? "+" + added : "", removed ? "−" + removed : ""].filter(Boolean).join(" ");
}

function ChangeCard({ change }: { change: FileChange }) {
    const [open, setOpen] = useState(false);
    const status = outcome(change);
    /* A delete or a move has nothing to open onto unless it failed. */
    const more = change.files.length > 0 || Boolean(change.error || change.message || change.destination);
    return (
        <section className="toolcard toolcard--file">
            <Head
                icon={CHANGE_GLYPH[change.action]}
                what={
                    <>
                        {CHANGE_VERB[change.action]} <code className="toolcard__cmd">{change.name}</code>
                        {change.destination && <> to <code className="toolcard__cmd">{change.destination}</code></>}
                    </>
                }
                meta={tally(change.added, change.removed) || undefined}
                status={status.text}
                tone={status.tone}
                open={more ? open : undefined}
                onToggle={more ? () => setOpen((was) => !was) : undefined}
            />
            {more && open && (
                <div className="toolcard__body">
                    {change.error && <p className="toolcard__note" data-tone="bad">{change.error}</p>}
                    {change.destination && <p className="toolcard__where">{change.path} → {change.destination}</p>}
                    {change.files.map((file) => (
                        <div key={file.path} className="toolcard__file">
                            <p className="toolcard__where">
                                {file.path}
                                {file.change === "add" ? " · new" : file.change === "delete" ? " · removed" : ""}
                            </p>
                            {file.lines.length > 0 && (
                                <pre className="toolcard__out toolcard__diff">
                                    {file.lines.map((line, index) =>
                                        line.sign === "gap" ? (
                                            <span key={index} className="toolcard__diffline" data-sign="gap">⋯</span>
                                        ) : (
                                            <span key={index} className="toolcard__diffline" data-sign={line.sign}>
                                                {line.sign + " " + line.text}
                                            </span>
                                        ),
                                    )}
                                </pre>
                            )}
                        </div>
                    ))}
                    {change.message && !change.error && <p className="toolcard__fine">{change.message}</p>}
                    {change.pending && <p className="toolcard__note">Nothing has come back yet.</p>}
                </div>
            )}
        </section>
    );
}

function searchLine(search: FileSearch): ReactNode {
    if (search.action === "list") return search.path ? <>Listed <code className="toolcard__cmd">{search.path}</code></> : search.title || "Listed files";
    if (!search.pattern) return search.title || (search.action === "grep" ? "Searched the files" : "Matched files");
    return (
        <>
            {search.action === "grep" ? "Searched for " : "Matched "}
            <code className="toolcard__cmd">{search.pattern}</code>
        </>
    );
}

function SearchCard({ search }: { search: FileSearch }) {
    const [open, setOpen] = useState(false);
    const status = outcome(search);
    const unit = search.action === "grep" ? " line" : " file";
    return (
        <section className="toolcard toolcard--file">
            <Head
                icon={search.action === "list" ? <FolderIcon size={14} /> : <SearchIcon size={14} />}
                what={searchLine(search)}
                meta={search.pending || search.failed ? undefined : search.count + unit + (search.count === 1 ? "" : "s")}
                status={status.text}
                tone={status.tone}
                open={open}
                onToggle={() => setOpen((was) => !was)}
            />
            {open && (
                <div className="toolcard__body">
                    {(search.path || search.filter) && search.action !== "list" && (
                        <p className="toolcard__where">
                            {["in " + (search.path || "the working folder"), search.filter ? "files matching " + search.filter : ""].filter(Boolean).join(", ")}
                        </p>
                    )}
                    {search.error && <p className="toolcard__note" data-tone="bad">{search.error}</p>}
                    {search.output && <pre className="toolcard__out">{search.output}</pre>}
                    {!search.output && !search.pending && !search.failed && <p className="toolcard__note">Nothing matched.</p>}
                    {search.pending && <p className="toolcard__note">Nothing has come back yet.</p>}
                </div>
            )}
        </section>
    );
}

/* ── a sub-agent ─────────────────────────────────────────────────────── */

/** The steps it took are the ones indented under this card in the fold;
 *  this card says what it was sent to do and what it brought back. */
function TaskCard({ task }: { task: SubTask }) {
    const [open, setOpen] = useState(false);
    const status = task.pending ? { text: "working", tone: "wait" as const } : outcome(task);
    return (
        <section className="toolcard toolcard--task">
            <Head
                icon={<AgentIcon size={14} />}
                what={task.description}
                meta={task.agentType || undefined}
                status={status.text}
                tone={status.tone}
                open={open}
                onToggle={() => setOpen((was) => !was)}
            />
            {open && (
                <div className="toolcard__body">
                    {task.error && <p className="toolcard__note" data-tone="bad">{task.error}</p>}
                    {task.prompt && task.prompt !== task.description && <p className="toolcard__note">{task.prompt}</p>}
                    {task.output && <pre className="toolcard__out" data-stream="value">{task.output}</pre>}
                    {task.pending && <p className="toolcard__note">Still working.</p>}
                </div>
            )}
        </section>
    );
}

/* ── one call out to a connected account ─────────────────────────────── */

function ConnectorCard({ run }: { run: ConnectorRun }) {
    const [open, setOpen] = useState(false);
    const status = run.pending ? "running" : run.failed ? "failed" : "done";
    const tone = run.failed ? "bad" : run.pending ? "wait" : undefined;

    return (
        <section className="toolcard toolcard--conn">
            <Head
                icon={<ConnectorIcon size={14} />}
                what={run.connector}
                meta={run.operation}
                status={status}
                tone={tone}
                open={open}
                onToggle={() => setOpen((was) => !was)}
            />
            {open && (
                <div className="toolcard__body">
                    {/* Said again, because the head drops it on a narrow
                        screen and the operation is the thing that was run. */}
                    <p className="toolcard__where">{run.operation}</p>
                    {run.account && <p className="toolcard__where">as account {run.account}</p>}
                    {run.error && <p className="toolcard__note" data-tone={run.failed ? "bad" : undefined}>{run.error}</p>}
                    {run.params.length > 0 && (
                        <dl className="toolcard__args">
                            {run.params.map((param) => (
                                <div key={param.name}>
                                    <dt>{param.name}</dt>
                                    <dd>{param.value}</dd>
                                </div>
                            ))}
                        </dl>
                    )}
                    {/* The provider's own payload, which is arbitrary JSON and
                        is described rather than printed. */}
                    {run.summary && <p className="toolcard__note">Came back with {run.summary}.</p>}
                    {run.savedTo && <p className="toolcard__where">saved to {run.savedTo}</p>}
                </div>
            )}
        </section>
    );
}

/* ── a run that is deliberately asleep ───────────────────────────────── */

const WOKE: Record<string, string> = {
    TIMER: "the time elapsed",
    ANSWERED: "everyone replied",
    CANCELLED: "the wait was cancelled",
};

function SnoozeCard({ wait }: { wait: SnoozeWait }) {
    /* A sleeping run and a finished one otherwise look the same, which is the
       whole reason this card exists. The status says which, and when it is
       due back — derived from the call's own clock, because nothing on the
       wire carries a wake time. */
    const back =
        wait.wakeAtMs === undefined
            ? "back in " + restLength(wait.seconds)
            : "back at " + clockOf(new Date(wait.wakeAtMs).toISOString());
    const status = wait.sleeping ? back : WOKE[wait.wokeBecause] ?? "awake";

    return (
        <section className="toolcard toolcard--rest">
            <Head
                icon={<ClockIcon size={14} />}
                what={wait.reason}
                meta={wait.sleeping ? "waiting " + restLength(wait.seconds) : undefined}
                status={status}
                tone={wait.sleeping ? "wait" : undefined}
            />
            {wait.note && (
                <div className="toolcard__body">
                    <p className="toolcard__note">Next: {wait.note}</p>
                </div>
            )}
        </section>
    );
}

/* ── a login wall, which only a person can get past ──────────────────── */

function signInStatus(ask: SignInAsk): { text: string; tone?: "bad" | "wait" | "ok" } {
    if (!ask.resolved) return { text: "needs you", tone: "wait" };
    if (ask.signedIn) return { text: ask.kept ? "kept for next time" : "signed in", tone: "ok" };
    if (ask.outcome === "declined") return { text: "skipped" };
    if (ask.outcome === "expired") return { text: "nobody answered" };
    return { text: "could not ask", tone: "bad" };
}

function SignInCard({ ask, conversationId, toolCallId }: { ask: SignInAsk; conversationId?: string | null; toolCallId?: string }) {
    const status = signInStatus(ask);
    const [signingIn, setSigningIn] = useState(false);
    /* Both ids name the pause, and a card that cannot name it cannot resolve
       it — so it says so rather than offering a control that goes nowhere. */
    const answerable = Boolean(conversationId && toolCallId);

    return (
        <section className="toolcard toolcard--signin" data-waiting={ask.resolved ? undefined : ""}>
            {/* The card where recognising the site matters most: this is a
                password prompt, and "is that really them" is the question a
                reader should be asking before they follow the link. */}
            <Head
                icon={<Site host={ask.host} />}
                what={
                    ask.resolved
                        ? (ask.signedIn ? "Signed in to " : "Not signed in to ") + ask.host
                        : "Sign in to " + ask.host
                }
                status={status.text}
                tone={status.tone}
            />
            {/* An answered sign-in with nothing to say gets no body at all: an
                empty bordered strip under the head reads as content that
                failed to load. */}
            {(ask.reason || !ask.resolved) && (
                <div className="toolcard__body">
                    {ask.reason && <p className="toolcard__note">{ask.reason}</p>}
                    {!ask.resolved &&
                        (answerable ? (
                            <>
                                {/* Said before the button rather than after
                                    it. Underneath it read as the button's own
                                    small print and put a bordered control
                                    between two paragraphs; here it is what it
                                    is — the thing to know before pressing. */}
                                <p className="toolcard__fine">
                                    Sign in to {ask.host} in the teammate&rsquo;s browser.
                                </p>
                                <button className="btn btn--primary toolcard__go" onClick={() => setSigningIn(true)}>
                                    Sign in to {ask.host}
                                </button>
                            </>
                        ) : (
                            <p className="toolcard__fine">This sign-in cannot be opened from here.</p>
                        ))}
                </div>
            )}
            {signingIn && conversationId && toolCallId && (
                <Modal
                    title={"Sign in to " + ask.host}
                    subtitle="In your teammate's browser, on your computer."
                    wide
                    onClose={() => setSigningIn(false)}
                >
                    {/* Closing is left to the person even once it is answered:
                        the outcome is the only thing that says whether the run
                        is carrying on, and a dialog that vanished the instant
                        it landed would take that sentence with it. */}
                    <SignInPane conversationId={conversationId} toolCallId={toolCallId} />
                </Modal>
            )}
        </section>
    );
}

export function ToolCardView({
    card,
    podId,
    conversationId,
    toolCallId,
}: {
    card: ToolCard;
    /** Only the image card needs it, and only to fetch: a pod file is read
     *  against a pod, and a workspace path is resolved against a conversation
     *  belonging to one. Optional so a card can still be drawn — without the
     *  picture — anywhere that has neither. */
    podId?: string;
    conversationId?: string | null;
    toolCallId?: string;
}) {
    switch (card.kind) {
        case "sign-in":
            return <SignInCard ask={card} conversationId={conversationId} toolCallId={toolCallId} />;
        case "browser":
            return <BrowserCard step={card} />;
        case "terminal":
            return <TerminalCard run={card} />;
        case "sources":
            return <SourcesCard list={card} />;
        case "connector":
            return <ConnectorCard run={card} />;
        case "snooze":
            return <SnoozeCard wait={card} />;
        case "image":
            return <ImageCard look={card} podId={podId} conversationId={conversationId} />;
        case "file-read":
            return <ReadCard read={card} />;
        case "file-change":
            return <ChangeCard change={card} />;
        case "file-search":
            return <SearchCard search={card} />;
        case "task":
            return <TaskCard task={card} />;
    }
}
