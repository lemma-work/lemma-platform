// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserLoginsCard } from './browser-logins-card';

/**
 * A card in "Add your own", and a dialog behind it.
 *
 * It arrived as a bare heading and a flat list stapled under a grid of eighty
 * app cards, which read as a footnote to the page rather than a part of it.
 * The list itself is the same idea it always was -- and what it can honestly
 * say changed when the store went away. It used to show rows Lemma kept, with
 * an activity log of what had been done to them, and it had to disclaim its
 * own button: forgetting removed Lemma's copy and left the browser signed in.
 * The browser keeps its own profile now, so the list is a read of that
 * browser and signing out signs it out.
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
    enabled: null as boolean | null,
}));
const removed = vi.hoisted(() => ({ calls: [] as string[] }));

vi.mock('@/lib/hooks/use-web-logins', () => ({
    useWebLogins: (wake: boolean, enabled: boolean) => {
        answer.wake = wake;
        answer.enabled = enabled;
        return { data: answer.data, isPending: false, error: null };
    },
    useRemoveWebLogin: () => ({
        mutate: (site: string) => removed.calls.push(site),
        isPending: false,
    }),
}));

afterEach(() => {
    answer.data = { items: [], sleeping: false };
    answer.wake = null;
    answer.enabled = null;
    removed.calls = [];
    cleanup();
});

const open = () =>
    userEvent.click(screen.getByRole('button', { name: /Browser logins/ }));

describe('the card', () => {
    it('asks the sandbox nothing until somebody opens it', () => {
        // It sits on a page of eighty connectors. A round trip into a sandbox
        // to render a door nobody has opened is a round trip spent on
        // nothing -- and it is why the card carries no count.
        render(<BrowserLoginsCard />);

        expect(answer.enabled).toBe(false);
    });

    it('opens the dialog, still without waking a sleeping computer', async () => {
        render(<BrowserLoginsCard />);
        await open();

        expect(screen.getByRole('dialog')).toBeTruthy();
        expect(answer.enabled).toBe(true);
        expect(answer.wake).toBe(false);
    });
});

describe('reading what the browser holds', () => {
    it('offers to start it rather than pretending there is nothing there', async () => {
        // "Not running" is "cannot say", not "nothing". The cookies are read
        // over CDP so the browser has to be up to answer, and the profile is
        // on disk whether it is or not -- reporting an empty list here would
        // tell somebody their logins were gone.
        answer.data = { items: [], sleeping: true };
        render(<BrowserLoginsCard />);
        await open();

        expect(screen.getByText(/browser is not running/)).toBeTruthy();
        expect(screen.getByText(/still there/)).toBeTruthy();
        await userEvent.click(screen.getByRole('button', { name: /Start it/ }));

        expect(answer.wake).toBe(true);
    });

    it('names each site and says roughly how long it lasts', async () => {
        const inTenDays = new Date(Date.now() + 10 * 86_400_000).toISOString();
        answer.data = {
            items: [site({ expires: inTenDays }), site({ site: 'other.test' })],
            sleeping: false,
        };
        render(<BrowserLoginsCard />);
        await open();

        expect(screen.getByText('app.example.com')).toBeTruthy();
        expect(screen.getByText('expires in 10 days')).toBeTruthy();
        // A session cookie has no expiry, and saying "never" would be a lie.
        expect(screen.getByText('until the browser restarts')).toBeTruthy();
    });
});

describe('signing out', () => {
    it('asks first, then signs the browser out of that site', async () => {
        answer.data = { items: [site()], sleeping: false };
        render(<BrowserLoginsCard />);
        await open();

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
        render(<BrowserLoginsCard />);
        await open();
        await userEvent.click(
            screen.getByRole('button', { name: 'Sign out of app.example.com' }),
        );

        const note = screen.getByText(/signs the agent/i);
        expect(note.textContent).toContain('drops its cookies');
        expect(note.textContent).not.toContain('does not sign you out');
    });
});
