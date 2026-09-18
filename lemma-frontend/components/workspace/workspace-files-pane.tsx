'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { FileTypeIcon } from '@/components/documents/file-type-icon';
import { getDocumentPreviewType } from '@/components/documents/preview-renderers';
import { Button } from '@/components/ui/button';
import { ChevronRight, Download, Folder, RefreshCw } from '@/components/ui/icons';
import {
    HOME_ROOT,
    useWorkspaceFile,
    useWorkspaceFiles,
} from '@/lib/hooks/use-workspace-files';
import { cn } from '@/lib/utils';

const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/** The next directory up, stopping at the ceiling rather than climbing past it. */
const parentOf = (path: string, ceiling: string): string | null => {
    if (path === ceiling) return null;
    const cut = path.lastIndexOf('/');
    return cut <= ceiling.length - 1 ? ceiling : path.slice(0, cut);
};

/** Segments between `from` and `path`, for the breadcrumb. */
const segmentsOf = (
    path: string,
    from: string,
    ceiling: string,
): { name: string; path: string }[] => {
    if (path === from) return [];
    const inside = path.startsWith(`${from}/`);
    const base = inside ? from : ceiling;
    if (path === base) return [];
    return path
        .slice(base.length + 1)
        .split('/')
        .reduce<{ name: string; path: string }[]>((acc, name) => {
            const previous = acc.at(-1)?.path ?? base;
            acc.push({ name, path: `${previous}/${name}` });
            return acc;
        }, []);
};

/**
 * Folders first, then files, each A-Z.
 *
 * The server returns one flat alphabetical run, so a directory sat wherever
 * its name fell -- `assets` between `PROJECT.md` and `audio` -- and the eye
 * had no way to separate the things you can open from the things you can go
 * into. Every file manager groups them, and this is why.
 *
 * `localeCompare` with `numeric` so `shot-2` precedes `shot-10`, and with
 * case folded so a capitalised name does not sort into its own block above
 * the lowercase ones.
 */
function orderedEntries<T extends { name: string; kind: string }>(entries: readonly T[]): T[] {
    return [...entries].sort((a, b) => {
        const aDir = a.kind === 'directory';
        if (aDir !== (b.kind === 'directory')) return aDir ? -1 : 1;
        return a.name.localeCompare(b.name, undefined, {
            numeric: true,
            sensitivity: 'base',
        });
    });
}

/**
 * What the pane can actually put on the screen for this file.
 *
 * The pane used to ask one question -- "is it an image?" -- and send
 * everything else down the text branch. So opening an MP4 decoded the
 * container as UTF-8 and printed it: pages of replacement characters with
 * `ftypisom` legible at the top. A PDF and a .docx did the same thing. The
 * fix is to decide per file what can be *shown*, and to let everything else
 * be a file you download rather than a file rendered wrongly.
 */
type PreviewKind = 'image' | 'video' | 'audio' | 'pdf' | 'text' | 'opaque';

// Only the containers a browser will actually play. `mov` and `flac` are in
// because Safari plays both and the `onError` fallback catches the browsers
// that do not; `avi`, `mkv` and `wmv` are out because essentially nothing
// plays them, and offering a player that always fails is worse than offering
// the download straight away.
const VIDEO_EXTENSIONS = new Set(['mp4', 'm4v', 'mov', 'webm', 'ogv']);
const AUDIO_EXTENSIONS = new Set(['mp3', 'm4a', 'aac', 'wav', 'ogg', 'oga', 'flac', 'opus']);

function previewKindOf(path: string): PreviewKind {
    const previewType = getDocumentPreviewType(path);
    if (previewType === 'image') return 'image';
    if (previewType === 'pdf') return 'pdf';
    const name = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
    const dot = name.lastIndexOf('.');
    const extension = dot >= 0 ? name.slice(dot + 1) : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
    // `office` lands here with `unsupported`: a .docx is a zip, and this pane
    // has no renderer for one. The document viewer does; the sandbox pane is
    // deliberately the smaller surface.
    if (previewType === 'office' || previewType === 'unsupported') return 'opaque';
    return 'text';
}

type MediaNode = HTMLImageElement | HTMLVideoElement | HTMLAudioElement | HTMLIFrameElement;

/**
 * A file shown as itself: a picture, a player, or an embedded PDF.
 *
 * The `blob:` URL is attached to the node from an effect rather than passed
 * as a `src` prop, for two reasons. It keeps the URL's life tied to the
 * element's: StrictMode mounts as setup -> cleanup -> setup, so a URL made
 * during render is revoked by the first cleanup and never replaced, which is
 * the same trap `DownloadLink` documents. And it is what the effect rule
 * asks for -- the DOM is the external system here, so the effect writes to
 * it instead of round-tripping through React state.
 *
 * Mount this keyed on the path. `failed` is then reset by the remount rather
 * than by an effect, and a format this browser cannot decode falls back to
 * the same card an archive gets.
 */
function MediaPreview({
    kind,
    blob,
    path,
    sizeBytes,
}: {
    kind: PreviewKind;
    blob: Blob;
    path: string;
    sizeBytes: number;
}) {
    const node = useRef<MediaNode | null>(null);
    const [failed, setFailed] = useState(false);
    const showable = kind !== 'opaque' && kind !== 'text' && !failed;

    useEffect(() => {
        const element = node.current;
        if (!element) return;
        const url = URL.createObjectURL(blob);
        element.setAttribute('src', url);
        return () => URL.revokeObjectURL(url);
    }, [blob, showable]);

    if (!showable) return <FileFacts path={path} sizeBytes={sizeBytes} />;

    const onError = () => setFailed(true);
    if (kind === 'image') {
        return (
            // eslint-disable-next-line @next/next/no-img-element
            <img
                ref={node as React.RefObject<HTMLImageElement>}
                alt={path}
                className="max-w-full rounded-md"
                onError={onError}
            />
        );
    }
    if (kind === 'video') {
        return (
            <video
                ref={node as React.RefObject<HTMLVideoElement>}
                controls
                className="max-w-full rounded-md"
                onError={onError}
            >
                <track kind="captions" />
            </video>
        );
    }
    if (kind === 'audio') {
        return (
            <audio
                ref={node as React.RefObject<HTMLAudioElement>}
                controls
                className="w-full"
                onError={onError}
            />
        );
    }
    return (
        <iframe
            ref={node as React.RefObject<HTMLIFrameElement>}
            title={path}
            className="h-[60vh] w-full rounded-md border-0"
        />
    );
}

/** Name, type and size, for a file the pane is not going to render. */
function FileFacts({ path, sizeBytes }: { path: string; sizeBytes: number }) {
    const name = path.slice(path.lastIndexOf('/') + 1);
    return (
        <div className="flex items-center gap-3">
            <FileTypeIcon filename={name} size="lg" />
            <div className="min-w-0">
                <p className="truncate text-sm text-[var(--text-primary)]">{name}</p>
                <p className="text-xs text-[var(--text-tertiary)]">
                    {formatSize(sizeBytes)} · no preview for this kind of file
                </p>
            </div>
        </div>
    );
}

/**
 * Save a workspace file to the reader's own machine.
 *
 * The blob is already here — the hook fetched it to decide whether it could be
 * shown — so this costs nothing extra and is the only way out for the files the
 * pane refuses to render: an archive, a binary, anything past the text ceiling.
 * Without it those files could be listed and never opened.
 */
function DownloadLink({ blob, path }: { blob: Blob; path: string }) {
    // The object URL is minted when the button is pressed, not when the
    // component renders.
    //
    // Rendering it was the bug, and it failed silently. The URL was made in a
    // `useMemo` and revoked in an effect cleanup; React's StrictMode runs a
    // mount as setup -> cleanup -> setup, so the cleanup revoked it while the
    // memo -- whose dependency had not changed -- never made another. The
    // anchor was left pointing at a revoked `blob:` URL, and a revoked one
    // does nothing when clicked: no download, no error, nothing in the
    // console. An `<img src>` built the same way survives, because it
    // resolves during commit rather than on a click minutes later, which is
    // why the same shape works elsewhere in the app.
    //
    // Minting on click sidesteps the lifecycle entirely, and is what the
    // document viewer already does.
    const save = useCallback(() => {
        const href = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = href;
        link.download = path.slice(path.lastIndexOf('/') + 1);
        document.body.append(link);
        link.click();
        link.remove();
        // Revoked on a later turn of the loop: Safari and Firefox abandon the
        // download if the URL dies in the same tick as the click.
        setTimeout(() => URL.revokeObjectURL(href), 0);
    }, [blob, path]);

    return (
        <Button variant="quiet" size="xs" onClick={save}>
            <Download className="size-3.5" />
            Download
        </Button>
    );
}

function FileBody({ path }: { path: string }) {
    const kind = previewKindOf(path);
    const { data, isPending, error } = useWorkspaceFile(path, kind !== 'text');

    if (isPending) {
        return <p className="p-4 text-sm text-[var(--text-tertiary)]">Reading…</p>;
    }
    if (error) {
        return (
            <p className="p-4 text-sm text-[var(--text-tertiary)]">
                This file could not be read. It may have been removed since the list was taken.
            </p>
        );
    }
    if (data?.tooLarge) {
        return (
            <div className="flex flex-col items-start gap-2 p-4">
                <p className="text-sm text-[var(--text-tertiary)]">
                    {formatSize(data.sizeBytes)} is too large to show here. Download it, ask
                    the agent to summarise it, or open the part you need.
                </p>
                <DownloadLink blob={data.blob} path={path} />
            </div>
        );
    }
    if (kind !== 'text' && data) {
        return (
            <div className="flex flex-col items-start gap-3 p-4">
                <MediaPreview
                    key={path}
                    kind={kind}
                    blob={data.blob}
                    path={path}
                    sizeBytes={data.sizeBytes}
                />
                <DownloadLink blob={data.blob} path={path} />
            </div>
        );
    }
    if (!data?.text) return null;

    return (
        <div className="flex min-h-0 flex-col">
            <pre className="overflow-x-auto p-4 text-xs leading-5 text-[var(--text-secondary)]">
                {data.text}
            </pre>
            <div className="px-4 pb-4">
                <DownloadLink blob={data.blob} path={path} />
            </div>
        </div>
    );
}

/**
 * The conversation's sandbox files, read-only.
 *
 * Rooted at the conversation's own directory rather than at the workspace root,
 * because the sandbox is one machine per *person*: everything every conversation
 * has ever written is on it, and opening on all of it buries the handful of
 * files this conversation actually produced. Walking up past the root is
 * allowed — that is where a cloned repo lives.
 *
 * Listing does not start a paused workspace — that is what the asleep state is,
 * and it is deliberate: a pane that woke a sandbox on every render would hold
 * compute open for as long as it was on screen. Opening a file is the
 * interactive act, and the person asks for it.
 */
export function WorkspaceFilesPane({
    workspaceCwd,
}: {
    /** The conversation's `workspace_cwd`, as the server resolved it. */
    workspaceCwd?: string;
}) {
    // Given, not derived. This pane used to build the path itself as
    // `/workspace/conversations/{id}`, mirroring `get_workspace_cwd()` — but
    // mirroring its *fallback* branch, which only contexts without a
    // conversation row ever reach. Every real run resolves to
    // `/workspace/c/{date}/{slug}`, so the pane asked for a directory that has
    // never existed. It showed no error because a missing directory and an
    // empty one answered identically; it simply looked like the agent had
    // written nothing.
    //
    // Until the record arrives there is no honest conversation directory to
    // show, so the whole machine is the fallback rather than a guess.
    // The machine, not the project root: with no conversation directory to
    // show there is nothing to be specific about, and the crumb above already
    // says "Whole computer". These were the same path until the durable root
    // moved into the home, which is what made the label wrong here.
    const home = workspaceCwd ?? HOME_ROOT;
    const [directory, setDirectory] = useState(home);
    const [wake, setWake] = useState(false);
    const [selected, setSelected] = useState<string | null>(null);
    // Where this page started. A directory bigger than one page was a dead end
    // before: the response counted the rest and offered no way to reach them.
    const [after, setAfter] = useState<string | undefined>(undefined);

    // The conversation record is fetched, so the first render of this pane
    // almost always has no `workspaceCwd` yet. `useState` takes its argument
    // once, so without this the pane opened on `/workspace` and stayed there
    // for the life of the mount -- which is the same "shows the wrong
    // directory" bug in a new place, and the reason the fix has to follow the
    // prop rather than merely seed from it.
    //
    // Adjusted during render against the previous value rather than in an
    // effect: React documents this as the way to reset state when a prop
    // changes, and it re-renders before painting instead of showing the wrong
    // directory for a frame and fetching it.
    const [homeSeen, setHomeSeen] = useState(home);
    if (home !== homeSeen) {
        setHomeSeen(home);
        setDirectory(home);
        setSelected(null);
        setAfter(undefined);
    }

    const { data, isPending, error, refetch, isFetching } = useWorkspaceFiles(
        directory,
        wake,
        after,
    );

    const open = useCallback((path: string, isDirectory: boolean) => {
        if (isDirectory) {
            setDirectory(path);
            setSelected(null);
            setAfter(undefined);
            return;
        }
        setSelected(path);
    }, []);

    // The ceiling is the durable home, which is as far up as the files route
    // will answer -- and it is a level above where projects live, so "whole
    // computer" now really is the machine rather than the project root. Taken
    // from the listing when there is one: the constant is only a first guess,
    // and the last time this value moved the hardcoded copy stayed behind.
    const ceiling = data?.home_root ?? HOME_ROOT;
    const parent = parentOf(directory, ceiling);
    // Above the conversation's own directory the crumb is the whole machine,
    // because that is what the person is actually looking at up there.
    const inHome = directory === home || directory.startsWith(`${home}/`);
    const segments = segmentsOf(directory, home, ceiling);

    if (data?.sleeping) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <p className="text-sm text-[var(--text-secondary)]">
                    This computer is asleep. Its files are still there.
                </p>
                <Button variant="secondary" size="sm" onClick={() => setWake(true)}>
                    Wake it and show them
                </Button>
            </div>
        );
    }

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center gap-1 border-b border-[var(--row-border)] px-3 py-2 text-xs">
                <Button
                    variant="quiet"
                    size="xs"
                    onClick={() => {
                        setDirectory(inHome ? home : ceiling);
                        setSelected(null);
                        setAfter(undefined);
                    }}
                >
                    {inHome && workspaceCwd ? 'This conversation' : 'Whole computer'}
                </Button>
                {segments.map((segment) => (
                    <span key={segment.path} className="flex items-center gap-1">
                        <ChevronRight className="size-3 text-[var(--text-tertiary)]" />
                        <Button
                            variant="quiet"
                            size="xs"
                            onClick={() => {
                                setDirectory(segment.path);
                                setSelected(null);
                            }}
                        >
                            {segment.name}
                        </Button>
                    </span>
                ))}
                <Button
                    variant="quiet"
                    size="xs"
                    onClick={() => void refetch()}
                    aria-label="Refresh"
                    className="ml-auto"
                >
                    <RefreshCw className={cn('size-3.5', isFetching && 'lemma-spin')} />
                </Button>
            </div>

            <div className="flex min-h-0 flex-1">
                <div className="w-1/2 min-w-0 overflow-y-auto border-r border-[var(--row-border)]">
                    {isPending ? (
                        <p className="p-4 text-sm text-[var(--text-tertiary)]">Looking…</p>
                    ) : error ? (
                        <p className="p-4 text-sm text-[var(--text-tertiary)]">
                            This computer is not reachable right now.
                        </p>
                    ) : (
                        <ul className="py-1">
                            {parent !== null ? (
                                <li>
                                    <Button
                                        variant="quiet"
                                        size="sm"
                                        onClick={() => open(parent, true)}
                                        className="w-full justify-start gap-2 rounded-none px-3 font-normal"
                                    >
                                        <Folder className="size-3.5" />
                                        <span>..</span>
                                    </Button>
                                </li>
                            ) : null}
                            {orderedEntries(data?.entries ?? []).map((entry) => {
                                const isDirectory = entry.kind === 'directory';
                                return (
                                    <li
                                        key={entry.path}
                                        // Selection is a property of the row, not a
                                        // restyling of the control inside it.
                                        className={cn(
                                            selected === entry.path && 'bg-[var(--surface-2)]',
                                        )}
                                    >
                                        <Button
                                            variant="quiet"
                                            size="sm"
                                            onClick={() => open(entry.path, isDirectory)}
                                            className="w-full justify-start gap-2 rounded-none px-3 font-normal"
                                        >
                                            <span className="flex size-4 shrink-0 items-center justify-center">
                                                {isDirectory ? (
                                                    <Folder className="size-4 text-[var(--text-secondary)]" />
                                                ) : (
                                                    <FileTypeIcon filename={entry.name} size="sm" />
                                                )}
                                            </span>
                                            <span className="min-w-0 flex-1 truncate text-left text-[var(--text-secondary)]">
                                                {entry.name}
                                            </span>
                                            {!isDirectory ? (
                                                <span className="shrink-0 text-xs tabular-nums text-[var(--text-tertiary)]">
                                                    {formatSize(entry.size_bytes)}
                                                </span>
                                            ) : null}
                                        </Button>
                                    </li>
                                );
                            })}
                            {data?.truncated && data.next_after ? (
                                <li className="px-3 py-1.5">
                                    <Button
                                        variant="quiet"
                                        size="xs"
                                        onClick={() => setAfter(data.next_after ?? undefined)}
                                    >
                                        Show more
                                    </Button>
                                </li>
                            ) : null}
                            {after ? (
                                <li className="px-3 py-1.5">
                                    <Button
                                        variant="quiet"
                                        size="xs"
                                        onClick={() => setAfter(undefined)}
                                    >
                                        Back to the start
                                    </Button>
                                </li>
                            ) : null}
                            {!data?.entries.length && parent === null ? (
                                <li className="px-3 py-4 text-sm text-[var(--text-tertiary)]">
                                    Nothing here yet.
                                </li>
                            ) : null}
                        </ul>
                    )}
                </div>

                <div className="min-w-0 flex-1 overflow-auto">
                    {selected ? (
                        <FileBody path={selected} />
                    ) : (
                        <p className="p-4 text-sm text-[var(--text-tertiary)]">
                            Pick a file to read it.
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
