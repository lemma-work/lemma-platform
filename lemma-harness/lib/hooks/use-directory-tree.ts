'use client';

import { useQueries } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import type { WorkspaceFileEntry, WorkspaceFileListResponse } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export interface TreeNode {
    /** The absolute path, which is also the node's identity. */
    id: string;
    name: string;
    kind: WorkspaceFileEntry['kind'];
    sizeBytes: number;
    /**
     * A row that is not a file: "this folder could not be read", "there is
     * more here than is shown". Drawn as plain text, and not openable.
     */
    problem?: string;
    /**
     * `undefined` marks a leaf. A directory nobody has opened yet is `[]`,
     * which is how the tree knows to draw a disclosure arrow for something it
     * has not fetched: `null`/`undefined` would render it as a file.
     */
    children?: TreeNode[];
}

/**
 * How many pages of one directory this will follow before it stops.
 *
 * The listing returns 1,000 entries at a time, so this is 50,000 -- past any
 * directory a person browses, and well short of the `node_modules` that would
 * otherwise turn one disclosure arrow into a hundred requests. Stopping early
 * is reported rather than hidden, which is the whole point.
 */
const MAX_PAGES = 50;

/** Every page of one directory, followed to the end or to `MAX_PAGES`. */
async function wholeDirectory(path: string): Promise<WorkspaceFileListResponse> {
    const client = getLemmaClient();
    let page = await client.workspace.listFiles({ path });
    const entries = [...page.entries];
    let pages = 1;
    while (page.next_after && pages < MAX_PAGES) {
        page = await client.workspace.listFiles({ path, after: page.next_after });
        entries.push(...page.entries);
        pages += 1;
    }
    return {
        ...page,
        path,
        entries,
        // True only if we stopped early. The per-page flag means "this page
        // is not all of it", which after following the pages is no longer the
        // question being asked.
        truncated: Boolean(page.next_after),
    };
}

/** The id of a row that stands for a condition rather than for a file. */
const noticeId = (path: string, problem: string) => `${path}#notice:${problem}`;

/**
 * A lazily-filled directory tree.
 *
 * One request per directory somebody actually opens, rather than one
 * recursive walk. The listing API answers a single directory by design, and
 * that is the right shape for this: a workspace with a `node_modules` in it
 * is the ordinary case, and a tree that fetched everything up front would
 * spend its first ten seconds reading folders nobody asked about.
 *
 * Directories already open are refetched together through `useQueries`, so
 * opening a third does not re-render the first two from scratch.
 *
 * **One directory means all of it.** The listing is paginated and this took
 * the first page only, so a folder of more than a thousand entries quietly
 * became a folder of a thousand -- no arrow, no notice, and nothing to tell
 * it from the real thing. The compact pane has a "show more" button; a tree
 * has nowhere to put one, so it follows the pages itself and says so when it
 * gives up.
 *
 * **Its own cache key**, not the pane's. The pane caches one page under
 * `['workspace-files', path, wake, after]` and pages by changing `after`;
 * writing a merged all-pages listing into that same slot would hand the pane
 * a response whose entries do not match the `after` it asked for.
 */
export function useDirectoryTree(root: string) {
    const [open, setOpen] = useState<string[]>([root]);

    // The root is always fetched; the rest are whatever is expanded. Deduped
    // and sorted so the query list is stable across renders that did not
    // actually change which directories are open.
    const wanted = useMemo(
        () => Array.from(new Set([root, ...open])).sort(),
        [root, open],
    );

    const results = useQueries({
        queries: wanted.map((path) => ({
            queryKey: ['workspace-tree', path] as const,
            queryFn: () => wholeDirectory(path),
            staleTime: 5_000,
        })),
    });

    const listings = useMemo(() => {
        const found = new Map<string, WorkspaceFileListResponse>();
        wanted.forEach((path, index) => {
            const data = results[index]?.data;
            if (data) found.set(path, data);
        });
        return found;
    }, [wanted, results]);

    // Which directories were asked for and came back a failure. Kept apart
    // from `listings` because absent means "not fetched yet, or still in
    // flight", and a folder that is still loading must not be drawn as one
    // that is broken.
    const failures = useMemo(() => {
        const found = new Map<string, Error>();
        wanted.forEach((path, index) => {
            const error = results[index]?.error;
            if (error) found.set(path, error as Error);
        });
        return found;
    }, [wanted, results]);

    // Declared inside the memo rather than as a `useCallback`, because it
    // recurses: a callback that names itself is a reference to a binding that
    // does not exist yet at the point the linter reads it, and hoisting it
    // out would mean memoising a function whose only caller is right here.
    const data = useMemo(() => {
        const notice = (path: string, problem: string): TreeNode => ({
            id: noticeId(path, problem),
            name: problem,
            kind: 'file',
            sizeBytes: 0,
            problem,
        });
        const build = (path: string, depth: number): TreeNode[] => {
            if (failures.has(path)) {
                // An open folder that failed used to render as an open folder
                // with nothing in it, which reads as "empty" -- the one thing
                // it is not.
                return [notice(path, 'This folder could not be read.')];
            }
            const listing = listings.get(path);
            if (!listing) return [];
            // A guard, not a product decision: a symlink loop would otherwise
            // be an infinite expansion, and nothing legitimate is 32 deep.
            const deeper = depth < 32;
            const rows: TreeNode[] = listing.entries.map((entry) => ({
                id: entry.path,
                name: entry.name,
                kind: entry.kind,
                sizeBytes: entry.size_bytes,
                children:
                    entry.kind === 'directory' && deeper
                        ? build(entry.path, depth + 1)
                        : undefined,
            }));
            if (listing.truncated) {
                rows.push(
                    notice(path, 'There is more in this folder than is shown here.'),
                );
            }
            return rows;
        };
        return build(root, 0);
    }, [listings, failures, root]);

    const onToggle = useCallback((path: string) => {
        setOpen((current) =>
            current.includes(path)
                ? current.filter((each) => each !== path)
                : [...current, path],
        );
    }, []);

    const rootListing = listings.get(root);
    return {
        data,
        onToggle,
        sleeping: Boolean(rootListing?.sleeping),
        homeRoot: rootListing?.home_root,
        /** The root itself could not be read. An empty tree would be a lie. */
        error: failures.get(root) ?? null,
        /** The root is not there, as against there and empty. */
        missing: Boolean(rootListing && rootListing.exists === false),
        loading: results.some((result) => result.isPending),
    };
}
