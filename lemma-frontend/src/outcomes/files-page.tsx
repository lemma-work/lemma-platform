"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { sinceShort } from "@/channels/model";
import { source, type Pod } from "@/data";
import { readDropped } from "@/library/drop-entries";
import { ConfirmDelete, useLibraryWrites } from "@/library/library-writes";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { CheckIcon, ChevronRightIcon, CloseIcon, DeleteIcon, EditIcon, MoreIcon, MoveIcon, PlusIcon, SearchIcon, UploadIcon } from "@/ui/icons";
import type { FrameActions } from "./actions";
import {
    crumbsOf,
    folderPath,
    inListOrder,
    isLemmasOwn,
    isWithin,
    kindLine,
    kindOf,
    readStateOf,
    ROOTS,
    type FileItem,
    type FolderCount,
    type MarkKind,
    type Whose,
} from "./file-kinds";
import { MoveDialog } from "./file-move";
import { FileSheet, KindMark } from "./file-sheet";
import { folderKey, useArrivals, useFolder, type Arrival, type Arriving, type FolderPages } from "./files-data";
import { outcomeView, type Go } from "./model";
import { PageActs } from "./page-acts";
import { to } from "./parts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";

/** A chip's count: what is listed at the top of those files, past the
 *  Lemma's own folders, with a "+" while there is more to read. Nothing
 *  while it is being read, or could not be. */
function countOf(listing: ReturnType<typeof useFolder>): string | null {
    if (!listing.isSuccess) return null;
    const shown = listing.data.pages.flatMap((page) => page.items).filter((item) => !isLemmasOwn(item.path)).length;
    return listing.hasNextPage ? shown + "+" : String(shown);
}

/** A Lemma's Files page: one calm list of everything handed to it.
 *
 *  Where you are reads as a path (Files › Proposals › 2026), each step a way
 *  back; whose files, as two chips, everyone's in the Lemma or only the
 *  reader's own (`/me`). Folders first, then files, each saying what it is
 *  in words, how big, who added it and when it changed -- never a mime
 *  type. The Lemma's own folders (memory, skills, pages) have pages of
 *  their own, so they are a line under the list rather than rows in it.
 *
 *  Files dropped anywhere on the page go into the folder you are in. The
 *  drop is listened for on the whole window, so the header and margins take
 *  files too; an overlay says where they will go only while something is
 *  being dragged. Each file is then a row at the top saying what is
 *  happening to it: uploading, being read (only while the server says it
 *  is), then read, or why not with a way to try again.
 *
 *  Clicking a file opens it in a sheet from the side; clicking a folder
 *  opens the folder. The sample is shown as it is, and says it can't be
 *  changed rather than offering buttons that can only fail. */
export function FilesPage({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const cache = useQueryClient();
    const me = useMe();
    const live = source.label === "live";
    const [whose, setWhose] = useState<Whose>("everyone");
    const [directory, setDirectory] = useState(ROOTS.everyone.path);
    const [query, setQuery] = useState("");
    const [making, setMaking] = useState<string | null>(null);
    const [renaming, setRenaming] = useState<{ path: string; draft: string } | null>(null);
    const [sheet, setSheet] = useState<FileItem | null>(null);
    const [moving, setMoving] = useState<FileItem | null>(null);
    const [deleting, setDeleting] = useState<FileItem | null>(null);
    const [over, setOver] = useState(false);
    const [said, setSaid] = useState("");
    const picker = useRef<HTMLInputElement | null>(null);

    const folder = useFolder(pod.id, directory);
    const everyone = useFolder(pod.id, ROOTS.everyone.path);
    const mine = useFolder(pod.id, ROOTS.mine.path);
    const writes = useLibraryWrites(pod.id, directory);
    const arrivals = useArrivals(pod.id);
    const steps = crumbsOf(directory, whose);
    const here = steps[steps.length - 1];
    const atTop = directory === ROOTS[whose].path;

    /* The drop is heard on the window, set up once; what it needs to know
       about the page now is read through this. */
    const latest = useRef({ directory, live, take: arrivals.take });
    useEffect(() => {
        latest.current = { directory, live, take: arrivals.take };
    });

    useEffect(() => {
        let depth = 0;
        const carries = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");
        const enter = (event: DragEvent) => {
            if (!carries(event)) return;
            depth += 1;
            setOver(true);
        };
        const hover = (event: DragEvent) => {
            if (!carries(event) || event.defaultPrevented) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        };
        const leave = (event: DragEvent) => {
            if (!carries(event)) return;
            depth = Math.max(0, depth - 1);
            if (depth === 0) setOver(false);
        };
        const drop = (event: DragEvent) => {
            depth = 0;
            setOver(false);
            if (!carries(event) || event.defaultPrevented) return;
            event.preventDefault();
            const { directory: into, live: open, take } = latest.current;
            if (!open) {
                setSaid("This is a sample, so nothing can be added here.");
                return;
            }
            /* Read out of the event before anything is awaited: a drop's data
               is there only while its handler runs. */
            const entries = Array.from(event.dataTransfer?.items ?? [])
                .map((item) => item.webkitGetAsEntry?.() ?? null)
                .filter((entry): entry is FileSystemEntry => entry !== null);
            const files = Array.from(event.dataTransfer?.files ?? []);
            void (async () => {
                let list: { file: File; directory: string }[];
                try {
                    list = entries.length > 0 ? (await readDropped(entries, into)).files : files.map((file) => ({ file, directory: into }));
                } catch {
                    list = files.map((file) => ({ file, directory: into }));
                }
                setSaid(list.length === 0 ? "There were no files in that drop." : "");
                await take(list);
            })();
        };
        window.addEventListener("dragenter", enter);
        window.addEventListener("dragover", hover);
        window.addEventListener("dragleave", leave);
        window.addEventListener("drop", drop);
        return () => {
            window.removeEventListener("dragenter", enter);
            window.removeEventListener("dragover", hover);
            window.removeEventListener("dragleave", leave);
            window.removeEventListener("drop", drop);
        };
    }, []);

    const listed = useMemo(() => folder.data?.pages.flatMap((page) => page.items) ?? [], [folder.data]);
    const own = listed.filter((item) => !isLemmasOwn(item.path));
    const asked = query.trim().toLowerCase();
    const matching = inListOrder(own.filter((item) => !asked || item.name.toLowerCase().includes(asked)));
    /* A file still on its way is said in its own row; the list's row for it
       waits until that row has said how it went, then takes over, fresh. */
    const arriving = arrivals.rows.filter((row) => !row.settled && isWithin(row.folder, directory, whose));
    const inFlight = new Set(arriving.map((row) => row.path).filter((path): path is string => Boolean(path)));
    const fresh = new Set(arrivals.rows.filter((row) => row.settled).map((row) => row.path));
    const rows = matching.filter((item) => !inFlight.has(item.path));

    const byOf = (item: FileItem): string | null => {
        if (!item.owner) return null;
        if (me && item.owner === me) return "You";
        return pod.members.find((member) => member.userId && member.userId === item.owner)?.name ?? null;
    };
    /* "Added by" is a column only where the listing says for something here. */
    const showBy = own.some((item) => byOf(item) !== null);

    /* A folder's count is known when its own listing has been read whole --
       it was opened, or moved into -- and not otherwise. */
    const countIn = (item: FileItem): FolderCount | null => {
        const read = cache.getQueryData<FolderPages>(folderKey(pod.id, folderPath(item.path)));
        if (!read || read.pages.at(-1)?.next) return null;
        const inside = read.pages.flatMap((page) => page.items).filter((one) => !isLemmasOwn(one.path));
        return { files: inside.filter((one) => one.kind !== "folder").length, folders: inside.filter((one) => one.kind === "folder").length };
    };

    function into(path: string) {
        setDirectory(folderPath(path));
        setQuery("");
        setMaking(null);
        setRenaming(null);
        setSaid("");
        writes.clearProblem();
    }

    function choose(next: Whose) {
        setWhose(next);
        into(ROOTS[next].path);
    }

    async function makeFolder(event: FormEvent) {
        event.preventDefault();
        if (making === null) return;
        if (await writes.createFolder(making)) setMaking(null);
    }

    async function rename(event: FormEvent, item: FileItem) {
        event.preventDefault();
        if (!renaming) return;
        if (await writes.rename(item, renaming.draft)) setRenaming(null);
    }

    const startRename = (item: FileItem) => {
        writes.clearProblem();
        setMaking(null);
        setRenaming({ path: item.path, draft: item.name });
    };
    const opened = sheet ? listed.find((item) => item.path === sheet.path) ?? sheet : null;

    /* What this folder is called in a sentence: the Lemma's files at the
       top, the reader's own, or the folder by its name. */
    const these = !atTop ? here.label : whose === "mine" ? "your files" : pod.name + "’s files";
    let state: ReactNode = null;
    if (folder.isPending) {
        state = <SkeletonRows label={"Reading " + these} rows={6} slow={"Still reading " + these + ". It’s slower than usual."} />;
    } else if (folder.isError && isForbidden(folder.error)) {
        state = (
            <Calm icon="lock" title="You can’t see these files">
                They’re kept for the people {pod.name} shares them with. An admin of {pod.name} can add you.
            </Calm>
        );
    } else if (folder.isError && !isMissing(folder.error)) {
        state = (
            <Calm
                tone="warn"
                title={"Couldn’t read " + these}
                note={codeOf(folder.error)}
                actions={<TryAgain busy={folder.isFetching} onClick={() => void folder.refetch()} />}
            >
                Nothing in them changed. You can still drop files here to add them.
            </Calm>
        );
    } else if (rows.length === 0 && arriving.length === 0 && making === null) {
        state = (
            <p className="ox-files-empty">
                {asked
                    ? (folder.hasNextPage ? "Nothing shown so far matches “" + query.trim() + "”. Show more to look further." : "Nothing in " + here.label + " matches “" + query.trim() + "”.")
                    : !live ? "No files here."
                        : !atTop ? "Nothing in " + here.label + " yet. Drop files here to add them."
                            : whose === "mine" ? "Nothing of yours here yet. Drop files here to keep them for yourself."
                                : "No files yet. Drop some anywhere on this page and I’ll read what I can."}
            </p>
        );
    }

    return (
        <div className="ox-files">
            <input
                ref={picker}
                type="file"
                multiple
                hidden
                onChange={(event) => {
                    const chosen = Array.from(event.target.files ?? []);
                    event.target.value = "";
                    setSaid("");
                    void arrivals.take(chosen.map((file) => ({ file, directory })));
                }}
            />
            {/* In the page's header, beside its title, as the mock has them. */}
            <PageActs className="ox-files-acts">
                <button type="button" className="b b--m b--secondary" disabled={!live} onClick={() => { setRenaming(null); writes.clearProblem(); setMaking(""); }}>
                    <PlusIcon size={16} />
                    New folder
                </button>
                <button type="button" className="b b--m b--primary" disabled={!live} onClick={() => picker.current?.click()}>
                    <UploadIcon size={16} />
                    Upload
                </button>
            </PageActs>
            {!live && <p className="ox-files-quiet">This is a sample, so files can’t be added or changed here.</p>}

            <div className="ph__tools ox-files-tools">
                <button type="button" className="kchip" aria-pressed={whose === "everyone"} onClick={() => choose("everyone")}>
                    Everyone’s{countOf(everyone) !== null && <span>{countOf(everyone)}</span>}
                </button>
                <button type="button" className="kchip" aria-pressed={whose === "mine"} onClick={() => choose("mine")}>
                    Only mine{countOf(mine) !== null && <span>{countOf(mine)}</span>}
                </button>
                <span className="ph__grow" />
                <label className="ksearch ox-files-search">
                    <SearchIcon size={15} aria-hidden="true" />
                    <input type="search" placeholder="Find in this folder" aria-label={"Find in " + here.label} value={query} onChange={(event) => setQuery(event.target.value)} />
                </label>
            </div>

            <nav className="ox-files-crumbs" aria-label="Where you are">
                {steps.map((step, index) => (
                    <span key={step.path} className="ox-files-crumbs__step">
                        {index > 0 && <ChevronRightIcon size={13} aria-hidden="true" />}
                        <button type="button" aria-current={index === steps.length - 1 ? "location" : undefined} onClick={() => into(step.path)}>{step.label}</button>
                    </span>
                ))}
            </nav>

            {writes.problem && (
                <div className="ox-files-bad">
                    <Problem>
                        {writes.problem}{" "}
                        <button type="button" className="ox-files-act" onClick={writes.clearProblem}>Dismiss</button>
                    </Problem>
                </div>
            )}
            {said && <p className="ox-files-quiet" role="status">{said}</p>}

            <div className="ox-files-list" data-by={showBy || undefined}>
                {(rows.length > 0 || arriving.length > 0 || making !== null) && (
                    <div className="ox-files-head" aria-hidden="true">
                        <span>Name</span>
                        <span>Kind</span>
                        {showBy && <span>Added by</span>}
                        <span>Changed</span>
                        <span />
                    </div>
                )}
                <ul className="ox-files-rows" aria-label={"In " + here.label}>
                    {making !== null && (
                        <EditRow
                            mark="folder"
                            label="New folder’s name"
                            placeholder="Name the folder"
                            value={making}
                            busy={Boolean(writes.busy)}
                            meta={<Meta kind="Folder" by={showBy ? "You" : null} when="Now" />}
                            onChange={setMaking}
                            onSubmit={(event) => void makeFolder(event)}
                            onCancel={() => { setMaking(null); writes.clearProblem(); }}
                        />
                    )}
                    {arriving.map((row) => (
                        <ArrivalRow
                            key={row.id}
                            row={row}
                            directory={directory}
                            showBy={showBy}
                            onRetry={() => arrivals.retry(row)}
                            onDismiss={() => arrivals.dismiss(row.id)}
                        />
                    ))}
                    {rows.map((item) => {
                        const line = kindLine(item, item.kind === "folder" ? countIn(item) : null);
                        const meta = <Meta kind={line} by={showBy ? byOf(item) ?? "" : null} when={sinceShort(item.updated)} at={item.updated} />;
                        return renaming?.path === item.path ? (
                            <EditRow
                                key={item.path}
                                mark={kindOf(item).mark}
                                label={"New name for " + item.name}
                                value={renaming.draft}
                                busy={writes.busy === item.path}
                                selectStem={item.kind !== "folder"}
                                meta={meta}
                                onChange={(draft) => setRenaming({ path: item.path, draft })}
                                onSubmit={(event) => void rename(event, item)}
                                onCancel={() => { setRenaming(null); writes.clearProblem(); }}
                            />
                        ) : (
                            <FileRow
                                key={item.path}
                                item={item}
                                meta={meta}
                                fresh={fresh.has(item.path)}
                                live={live}
                                busy={writes.busy === item.path}
                                onOpen={() => (item.kind === "folder" ? into(item.path) : setSheet(item))}
                                onRetry={() => void writes.retry(item)}
                                onRename={() => startRename(item)}
                                onMove={() => setMoving(item)}
                                onDelete={() => setDeleting(item)}
                            />
                        );
                    })}
                </ul>
                {state}
                {folder.hasNextPage && (
                    <div className="ox-files-more-rows">
                        <button type="button" className="b b--s b--secondary" disabled={folder.isFetchingNextPage} onClick={() => void folder.fetchNextPage()}>
                            {folder.isFetchingNextPage ? "Reading more…" : "Show more"}
                        </button>
                    </div>
                )}
            </div>

            {atTop && (
                <p className="ox-files-also">
                    Also kept by {pod.name}, each on its own page:{" "}
                    <a {...to(outcomeView(pod.id, "memory"), go)}>Memory</a>
                    {" · "}
                    <a {...to(outcomeView(pod.id, "skills"), go)}>Skills</a>
                    {" · "}
                    <a {...to(outcomeView(pod.id, "pages"), go)}>Pages</a>
                </p>
            )}

            {over && (
                <div className="ox-files-drop" aria-hidden="true">
                    <UploadIcon size={26} />
                    <span className="ox-files-drop__t">{live ? "Drop to add to " + here.label : "This is a sample"}</span>
                    <span>{live ? "I’ll read what I can as each one lands." : "Nothing can be added here."}</span>
                </div>
            )}

            {opened && (
                <FileSheet
                    pod={pod}
                    item={opened}
                    where={[pod.name, ...steps.map((step) => step.label)].join(" › ")}
                    by={byOf(opened)}
                    live={live}
                    busy={writes.busy === opened.path}
                    onOpen={() => go({ name: "file", podId: pod.id, path: opened.path })}
                    onAsk={() => actions.prompt(pod.id, "Look at " + opened.path + " and ")}
                    onRetry={() => void writes.retry(opened)}
                    onRename={() => { setSheet(null); startRename(opened); }}
                    onMove={() => setMoving(opened)}
                    onDelete={() => setDeleting(opened)}
                    onClose={() => setSheet(null)}
                />
            )}
            {moving && (
                <MoveDialog
                    podId={pod.id}
                    item={moving}
                    whose={whose}
                    onClose={() => setMoving(null)}
                    onMoved={() => { setMoving(null); setSheet(null); }}
                />
            )}
            {deleting && (
                <ConfirmDelete
                    item={deleting}
                    onCancel={() => setDeleting(null)}
                    onConfirm={() => {
                        const gone = deleting;
                        setDeleting(null);
                        setSheet(null);
                        void writes.remove(gone);
                    }}
                />
            )}
        </div>
    );
}

/** The columns after a row's name: the kind in words with its size, who
 *  added it (a column only where the listing says for something here), and
 *  when it changed. On a phone they are one line under the name. */
function Meta({ kind, by, when, at }: { kind: ReactNode; by: string | null; when: string; at?: string }) {
    return (
        <span className="ox-files-meta">
            <span className="ox-files-kind">{kind}</span>
            {by !== null && <span className="ox-files-by">{by}</span>}
            <span className="ox-files-when">{at ? <time dateTime={at}>{when}</time> : when}</span>
        </span>
    );
}

/** One file or folder in the list. The name is the button that opens it,
 *  stretched over the whole row, so the row is the target and still holds
 *  the ⋯ beside it -- a button inside a button is invalid markup, and the
 *  browser unnesting it is how "rename" ends up opening the file. A file
 *  the datastore is reading, or could not read, says so under its name. */
function FileRow({ item, meta, fresh, live, busy, onOpen, onRetry, onRename, onMove, onDelete }: {
    item: FileItem;
    meta: ReactNode;
    fresh: boolean;
    live: boolean;
    busy: boolean;
    onOpen: () => void;
    onRetry: () => void;
    onRename: () => void;
    onMove: () => void;
    onDelete: () => void;
}) {
    const reading = item.kind === "file" ? readStateOf(item.status) : null;
    return (
        <li className="ox-files-row" data-new={fresh || undefined} aria-busy={busy || undefined}>
            <span className="ox-files-name">
                <KindMark mark={kindOf(item).mark} />
                <span className="ox-files-name__w">
                    <button type="button" className="ox-files-open" onClick={onOpen}>{item.name}</button>
                    {reading === "reading" && (
                        <span className="ox-files-name__s"><i className="ox-knows-spin" aria-hidden="true" />Reading…</span>
                    )}
                    {reading === "failed" && (
                        <span className="ox-files-name__s ox-files-name__s--bad">
                            Couldn’t read it
                            {live && <>{" · "}<button type="button" className="ox-files-act" disabled={busy} onClick={onRetry}>Try again</button></>}
                        </span>
                    )}
                </span>
            </span>
            {meta}
            <span className="ox-files-end">
                {live && <RowMenu name={item.name} onRename={onRename} onMove={onMove} onDelete={onDelete} />}
            </span>
        </li>
    );
}

/** A row being named: a new folder, or something being renamed. Enter
 *  makes it, Escape leaves it as it was. For a file, the name is selected
 *  up to its extension, since that is the part being changed. */
function EditRow({ mark, label, placeholder, value, busy, selectStem = false, meta, onChange, onSubmit, onCancel }: {
    mark: MarkKind;
    label: string;
    placeholder?: string;
    value: string;
    busy: boolean;
    selectStem?: boolean;
    meta: ReactNode;
    onChange: (value: string) => void;
    onSubmit: (event: FormEvent) => void;
    onCancel: () => void;
}) {
    return (
        <li className="ox-files-row ox-files-row--edit">
            <form className="ox-files-name" onSubmit={onSubmit}>
                <KindMark mark={mark} />
                <input
                    className="ox-files-field"
                    autoFocus
                    aria-label={label}
                    placeholder={placeholder}
                    value={value}
                    disabled={busy}
                    autoComplete="off"
                    onChange={(event) => onChange(event.target.value)}
                    onFocus={(event) => {
                        if (!selectStem) return;
                        const dot = event.target.value.lastIndexOf(".");
                        event.target.setSelectionRange(0, dot > 0 ? dot : event.target.value.length);
                    }}
                    onKeyDown={(event) => {
                        if (event.key !== "Escape") return;
                        event.preventDefault();
                        onCancel();
                    }}
                />
            </form>
            {meta}
            <span className="ox-files-end">
                <button type="button" className="b b--s b--quiet b--icon" aria-label="Cancel" disabled={busy} onClick={onCancel}>
                    <CloseIcon size={15} />
                </button>
            </span>
        </li>
    );
}

const ARRIVING_WORDS: Record<Arriving, string> = {
    queued: "Waiting",
    up: "Uploading…",
    reading: "Reading…",
    read: "Read",
    added: "Added",
    slow: "Still reading",
    upfailed: "Didn’t upload",
    readfailed: "Couldn’t read",
};

/** A file handed over on this page, at the top of the folder it went to,
 *  saying where it is: going up (a bar with no figure on it, since the
 *  upload gives none), being read, then read -- or why not, with a way to
 *  try again and a way to let it go. One dropped into a folder inside this
 *  one says which. */
function ArrivalRow({ row, directory, showBy, onRetry, onDismiss }: {
    row: Arrival;
    directory: string;
    showBy: boolean;
    onRetry: () => void;
    onDismiss: () => void;
}) {
    const failed = row.state === "upfailed" || row.state === "readfailed";
    const stuck = failed || row.state === "slow";
    const folder = folderPath(row.folder);
    const inside = folder === folderPath(directory) ? null : "In " + folder.slice(directory === "/" ? 1 : folderPath(directory).length + 1).split("/").join(" › ");
    const why = row.state === "slow" ? "This one is taking a while." : row.problem ?? null;
    const again = (failed && row.again) || row.state === "slow";
    return (
        <li className="ox-files-row ox-files-row--arriving" data-s={row.state}>
            <span className="ox-files-name">
                <KindMark mark={kindOf({ name: row.file.name, kind: "file", mime: row.file.type }).mark} />
                <span className="ox-files-name__w">
                    <span className="ox-files-name__t">{row.file.name}</span>
                    {(inside || why || again) && (
                        <span className={"ox-files-name__s" + (failed ? " ox-files-name__s--bad" : "")}>
                            {[inside, why].filter(Boolean).join(" · ")}
                            {again && (
                                <>
                                    {inside || why ? " · " : ""}
                                    <button type="button" className="ox-files-act" onClick={onRetry}>{row.state === "slow" ? "Check again" : "Try again"}</button>
                                </>
                            )}
                        </span>
                    )}
                </span>
            </span>
            <Meta
                kind={(
                    <span className="ox-files-state" data-s={row.state} role="status">
                        {row.state === "up" && <span className="kmeter ox-files-meter" aria-hidden="true"><i /></span>}
                        {row.state === "reading" && <i className="ox-knows-spin" aria-hidden="true" />}
                        {(row.state === "read" || row.state === "added") && <CheckIcon size={13} aria-hidden="true" />}
                        {ARRIVING_WORDS[row.state]}
                    </span>
                )}
                by={showBy ? "You" : null}
                when="Now"
            />
            <span className="ox-files-end">
                {stuck && (
                    <button type="button" className="b b--s b--quiet b--icon" aria-label={"Dismiss " + row.file.name} onClick={onDismiss}>
                        <CloseIcon size={15} />
                    </button>
                )}
            </span>
        </li>
    );
}

/** A row's ⋯: rename, move and delete, out of the way until wanted, so a
 *  row stays its name and what it is. Opens with the first choice focused;
 *  arrow keys move between them; Escape, a pick or a click elsewhere
 *  closes it. Raised over the rows under it while open. */
function RowMenu({ name, onRename, onMove, onDelete }: { name: string; onRename: () => void; onMove: () => void; onDelete: () => void }) {
    const [open, setOpen] = useState(false);
    const box = useRef<HTMLSpanElement>(null);
    useEffect(() => {
        if (!open) return;
        box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
        const away = (event: PointerEvent) => {
            if (!box.current?.contains(event.target as Node)) setOpen(false);
        };
        document.addEventListener("pointerdown", away);
        return () => document.removeEventListener("pointerdown", away);
    }, [open]);

    const keys = (event: ReactKeyboardEvent<HTMLSpanElement>) => {
        if (event.key === "Escape") {
            event.preventDefault();
            setOpen(false);
            box.current?.querySelector<HTMLElement>("button")?.focus();
            return;
        }
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        const items = Array.from(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
        if (items.length === 0) return;
        event.preventDefault();
        const at = items.indexOf(document.activeElement as HTMLElement);
        const next = event.key === "ArrowDown" ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
        items[next]?.focus();
    };
    const pick = (then: () => void) => () => {
        setOpen(false);
        then();
    };

    return (
        <span className="ox-files-more" data-open={open || undefined} ref={box} onKeyDown={keys}>
            <button type="button" className="b b--s b--quiet b--icon" aria-label={"More for " + name} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
                <MoreIcon size={16} />
            </button>
            {open && (
                <span className="ox-more__menu ox-files-menu" role="menu" aria-label={name}>
                    <button type="button" role="menuitem" onClick={pick(onRename)}><EditIcon size={15} />Rename</button>
                    <button type="button" role="menuitem" onClick={pick(onMove)}><MoveIcon size={15} />Move</button>
                    <button type="button" role="menuitem" className="ox-files-menu__bad" onClick={pick(onDelete)}><DeleteIcon size={15} />Delete</button>
                </span>
            )}
        </span>
    );
}
