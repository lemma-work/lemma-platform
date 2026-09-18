// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';

import { useDirectoryTree } from './use-directory-tree';

/**
 * The tree fetches one directory per thing somebody opens.
 *
 * Worth testing on its own because the alternative is easy to reach for and
 * wrong: one recursive walk up front. A workspace with a `node_modules` in it
 * is the ordinary case, and a tree that read every folder before drawing
 * anything would spend its first seconds in directories nobody asked about.
 */

const listings = vi.hoisted(
    () => ({ byPath: {} as Record<string, unknown>, asked: [] as string[] }),
);

vi.mock('@/lib/sdk/lemma-client', () => ({
    getLemmaClient: () => ({
        workspace: {
            listFiles: async ({ path }: { path: string }) => {
                listings.asked.push(path);
                return (
                    listings.byPath[path] ?? {
                        path,
                        home_root: '/home/user',
                        workspace_root: '/home/user/lemma',
                        sleeping: false,
                        truncated: false,
                        exists: true,
                        entries: [],
                    }
                );
            },
        },
    }),
}));

const dir = (path: string, name: string) => ({
    path,
    name,
    kind: 'directory' as const,
    size_bytes: 0,
    modified_at: new Date().toISOString(),
});
const file = (path: string, name: string, size = 10) => ({
    path,
    name,
    kind: 'file' as const,
    size_bytes: size,
    modified_at: new Date().toISOString(),
});

const listing = (path: string, entries: unknown[], over: Record<string, unknown> = {}) => ({
    path,
    home_root: '/home/user',
    workspace_root: '/home/user/lemma',
    sleeping: false,
    truncated: false,
    exists: true,
    entries,
    ...over,
});

function wrapper({ children }: { children: React.ReactNode }) {
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
    listings.byPath = {};
    listings.asked = [];
    cleanup();
});

describe('filling a tree as somebody opens it', () => {
    it('reads the root and nothing else to begin with', async () => {
        listings.byPath['/home/user/lemma'] = listing('/home/user/lemma', [
            dir('/home/user/lemma/project', 'project'),
            file('/home/user/lemma/notes.md', 'notes.md'),
        ]);

        const { result } = renderHook(() => useDirectoryTree('/home/user/lemma'), {
            wrapper,
        });

        await waitFor(() => expect(result.current.data).toHaveLength(2));
        expect(listings.asked).toEqual(['/home/user/lemma']);
    });

    it('marks an unopened directory as openable rather than as a file', async () => {
        // `children: []` and `children: undefined` are the difference between
        // a disclosure arrow and a leaf. A directory nobody has opened has to
        // be the former, or there is no way to open it.
        listings.byPath['/home/user/lemma'] = listing('/home/user/lemma', [
            dir('/home/user/lemma/project', 'project'),
            file('/home/user/lemma/notes.md', 'notes.md'),
        ]);

        const { result } = renderHook(() => useDirectoryTree('/home/user/lemma'), {
            wrapper,
        });
        await waitFor(() => expect(result.current.data).toHaveLength(2));

        const [folder, leaf] = result.current.data;
        expect(folder.children).toEqual([]);
        expect(leaf.children).toBeUndefined();
    });

    it('fetches a directory only when it is opened', async () => {
        listings.byPath['/home/user/lemma'] = listing('/home/user/lemma', [
            dir('/home/user/lemma/project', 'project'),
        ]);
        listings.byPath['/home/user/lemma/project'] = listing(
            '/home/user/lemma/project',
            [file('/home/user/lemma/project/main.py', 'main.py')],
        );

        const { result } = renderHook(() => useDirectoryTree('/home/user/lemma'), {
            wrapper,
        });
        await waitFor(() => expect(result.current.data).toHaveLength(1));
        expect(listings.asked).not.toContain('/home/user/lemma/project');

        act(() => result.current.onToggle('/home/user/lemma/project'));

        await waitFor(() =>
            expect(result.current.data[0].children).toHaveLength(1),
        );
        expect(result.current.data[0].children?.[0].name).toBe('main.py');
    });

    it('forgets a directory that is closed again', async () => {
        listings.byPath['/home/user/lemma'] = listing('/home/user/lemma', [
            dir('/home/user/lemma/project', 'project'),
        ]);
        listings.byPath['/home/user/lemma/project'] = listing(
            '/home/user/lemma/project',
            [file('/home/user/lemma/project/main.py', 'main.py')],
        );

        const { result } = renderHook(() => useDirectoryTree('/home/user/lemma'), {
            wrapper,
        });
        await waitFor(() => expect(result.current.data).toHaveLength(1));

        act(() => result.current.onToggle('/home/user/lemma/project'));
        await waitFor(() => expect(result.current.data[0].children).toHaveLength(1));

        act(() => result.current.onToggle('/home/user/lemma/project'));
        await waitFor(() => expect(result.current.data[0].children).toEqual([]));
    });

    it('reports a sleeping computer rather than an empty machine', async () => {
        // An empty list and a paused sandbox look identical otherwise, which
        // is the same confusion that made a pane pointed at a missing
        // directory read as a working, empty folder.
        listings.byPath['/home/user/lemma'] = listing('/home/user/lemma', [], {
            sleeping: true,
        });

        const { result } = renderHook(() => useDirectoryTree('/home/user/lemma'), {
            wrapper,
        });

        await waitFor(() => expect(result.current.sleeping).toBe(true));
    });
});
