// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { ComputerPanel } from './computer-panel';

// The two panes are stood in for: one starts a real browser over a WebSocket
// and the other fetches a workspace listing, and neither is what this file is
// about. What is under test is which of them the panel decides to show.
vi.mock('./browser-pane', () => ({
    BrowserPane: ({ conversationId }: { conversationId?: string }) => (
        <div data-testid="browser-pane">watching {conversationId}</div>
    ),
}));
vi.mock('./workspace-files-pane', () => ({
    WorkspaceFilesPane: () => <div data-testid="files-pane" />,
}));
vi.mock('./sign-in-embed', () => ({
    SignInEmbed: ({ toolCallId }: { toolCallId: string }) => (
        <div data-testid="sign-in-embed">{toolCallId}</div>
    ),
}));

afterEach(cleanup);

describe('the computer panel', () => {
    it('opens on files, so rendering it does not start a browser', () => {
        render(<ComputerPanel conversationId="conv-1" />);

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
            <ComputerPanel conversationId="conv-1" signInToolCallId={null} />,
        );
        expect(screen.getByTestId('files-pane')).toBeTruthy();

        rerender(<ComputerPanel conversationId="conv-1" signInToolCallId="call_abc123" />);

        expect(screen.getByTestId('sign-in-embed').textContent).toBe('call_abc123');
        expect(screen.queryByTestId('files-pane')).toBeNull();
    });

    it('watches the conversation when no sign-in is named', () => {
        render(<ComputerPanel conversationId="conv-1" />);

        fireEvent.click(screen.getByRole('button', { name: 'Browser' }));

        expect(screen.getByTestId('browser-pane').textContent).toBe('watching conv-1');
        expect(screen.queryByTestId('sign-in-embed')).toBeNull();
    });
});
