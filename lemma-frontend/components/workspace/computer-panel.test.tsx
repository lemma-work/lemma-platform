// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { ComputerPanel } from './computer-panel';

// The panel resolves a sign-in's origin through the SDK, so it needs a client.
// Retries off: a test that fails a query should say so at once.
const withQuery = (ui: ReactElement) => (
    <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
        {ui}
    </QueryClientProvider>
);

vi.mock('@/lib/sdk/lemma-client', () => ({
    getLemmaClient: () => ({
        webLogins: {
            pendingSignIn: async () => ({
                tool_call_id: 'call_abc123',
                origin: 'https://example.com',
                reason: 'because',
            }),
        },
    }),
}));

// The two panes are stood in for: one starts a real browser over a WebSocket
// and the other fetches a workspace listing, and neither is what this file is
// about. What is under test is which of them the panel decides to show.
vi.mock('./browser-pane', () => ({
    BrowserPane: ({
        conversationId,
        origin,
        autoResize,
    }: {
        conversationId?: string;
        origin?: string;
        autoResize?: boolean;
    }) => (
        <div data-testid="browser-pane" data-auto-resize={String(autoResize ?? true)}>
            {origin ? `steered ${origin}` : `watching ${conversationId}`}
        </div>
    ),
}));
vi.mock('./workspace-files-pane', () => ({
    WorkspaceFilesPane: () => <div data-testid="files-pane" />,
}));
afterEach(cleanup);

describe('the computer panel', () => {
    it('opens on files, so rendering it does not start a browser', () => {
        render(withQuery(<ComputerPanel conversationId="conv-1" />));

        expect(screen.getByTestId('files-pane')).toBeTruthy();
        expect(screen.queryByTestId('browser-pane')).toBeNull();
    });

    it('switches an already-open panel to the sign-in when one arrives', () => {
        // The case lazy initial state would miss, and the reason the tab is
        // driven by an effect. This panel is one long-lived instance reused
        // across whichever conversation is open: when somebody clicks "Sign in
        // to ..." it is usually already mounted, and usually sitting on Files.
        // `useState(initialTab)` runs once and would leave them staring at
        // their file tree.
        const { rerender } = render(
            withQuery(<ComputerPanel conversationId="conv-1" signInToolCallId={null} />),
        );
        expect(screen.getByTestId('files-pane')).toBeTruthy();

        rerender(withQuery(<ComputerPanel conversationId="conv-1" signInToolCallId="call_abc123" />));

        expect(screen.getByTestId('browser-pane')).toBeTruthy();
        expect(screen.queryByTestId('files-pane')).toBeNull();
    });

    it('watches the conversation when no sign-in is named', () => {
        render(withQuery(<ComputerPanel conversationId="conv-1" />));

        fireEvent.click(screen.getByRole('button', { name: 'Browser' }));

        expect(screen.getByTestId('browser-pane').textContent).toBe('watching conv-1');
        expect(screen.queryByTestId('sign-in-embed')).toBeNull();
    });
});

describe('popping the browser out into its own window', () => {
    /**
     * A fake Document Picture-in-Picture. jsdom has none, and what is worth
     * testing is not the API -- it is what the panel does with the window
     * once it has one.
     */
    const fakePip = () => {
        const doc = document.implementation.createHTMLDocument('pip');
        const listeners: Record<string, (() => void)[]> = {};
        const win = {
            document: doc,
            addEventListener: (name: string, fn: () => void) => {
                (listeners[name] ??= []).push(fn);
            },
            close: () => {
                for (const fn of listeners.pagehide ?? []) fn();
            },
        };
        (window as unknown as Record<string, unknown>).documentPictureInPicture = {
            requestWindow: async () => win,
        };
        return { win, doc };
    };

    afterEach(() => {
        delete (window as unknown as Record<string, unknown>).documentPictureInPicture;
    });

    const openBrowserTab = async () => {
        render(withQuery(<ComputerPanel conversationId="conv-1" />));
        fireEvent.click(screen.getByRole('button', { name: 'Browser' }));
        // `supported` is read in an effect, so the control appears a tick later.
        return screen.findByRole('button', { name: /Pop out/ });
    };

    it('offers nothing where the browser has no such API', () => {
        render(withQuery(<ComputerPanel conversationId="conv-1" />));
        fireEvent.click(screen.getByRole('button', { name: 'Browser' }));

        expect(screen.queryByRole('button', { name: /Pop out/ })).toBeNull();
    });

    it('moves the picture into the window rather than drawing it twice', async () => {
        const { doc } = fakePip();
        const button = await openBrowserTab();

        fireEvent.click(button);
        await screen.findByText(/in its own window/);

        // Exactly one live connection: a second one behind the floating
        // window costs a socket and an encode for a view nobody is looking at.
        expect(screen.queryByTestId('browser-pane')).toBeNull();
        expect(doc.body.querySelectorAll('[data-testid="browser-pane"]')).toHaveLength(1);
    });

    it('does not let the floating window reshape the display', async () => {
        // One display serves the sandbox. Two viewers of different shapes
        // both asking for a fit would fight, last writer wins, and each
        // would keep seeing the other's size.
        const { doc } = fakePip();
        fireEvent.click(await openBrowserTab());
        await screen.findByText(/in its own window/);

        const popped = doc.body.querySelector('[data-testid="browser-pane"]');
        expect(popped?.getAttribute('data-auto-resize')).toBe('false');
    });

    it('comes back when the window is closed', async () => {
        const { win } = fakePip();
        fireEvent.click(await openBrowserTab());
        await screen.findByText(/in its own window/);

        // The person closing the window, rather than pressing the control.
        win.close();

        expect(await screen.findByTestId('browser-pane')).toBeTruthy();
    });
});
