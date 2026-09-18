// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { isSignInToolName, isUserInteractionToolName } from 'lemma-sdk';
import { SignInCard } from './assistant-approval-cards';

afterEach(cleanup);

const paused = {
    toolCallId: 'call_abc123',
    toolName: 'browser_sign_in',
    args: { origin: 'https://asur.work', reason: 'reading your pods' },
    state: 'call' as const,
};

describe('a waiting sign-in', () => {
    it('is treated as an interaction, not as ordinary tool activity', () => {
        // The whole bug, in one assertion. The backend has always listed
        // `browser_sign_in` in USER_PAUSING_TOOL_NAMES beside `ask_user` and
        // `request_approval`; the client did not, so a web conversation went to
        // WAITING and the transcript rendered the call as a tool log line. The
        // person was never asked anything and the run never resumed.
        expect(isSignInToolName('browser_sign_in')).toBe(true);
        expect(isUserInteractionToolName('browser_sign_in')).toBe(true);
    });

    it('opens the computer panel in place rather than leaving the conversation', () => {
        // The bug this pins: the card was a bare `<a href>`, so answering a
        // sign-in threw the whole page away and replaced the conversation the
        // person was reading with a standalone route. The panel is already
        // beside them; the card now asks for it.
        const navigations: Array<[string, string, Record<string, unknown> | undefined]> = [];
        render(
            <SignInCard
                invocation={paused}
                conversationId="conv-1"
                onNavigateResource={(type, id, meta) => navigations.push([type, id, meta])}
            />,
        );

        screen.getByRole('button', { name: /Open asur\.work/ }).click();

        expect(navigations).toEqual([
            ['sign_in', 'call_abc123', { conversationId: 'conv-1' }],
        ]);
        // No link at all: a click that also navigated would take the page away
        // a moment after opening the panel.
        expect(screen.queryByRole('link')).toBeNull();
    });

    it('falls back to the standalone page where there is no panel to open', () => {
        render(<SignInCard invocation={paused} conversationId="conv-1" />);

        const link = screen.getByRole('link', { name: /Open asur\.work/ });
        // The same destination the Slack and Telegram links use, so somebody
        // outside the app shell still reaches a page that can resolve it.
        expect(link.getAttribute('href')).toBe('/sign-in-to-site/conv-1/call_abc123');
        expect(screen.getByText('reading your pods')).toBeTruthy();
    });

    it('is answered from the card, the way a question is', () => {
        // The bug this pins: answering used to happen on the panel, through
        // an endpoint of its own. The server resolved the pause and the run
        // carried on, but this screen never heard about it -- the card sat
        // there and the composer stayed locked on "Sign in to continue" over
        // a conversation that had already moved on. Answering through the
        // approval path is what the transcript actually watches.
        const calls: Array<[string, string]> = [];
        render(
            <SignInCard
                invocation={paused}
                conversationId="conv-1"
                onResolveUserApproval={async (id, decision) => {
                    calls.push([id, decision]);
                }}
            />,
        );

        screen.getByRole('button', { name: /I’m signed in/ }).click();
        expect(calls).toEqual([['call_abc123', 'APPROVE_ONCE']]);
    });

    it('can say the sign-in did not happen', () => {
        const calls: Array<[string, string]> = [];
        render(
            <SignInCard
                invocation={paused}
                conversationId="conv-1"
                onResolveUserApproval={async (id, decision) => {
                    calls.push([id, decision]);
                }}
            />,
        );

        screen.getByRole('button', { name: /Can’t right now/ }).click();
        expect(calls).toEqual([['call_abc123', 'DENY']]);
    });

    it('says so rather than offering a link it cannot build', () => {
        render(<SignInCard invocation={paused} conversationId={null} />);

        expect(screen.queryByRole('link')).toBeNull();
        expect(screen.getByText(/cannot be opened from here/)).toBeTruthy();
    });

    it('reports the outcome once it has been answered', () => {
        // The shape `_browser_sign_in_return` actually persists: `outcome` is a
        // string, and there is no `signed_in` key and no `decision` key. Read
        // wrongly, every completed sign-in renders as "skipped".
        render(
            <SignInCard
                invocation={{
                    ...paused,
                    state: 'result',
                    result: { success: true, outcome: 'signed_in', origin: 'https://asur.work', saved: true },
                }}
                conversationId="conv-1"
            />,
        );

        expect(screen.getByText('Signed in to asur.work')).toBeTruthy();
        expect(screen.getByText('kept for next time')).toBeTruthy();
        expect(screen.queryByRole('link')).toBeNull();
    });

    it('says when the person declined rather than calling it done', () => {
        render(
            <SignInCard
                invocation={{
                    ...paused,
                    state: 'result',
                    result: { success: true, outcome: 'declined', origin: 'https://asur.work' },
                }}
                conversationId="conv-1"
            />,
        );

        expect(screen.getByText('Not signed in to asur.work')).toBeTruthy();
        expect(screen.getByText('skipped')).toBeTruthy();
    });
});
