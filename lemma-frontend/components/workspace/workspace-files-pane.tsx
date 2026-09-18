'use client';

import { useCallback, useState } from 'react';

import { FileTypeIcon } from '@/components/documents/file-type-icon';
import { Button } from '@/components/ui/button';
import { ChevronRight, Folder, RefreshCw } from '@/components/ui/icons';
import { HOME_ROOT, useWorkspaceFiles } from '@/lib/hooks/use-workspace-files';
import { FileBody, formatSize, orderedEntries } from './file-preview';

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
import { cn } from '@/lib/utils';

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
                        <FileBody
                            path={selected}
                            sizeBytes={
                                data?.entries.find((entry) => entry.path === selected)
                                    ?.size_bytes ?? 0
                            }
                        />
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
