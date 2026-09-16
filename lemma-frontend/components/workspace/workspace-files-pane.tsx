'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

import { FileTypeIcon } from '@/components/documents/file-type-icon';
import { getDocumentPreviewType } from '@/components/documents/preview-renderers';
import { Button } from '@/components/ui/button';
import { ChevronRight, Folder, RefreshCw } from '@/components/ui/icons';
import {
    WORKSPACE_ROOT,
    conversationDirectory,
    useWorkspaceFile,
    useWorkspaceFiles,
} from '@/lib/hooks/use-workspace-files';
import { cn } from '@/lib/utils';

const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const parentOf = (path: string): string | null => {
    if (path === WORKSPACE_ROOT) return null;
    const cut = path.lastIndexOf('/');
    return cut <= WORKSPACE_ROOT.length - 1 ? WORKSPACE_ROOT : path.slice(0, cut);
};

/** Segments between `from` and `path`, for the breadcrumb. */
const segmentsOf = (path: string, from: string): { name: string; path: string }[] => {
    if (path === from) return [];
    const inside = path.startsWith(`${from}/`);
    const base = inside ? from : WORKSPACE_ROOT;
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
 * Save a workspace file to the reader's own machine.
 *
 * The blob is already here — the hook fetched it to decide whether it could be
 * shown — so this costs nothing extra and is the only way out for the files the
 * pane refuses to render: an archive, a binary, anything past the text ceiling.
 * Without it those files could be listed and never opened.
 */
function DownloadLink({ blob, path }: { blob: Blob; path: string }) {
    const href = useMemo(() => URL.createObjectURL(blob), [blob]);
    useEffect(() => () => URL.revokeObjectURL(href), [href]);

    return (
        <a
            href={href}
            download={path.slice(path.lastIndexOf('/') + 1)}
            className="text-xs underline text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
            Download
        </a>
    );
}

function FileBody({ path }: { path: string }) {
    const isImage = getDocumentPreviewType(path) === 'image';
    const { data, isPending, error } = useWorkspaceFile(path, isImage);

    const imageUrl = useMemo(() => {
        if (!data || !isImage) return null;
        return URL.createObjectURL(data.blob);
    }, [data, isImage]);

    useEffect(() => {
        return () => {
            if (imageUrl) URL.revokeObjectURL(imageUrl);
        };
    }, [imageUrl]);

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
    if (imageUrl) {
        return (
            <div className="flex flex-col items-start gap-2 p-4">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={imageUrl} alt={path} className="max-w-full" />
                <DownloadLink blob={data!.blob} path={path} />
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
export function WorkspaceFilesPane({ conversationId }: { conversationId?: string }) {
    const home = conversationId ? conversationDirectory(conversationId) : WORKSPACE_ROOT;
    const [directory, setDirectory] = useState(home);
    const [wake, setWake] = useState(false);
    const [selected, setSelected] = useState<string | null>(null);
    // Where this page started. A directory bigger than one page was a dead end
    // before: the response counted the rest and offered no way to reach them.
    const [after, setAfter] = useState<string | undefined>(undefined);

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

    const parent = parentOf(directory);
    // Above the conversation's own directory the crumb is the whole machine,
    // because that is what the person is actually looking at up there.
    const inHome = directory === home || directory.startsWith(`${home}/`);
    const segments = segmentsOf(directory, home);

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
                        setDirectory(inHome ? home : WORKSPACE_ROOT);
                        setSelected(null);
                        setAfter(undefined);
                    }}
                >
                    {inHome && conversationId ? 'This conversation' : 'Whole computer'}
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
                            {(data?.entries ?? []).map((entry) => {
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
                                            {isDirectory ? (
                                                <Folder className="size-3.5 text-[var(--text-tertiary)]" />
                                            ) : (
                                                <FileTypeIcon filename={entry.name} size="sm" />
                                            )}
                                            <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
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
