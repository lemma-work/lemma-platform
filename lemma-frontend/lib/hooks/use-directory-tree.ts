'use client';

import { useQueries } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import type { WorkspaceFileEntry, WorkspaceFileListResponse } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { workspaceFilesQueryKey } from '@/lib/hooks/use-workspace-files';

export interface TreeNode {
    /** The absolute path, which is also the node's identity. */
    id: string;
    name: string;
    kind: WorkspaceFileEntry['kind'];
    sizeBytes: number;
    /**
     * `undefined` marks a leaf. A directory nobody has opened yet is `[]`,
     * which is how the tree knows to draw a disclosure arrow for something it
     * has not fetched: `null`/`undefined` would render it as a file.
     */
    children?: TreeNode[];
}

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
            queryKey: workspaceFilesQueryKey(path, false, undefined),
            queryFn: () => getLemmaClient().workspace.listFiles({ path }),
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

    // Declared inside the memo rather than as a `useCallback`, because it
    // recurses: a callback that names itself is a reference to a binding that
    // does not exist yet at the point the linter reads it, and hoisting it
    // out would mean memoising a function whose only caller is right here.
    const data = useMemo(() => {
        const build = (path: string, depth: number): TreeNode[] => {
            const listing = listings.get(path);
            if (!listing) return [];
            // A guard, not a product decision: a symlink loop would otherwise
            // be an infinite expansion, and nothing legitimate is 32 deep.
            const deeper = depth < 32;
            return listing.entries.map((entry) => ({
                id: entry.path,
                name: entry.name,
                kind: entry.kind,
                sizeBytes: entry.size_bytes,
                children:
                    entry.kind === 'directory' && deeper
                        ? build(entry.path, depth + 1)
                        : undefined,
            }));
        };
        return build(root, 0);
    }, [listings, root]);

    const onToggle = useCallback((path: string) => {
        setOpen((current) =>
            current.includes(path)
                ? current.filter((each) => each !== path)
                : [...current, path],
        );
    }, []);

    return {
        data,
        onToggle,
        sleeping: Boolean(listings.get(root)?.sleeping),
        homeRoot: listings.get(root)?.home_root,
        loading: results.some((result) => result.isPending),
    };
}
