// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SavedLogins } from './saved-logins';

const entry = (over: Record<string, unknown> = {}) => ({
    origin: 'https://app.example.com',
    action: 'inject',
    outcome: 'ok',
    detail: null,
    conversation_id: null,
    created_at: new Date().toISOString(),
    ...over,
});

const history = vi.hoisted(() => ({ items: [] as Record<string, unknown>[] }));

vi.mock('@/lib/hooks/use-web-logins', () => ({
    useWebLogins: () => ({ data: { items: [] }, isPending: false, error: null }),
    useWebLoginHistory: () => ({ data: { items: history.items } }),
    useRemoveWebLogin: () => ({ mutate: vi.fn(), isPending: false }),
}));

afterEach(cleanup);

describe('the activity list', () => {
    it('says why a saved login stopped working, not just that it did', async () => {
        // The whole reason somebody opens this list. `detail` was written and
        // returned by the API for the life of this feature and rendered
        // nowhere, so the line read "inject app.example.com · failed" and left
        // the person with no idea whether to sign in again or wait.
        history.items = [
            entry({ outcome: 'failed', detail: 'session rejected' }),
        ];
        render(<SavedLogins />);
        await userEvent.click(screen.getByRole('button', { name: 'Show activity' }));

        expect(screen.getByText('session rejected')).toBeTruthy();
        expect(screen.getByText(/inject app\.example\.com · failed/)).toBeTruthy();
    });

    it('stays quiet about an ordinary success', async () => {
        history.items = [entry()];
        render(<SavedLogins />);
        await userEvent.click(screen.getByRole('button', { name: 'Show activity' }));

        expect(screen.getByText(/inject app\.example\.com/).textContent).not.toContain('·');
    });
});
