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
    BrowserPane: ({ conversationId, origin }: { conversationId?: string; origin?: string }) => (
        <div data-testid="browser-pane">{origin ? `steered ${origin}` : `watching ${conversationId}`}</div>
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
