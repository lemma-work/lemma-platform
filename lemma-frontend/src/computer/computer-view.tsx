"use client";

import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Prose } from "@/thread/markdown";
import {
    BackIcon, ChevronRightIcon, DownloadIcon, ExternalIcon, FileIcon, FolderIcon,
    ImageIcon, RefreshIcon, TerminalIcon,
} from "@/ui/icons";
import { live } from "@/usage/queries";
import { Logins } from "./logins-view";
import { Screen } from "./screen";
import { useBrowser, useConversationDirectory, useFileBody, useFiles, useFileStat, useOpenBrowserTab, wholeFile } from "./queries";
import {
    crumbs, describe, isNoise, machineState, ordered, parentOf, readableSize, rootsOf,
    viewerFor, type Entry,
} from "./machine";

/** What a file the machine holds looks like when you open it.
 *
 *  Its own component so the bytes are fetched when a file is chosen and
 *  dropped when it is closed — reading is the call that wakes the sandbox, and
 *  a hook that stayed mounted would keep the last file's blob alive for as
 *  long as the pane was open.
 */
function FileBody({ path }: { path: string }) {
    const name = path.split("/").pop() ?? path;
    const viewer = viewerFor(name);
    const body = useFileBody(path);
    /* The file's real size. `body` holds at most the reading ceiling, so its
       blob cannot answer this — and when it was asked, every file past the
       ceiling reported as exactly the ceiling. */
    const stat = useFileStat(path);
    const blob = body.data?.blob;
    const sizeBytes = stat.data?.size_bytes ?? body.data?.sizeBytes ?? 0;

    /* One object URL per blob, revoked when it changes. An image and a
       download want the same handle, so it is made once for both. */
    const href = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
    useEffect(() => () => { if (href) URL.revokeObjectURL(href); }, [href]);

    if (body.isPending) return <p className="computer-note" role="status">Reading it…</p>;
    if (body.isError || !body.data) {
        return (
            <p className="computer-note" role="alert">
                Couldn’t load this file. Refresh the folder and try again.{" "}
                <button className="computer-inline" onClick={() => void body.refetch()}>Try again</button>
            </p>
        );
    }

    const save = <Save path={path} name={name} sizeBytes={sizeBytes} />;

    if (viewer === "image" && href && !body.data.tooLarge) {
        return (
            <div className="computer-file">
                <img src={href} alt={name} />
                <p className="computer-note">{readableSize(sizeBytes)} · {save}</p>
            </div>
        );
    }
    if (body.data.tooLarge || body.data.text === null) {
        return (
            <p className="computer-note">
                {readableSize(sizeBytes)}, which is more than this pane should show. {save}, or
                ask the teammate who wrote it what is in it.
            </p>
        );
    }
    return (
        <div className="computer-file">
            {viewer === "markdown"
                ? <Prose text={body.data.text} />
                : <pre className="computer-text">{body.data.text}</pre>}
            <p className="computer-note">{readableSize(sizeBytes)} · {save}</p>
        </div>
    );
}

function Save({ path, name, sizeBytes }: { path: string; name: string; sizeBytes: number }) {
    const save = useMutation({
        mutationFn: () => wholeFile(path, sizeBytes),
        onSuccess: (whole) => {
            const href = URL.createObjectURL(whole);
            const link = document.createElement("a");
            link.href = href;
            link.download = name;
            link.click();
            /* Not in the same turn: a browser reads the handle when it starts
               the save, and revoking it under the click cancels the download
               in some of them. */
            setTimeout(() => URL.revokeObjectURL(href), 60_000);
        },
    });

    if (save.isError) {
        return (
            <button className="computer-inline" onClick={() => save.mutate()}>
                <DownloadIcon size={14} /> Download failed. Try again
            </button>
        );
    }
    return (
        <button className="computer-inline" disabled={save.isPending} onClick={() => save.mutate()}>
            <DownloadIcon size={14} /> {save.isPending ? "Downloading…" : "Download"}
        </button>
    );
}

/** The computer your teammates work on.
 *
 *  Not this pod's — yours. One sandbox per person, shared by every teammate in
 *  every organization you belong to, which is the fact the subtitle spends its
 *  one line on because nothing else on screen implies it.
 *
 *  It opens on the directory the conversation you were reading works in,
 *  because that is the question that brings anybody here: the teammate said it
 *  wrote a file, and this is where it went. The whole machine is one crumb
 *  away for when the question is broader than that.
 *
 *  Nothing here starts a sandbox on its own. Listing is ambient — a paused
 *  machine answers "asleep" rather than being woken — and the two acts that do
 *  cost something, opening a file and opening the browser, are both a click
 *  somebody made.
 */
export function ComputerView({ podId, conversationId, visible }: {
    podId: string;
    conversationId: string | null;
    /** Whether this pane is the one on screen.
     *
     *  The pane stays mounted behind whatever tab is in front so a walk three
     *  folders deep survives a glance at the conversation — and everything it
     *  asks for is gated on this, so a pane nobody is looking at does not go
     *  on polling a sandbox on every window focus. Staying mounted is for the
     *  reader; staying quiet is for the machine.
     */
    visible: boolean;
}) {
    const home = useConversationDirectory(podId, conversationId);
    const [at, setAt] = useState<string | null>(null);
    const [openFile, setOpenFile] = useState<string | null>(null);
    const [wake, setWake] = useState(false);
    const [noise, setNoise] = useState(false);

    /* The conversation's directory is where this opens, but only until
       somebody navigates: `at` staying null would send a reader back to the
       start of the walk every time the conversation query settled. */
    /* Null is a real value here and the one this opens on: it means "wherever
       the server keeps things", which is the only way to ask without first
       guessing a root. The answer carries both roots and echoes the directory
       it listed, so one request settles where this pane is and where it may
       go. */
    const asked = at ?? home.data ?? null;
    const listing = useFiles(asked, wake, visible);
    const first = listing.data?.pages[0];
    const roots = rootsOf(first);
    /* The server's own word for where we are, because it resolved the path it
       was given — or was given none at all. */
    const path = first?.path ?? asked ?? roots.workspace;
    /* Not asked until the listing has said the machine is up. Both answers are
       ambient and neither provisions anything, but asleep is the ordinary
       resting state and a second request to be told so again is one nobody
       needed. */
    const browser = useBrowser(visible && first !== undefined && !first.sleeping);
    const state = machineState(first, browser.data, listing.isPending);

    const all = listing.data?.pages.flatMap((page) => page.entries ?? []) ?? [];
    const shown = ordered(noise ? all : all.filter((entry) => !isNoise(entry)));
    const hiddenCount = all.length - shown.length;
    const up = parentOf(path, roots);

    const tab = useOpenBrowserTab();

    const goTo = (next: string) => { setAt(next); setOpenFile(null); };

    /* Sample mode has no account behind it, so there is no sandbox to ask
       about — and the queries are disabled, which without this would leave the
       pane saying "Looking…" at nothing forever. */
    if (!live()) {
        return (
            <section className="library-view computer-view" aria-label="Computer">
                <header className="library-heading"><div><h1>Your computer</h1></div></header>
                <div className="computer-empty">
                    <TerminalIcon size={26} />
                    <p>The sample has no machine behind it. Sign in to see the one your teammates work on.</p>
                </div>
            </section>
        );
    }

    return (
        <section className="library-view computer-view" aria-label="Computer">
            {/* The screen first, because that is what a computer is from the
                outside, and the state is said once — on it — rather than
                repeated in a badge beside it. */}
            <header className="computer-head">
                <Screen
                    state={state}
                    browser={browser.data}
                    conversationId={conversationId}
                    visible={visible}
                    busy={tab.busy}
                    onWake={() => setWake(true)}
                    onOpenTab={tab.open}
                />
                <div className="computer-intro">
                    <h1>Your computer</h1>
                    <p>Your AI teammates sign in to this one machine, each conversation in a profile of its own.</p>
                </div>
            </header>

            {/* Between the machine and its files, because that is what it is
                about: the browser on the screen above is the one holding these,
                and they outlive a suspend along with its profile. */}
            <Logins visible={visible} />

            <nav className="computer-path" aria-label="Where you are">
                {/* Back means the last step taken, not one level of path. With
                    a file open that step was opening it, and an arrow that
                    skipped past the folder it came from would be the one
                    control on the page that does not go where it points. */}
                {(openFile || up !== null) && (
                    <button
                        className="computer-up"
                        aria-label={openFile ? "Back to the folder" : "Up one folder"}
                        title={openFile ? "Back to the folder" : "Up one folder"}
                        onClick={() => { if (openFile) setOpenFile(null); else if (up !== null) goTo(up); }}
                    >
                        <BackIcon size={15} />
                    </button>
                )}
                {crumbs(path, roots).map((step, index) => (
                    <span key={step.path}>
                        {index > 0 && <ChevronRightIcon size={12} aria-hidden="true" />}
                        <button disabled={step.path === path && !openFile} onClick={() => goTo(step.path)}>{step.name}</button>
                    </span>
                ))}
                {openFile && (
                    <span>
                        <ChevronRightIcon size={12} aria-hidden="true" />
                        <button disabled>{openFile.split("/").pop()}</button>
                    </span>
                )}
                <button
                    className="computer-again"
                    title="Refresh"
                    aria-label="Refresh"
                    disabled={listing.isFetching}
                    onClick={() => void listing.refetch()}
                >
                    <RefreshIcon size={15} />
                </button>
            </nav>
            {!openFile && describe(path, roots) && <p className="computer-where">{describe(path, roots)}</p>}

            {tab.failed && (
                <p className="computer-note" role="alert">
                    Couldn’t open the browser. This computer may not support a browser view.
                </p>
            )}

            {openFile ? <FileBody path={openFile} /> : (
                <>
                    {listing.isPending && <p className="computer-note" role="status">Looking…</p>}
                    {listing.isError && (
                        <p className="computer-note" role="alert">
                            This computer could not be read.{" "}
                            <button className="computer-inline" onClick={() => void listing.refetch()}>Try again</button>
                        </p>
                    )}

                    {/* Asleep is the ordinary resting state, not a fault: the
                        sandbox is released after a quarter of an hour idle, and
                        a pane that woke one to fill itself in would hold
                        compute for as long as somebody left this tab open. So
                        it says what it is and waits to be asked. */}
                    {first?.sleeping && (
                        <div className="computer-empty">
                            <TerminalIcon size={26} />
                            <p>
                                This computer is asleep. It starts when a teammate needs it and stops again
                                once it has been idle a while — wake it to continue.
                            </p>
                            <button className="btn btn--primary" onClick={() => setWake(true)}>Wake it</button>
                        </div>
                    )}

                    {first && !first.sleeping && first.exists === false && (
                        <div className="computer-empty">
                            <FolderIcon size={26} />
                            <p>
                                Nothing has been written here. A conversation&apos;s folder is made the first
                                time its teammate saves something into it.
                            </p>
                        </div>
                    )}

                    {first && !first.sleeping && first.exists !== false && shown.length === 0 && (
                        <div className="computer-empty">
                            <FolderIcon size={26} />
                            <p>{hiddenCount > 0 ? "Only build output and dotfiles here." : "This folder is empty."}</p>
                        </div>
                    )}

                    {shown.length > 0 && (
                        <div className="library-list">
                            {shown.map((entry) => (
                                <Row key={entry.path} entry={entry} onOpen={() => {
                                    if (entry.kind === "directory") goTo(entry.path);
                                    else setOpenFile(entry.path);
                                }} />
                            ))}
                        </div>
                    )}

                    {(shown.length > 0 || hiddenCount > 0) && (
                        <footer className="library-footer">
                            <span>
                                {shown.length} {shown.length === 1 ? "item" : "items"}
                                {hiddenCount > 0 && ` · ${hiddenCount} hidden`}
                            </span>
                            <span className="computer-footer-actions">
                                {hiddenCount > 0 && !noise && <button onClick={() => setNoise(true)}>Show them</button>}
                                {noise && <button onClick={() => setNoise(false)}>Hide build output</button>}
                                {listing.hasNextPage && (
                                    <button disabled={listing.isFetchingNextPage} onClick={() => void listing.fetchNextPage()}>
                                        Load more
                                    </button>
                                )}
                            </span>
                        </footer>
                    )}
                </>
            )}
        </section>
    );
}

function Row({ entry, onOpen }: { entry: Entry; onOpen: () => void }) {
    const folder = entry.kind === "directory";
    const viewer = viewerFor(entry.name);
    const when = new Date(entry.modified_at);
    return (
        <div className="library-row">
            <button className="library-item" onClick={onOpen}>
                <span className="library-item-icon">
                    {folder ? <FolderIcon size={21} /> : viewer === "image" ? <ImageIcon size={21} /> : <FileIcon size={21} />}
                </span>
                <span className="library-item-name">
                    <strong>{entry.name}</strong>
                    <small>
                        {folder ? "Folder" : readableSize(entry.size_bytes) || "Empty file"}
                        {entry.kind === "symlink" && " · a link to somewhere else"}
                    </small>
                </span>
                <span className="library-item-date">
                    {Number.isNaN(when.getTime()) ? "" : when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                </span>
                {folder ? <ChevronRightIcon size={16} /> : <ExternalIcon size={15} />}
            </button>
        </div>
    );
}
