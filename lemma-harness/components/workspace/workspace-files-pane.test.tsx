// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { WorkspaceFilesPane } from './workspace-files-pane';

const asked = vi.hoisted(() => ({ paths: [] as string[] }));
// What `useWorkspaceFile` answers, so a test can put a real file in the
// detail pane instead of the empty default.
const listing = vi.hoisted(() => ({
    entries: [] as Array<Record<string, unknown>>,
    homeRoot: '/home/user',
}));
const file = vi.hoisted(() => ({
    data: undefined as { blob: Blob; text: string | null; tooLarge: boolean; sizeBytes: number } | undefined,
}));

const workspace = vi.hoisted(() => ({ status: undefined as { state: string; detail?: string } | undefined }));

vi.mock('@/lib/hooks/use-workspace-status', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@/lib/hooks/use-workspace-status')>();
    return { ...actual, useWorkspaceStatus: () => ({ data: workspace.status }) };
});

vi.mock('@/lib/hooks/use-workspace-files', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@/lib/hooks/use-workspace-files')>();
    return {
        ...actual,
        useWorkspaceFiles: (path: string) => {
            asked.paths.push(path);
            return {
                data: {
                    path,
                    home_root: listing.homeRoot,
                    workspace_root: `${listing.homeRoot}/lemma`,
                    sleeping: false,
                    truncated: false,
                    exists: true,
                    entries: listing.entries,
                },
                isPending: false,
                error: null,
                refetch: vi.fn(),
                isFetching: false,
            };
        },
        useWorkspaceFile: () => ({ data: file.data, isPending: false, error: null }),
    };
});

afterEach(() => {
    asked.paths = [];
    file.data = undefined;
    listing.entries = [];
    listing.homeRoot = '/home/user';
    cleanup();
});

describe('which directory the pane opens', () => {
    it('opens the directory the server resolved, not one it worked out', () => {
        // The bug: the pane built `/home/user/lemma/conversations/{id}` itself,
        // mirroring `get_workspace_cwd()`'s *fallback* branch. Every real run
        // resolves to `~/lemma/c/{date}/{slug}`, so the pane asked for a
        // directory that has never existed — and saw no error, because a
        // missing directory and an empty one answer identically.
        render(
            <WorkspaceFilesPane workspaceCwd="/home/user/lemma/c/2026-09-16/quiet-harbour" />,
        );

        expect(asked.paths[0]).toBe('/home/user/lemma/c/2026-09-16/quiet-harbour');
        expect(asked.paths.join(' ')).not.toContain('/conversations/');
    });

    it('shows the whole machine rather than guessing when the cwd is unknown', () => {
        // A conversation that has not been created yet has no resolved cwd.
        // The honest answer is the machine, not a path invented from the id.
        render(<WorkspaceFilesPane />);

        expect(asked.paths[0]).toBe('/home/user');
        expect(screen.getByRole('button', { name: 'Whole computer' })).toBeTruthy();
    });

    it('follows the cwd when the conversation record arrives', () => {
        // The pane almost always mounts before the record is fetched, so the
        // first render has no cwd at all. `useState` takes its argument once —
        // seeding from the prop is not following it, and without this the pane
        // opened on the root and stayed there for the life of the mount.
        const { rerender } = render(<WorkspaceFilesPane />);
        expect(asked.paths[0]).toBe('/home/user');

        rerender(<WorkspaceFilesPane workspaceCwd="/home/user/lemma/c/2026-09-16/quiet-harbour" />);

        expect(asked.paths.at(-1)).toBe('/home/user/lemma/c/2026-09-16/quiet-harbour');
        expect(screen.getByRole('button', { name: 'This conversation' })).toBeTruthy();
    });

    it('stops climbing at the root the server reports, not one it was compiled with', () => {
        // The drift this exists to stop. The sandbox root moved into the home
        // and the frontend kept its own copy of the old value, so it asked for
        // a directory that no longer existed -- and a missing directory lists
        // exactly like an empty one, so it looked like a machine with nothing
        // on it. Every listing now carries the root, and taking the ceiling
        // from there is what makes a third move a non-event.
        listing.homeRoot = '/srv/somewhere-else';

        render(<WorkspaceFilesPane workspaceCwd="/srv/somewhere-else/project" />);
        act(() => screen.getByRole('button', { name: '..' }).click());

        expect(asked.paths.at(-1)).toBe('/srv/somewhere-else');
        // At the ceiling there is nowhere further up to offer.
        expect(screen.queryByRole('button', { name: '..' })).toBeNull();
    });
});

describe('saving a file to your own machine', () => {
    it('mints the object URL when pressed, not when rendered', async () => {
        // The bug, and why it was invisible. The URL used to be made in a
        // `useMemo` and revoked in an effect cleanup, and StrictMode runs a
        // mount as setup -> cleanup -> setup: the cleanup revoked it, the memo
        // never made another, and the anchor pointed at a revoked `blob:` URL.
        // Clicking a revoked one does nothing at all -- no download, no error,
        // nothing in the console.
        //
        // So this asserts the ordering that makes that impossible: no URL
        // exists until the button is pressed.
        const created: string[] = [];
        const revoked: string[] = [];
        const realCreate = URL.createObjectURL;
        const realRevoke = URL.revokeObjectURL;
        URL.createObjectURL = vi.fn(() => {
            const url = `blob:fake/${created.length}`;
            created.push(url);
            return url;
        }) as unknown as typeof URL.createObjectURL;
        URL.revokeObjectURL = vi.fn((url: string) => void revoked.push(url));

        try {
            file.data = {
                blob: new Blob(['hello']),
                text: 'hello',
                tooLarge: false,
                sizeBytes: 5,
            };
            listing.entries = [
                { path: '/home/user/lemma/notes.txt', name: 'notes.txt', kind: 'file', size_bytes: 5 },
            ];
            render(<WorkspaceFilesPane workspaceCwd="/home/user/lemma" />);
            screen.getByRole('button', { name: /notes\.txt/ }).click();

            // Rendering the detail pane must not have minted anything.
            expect(created).toEqual([]);

            const button = await screen.findByRole('button', { name: /download/i });
            let clicked = false;
            const realClick = HTMLAnchorElement.prototype.click;
            HTMLAnchorElement.prototype.click = function () {
                clicked = true;
                // The href must still be live at the moment of the click.
                expect(revoked).not.toContain(this.getAttribute('href'));
            };
            try {
                button.click();
            } finally {
                HTMLAnchorElement.prototype.click = realClick;
            }

            expect(created).toHaveLength(1);
            expect(clicked).toBe(true);
        } finally {
            URL.createObjectURL = realCreate;
            URL.revokeObjectURL = realRevoke;
        }
    });
});

describe('what the detail pane will and will not render', () => {
    // The report: opening a video showed the MP4 container decoded as UTF-8
    // -- `ftypisom` and then screens of replacement characters. The pane
    // asked one question, "is this an image?", and sent everything else down
    // the text branch, so video, audio, PDF and .docx all printed their bytes.
    it('offers a video as a player rather than as text', async () => {
        file.data = {
            blob: new Blob([new Uint8Array([0, 0, 0, 32])], { type: 'video/mp4' }),
            text: null,
            tooLarge: false,
            sizeBytes: 4,
        };
        listing.entries = [
            { path: '/home/user/lemma/clip.mp4', name: 'clip.mp4', kind: 'file', size_bytes: 4 },
        ];
        render(<WorkspaceFilesPane workspaceCwd="/home/user/lemma" />);
        screen.getByRole('button', { name: /clip\.mp4/ }).click();
        expect(await screen.findByRole('button', { name: /download/i })).toBeTruthy();

        expect(document.querySelector('video')).toBeTruthy();
        expect(document.querySelector('pre')).toBeNull();
    });

    it('gives a file it cannot show its name, size and a download, and no <pre>', async () => {
        file.data = {
            blob: new Blob([new Uint8Array([80, 75, 3, 4])]),
            text: null,
            tooLarge: false,
            sizeBytes: 2048,
        };
        listing.entries = [
            { path: '/home/user/lemma/report.docx', name: 'report.docx', kind: 'file', size_bytes: 2048 },
        ];
        render(<WorkspaceFilesPane workspaceCwd="/home/user/lemma" />);
        screen.getByRole('button', { name: /report\.docx/ }).click();
        expect(await screen.findByRole('button', { name: /download/i })).toBeTruthy();

        expect(document.querySelector('pre')).toBeNull();
        expect(screen.getAllByText(/no preview for this kind of file/).length).toBeGreaterThan(0);
    });

    it('still shows a text file as text', async () => {
        file.data = {
            blob: new Blob(['hello']),
            text: 'hello',
            tooLarge: false,
            sizeBytes: 5,
        };
        listing.entries = [
            { path: '/home/user/lemma/notes.txt', name: 'notes.txt', kind: 'file', size_bytes: 5 },
        ];
        render(<WorkspaceFilesPane workspaceCwd="/home/user/lemma" />);
        screen.getByRole('button', { name: /notes\.txt/ }).click();
        expect(await screen.findByRole('button', { name: /download/i })).toBeTruthy();

        expect(document.querySelector('pre')?.textContent).toBe('hello');
    });
});

describe('while the computer is coming up', () => {
    afterEach(() => {
        workspace.status = undefined;
        cleanup();
    });

    it('says it is preparing rather than asleep or failing', () => {
        workspace.status = { state: 'downloading', detail: 'Downloading the workspace image.' };
        render(<WorkspaceFilesPane workspaceCwd="/home/user/lemma" />);
        expect(screen.getByText('Preparing your workspace…')).toBeTruthy();
        expect(screen.getByText('Downloading the workspace image.')).toBeTruthy();
    });
});
