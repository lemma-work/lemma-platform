// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SavedLogins } from './saved-logins';

/**
 * What this screen can honestly say changed when the store went away.
 *
 * It used to list rows Lemma kept and show an activity log of what had been
 * done to them, and it had to disclaim its own button: forgetting removed
 * Lemma's copy and left the browser signed in. The browser keeps its own
 * profile now, so the list is a read of that browser and signing out signs it
 * out. The activity log went with the table -- there is no server-side event
 * left to record.
 */

const site = (over: Record<string, unknown> = {}) => ({
    site: 'app.example.com',
    cookie_count: 3,
    expires: null,
    ...over,
});

const answer = vi.hoisted(() => ({
    data: { items: [] as Record<string, unknown>[], sleeping: false },
    wake: null as boolean | null,
}));
const removed = vi.hoisted(() => ({ calls: [] as string[] }));

vi.mock('@/lib/hooks/use-web-logins', () => ({
    useWebLogins: (wake: boolean) => {
        answer.wake = wake;
        return { data: answer.data, isPending: false, error: null };
    },
    useRemoveWebLogin: () => ({
        mutate: (origin: string) => removed.calls.push(origin),
        isPending: false,
    }),
}));

afterEach(() => {
    answer.data = { items: [], sleeping: false };
    answer.wake = null;
    removed.calls = [];
    cleanup();
});

describe('reading what the browser holds', () => {
    it('does not wake a sleeping computer to render itself', () => {
        // Unlike the table read this replaces, answering costs a round trip
        // into the sandbox. Opening a settings page is not a reason to start
        // somebody's machine.
        render(<SavedLogins />);

        expect(answer.wake).toBe(false);
    });

    it('offers to wake it rather than pretending there is nothing there', async () => {
        answer.data = { items: [], sleeping: true };
        render(<SavedLogins />);

        expect(screen.getByText(/Your computer is asleep/)).toBeTruthy();
        await userEvent.click(screen.getByRole('button', { name: /Wake it/ }));

        expect(answer.wake).toBe(true);
    });

    it('names each site and says roughly how long it lasts', () => {
        const inTenDays = new Date(Date.now() + 10 * 86_400_000).toISOString();
        answer.data = {
            items: [site({ expires: inTenDays }), site({ site: 'other.test' })],
            sleeping: false,
        };
        render(<SavedLogins />);

        expect(screen.getByText('app.example.com')).toBeTruthy();
        expect(screen.getByText('expires in 10 days')).toBeTruthy();
        // A session cookie has no expiry, and saying "never" would be a lie.
        expect(screen.getByText('until the browser restarts')).toBeTruthy();
    });
});

describe('signing out', () => {
    it('asks first, then signs the browser out of that site', async () => {
        answer.data = { items: [site()], sleeping: false };
        render(<SavedLogins />);

        await userEvent.click(
            screen.getByRole('button', { name: 'Sign out of app.example.com' }),
        );
        expect(removed.calls).toEqual([]);

        await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
        expect(removed.calls).toEqual(['app.example.com']);
    });

    it('no longer disclaims itself, because it no longer has to', async () => {
        // The old copy had to say "forgetting removes Lemma's copy, it does
        // not sign you out at the site", because that was true of it.
        answer.data = { items: [site()], sleeping: false };
        render(<SavedLogins />);
        await userEvent.click(
            screen.getByRole('button', { name: 'Sign out of app.example.com' }),
        );

        const note = screen.getByText(/signs the agent/i);
        expect(note.textContent).toContain('drops its cookies');
        expect(note.textContent).not.toContain('does not sign you out');
    });
});
