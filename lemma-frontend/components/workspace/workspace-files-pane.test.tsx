// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { WorkspaceFilesPane } from './workspace-files-pane';

const asked = vi.hoisted(() => ({ paths: [] as string[] }));

vi.mock('@/lib/hooks/use-workspace-files', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@/lib/hooks/use-workspace-files')>();
    return {
        ...actual,
        useWorkspaceFiles: (path: string) => {
            asked.paths.push(path);
            return {
                data: { path, sleeping: false, truncated: false, exists: true, entries: [] },
                isPending: false,
                error: null,
                refetch: vi.fn(),
                isFetching: false,
            };
        },
        useWorkspaceFile: () => ({ data: undefined, isPending: false, error: null }),
    };
});

afterEach(() => {
    asked.paths = [];
    cleanup();
});

describe('which directory the pane opens', () => {
    it('opens the directory the server resolved, not one it worked out', () => {
        // The bug: the pane built `/workspace/conversations/{id}` itself,
        // mirroring `get_workspace_cwd()`'s *fallback* branch. Every real run
        // resolves to `/workspace/c/{date}/{slug}`, so the pane asked for a
        // directory that has never existed — and saw no error, because a
        // missing directory and an empty one answer identically.
        render(
            <WorkspaceFilesPane workspaceCwd="/workspace/c/2026-09-16/quiet-harbour" />,
        );

        expect(asked.paths[0]).toBe('/workspace/c/2026-09-16/quiet-harbour');
        expect(asked.paths.join(' ')).not.toContain('/workspace/conversations/');
    });

    it('shows the whole machine rather than guessing when the cwd is unknown', () => {
        // A conversation that has not been created yet has no resolved cwd.
        // The honest answer is the machine, not a path invented from the id.
        render(<WorkspaceFilesPane />);

        expect(asked.paths[0]).toBe('/workspace');
        expect(screen.getByRole('button', { name: 'Whole computer' })).toBeTruthy();
    });
});
