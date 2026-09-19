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
    signed_in: false,
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

describe('clearing a site', () => {
    it('asks first, then clears that site', async () => {
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

    it('promises only what it does', async () => {
        // Twice narrowed, now widened, and each move followed a measurement.
        // The first copy had to say "forgetting removes Lemma's copy, it does
        // not sign you out at the site", because that was true of it. The
        // second hedged about local storage -- but that was true of a bug,
        // not of the design: the relay sent bare hosts to
        // `clearDataForOrigin` and local storage is keyed by full origin.
        // With the origins the browser actually has open now included,
        // cookie and localStorage both go, so the hedge has to go with them.
        answer.data = { items: [site()], sleeping: false };
        render(<BrowserLoginsCard />);
        await open();
        await userEvent.click(
            screen.getByRole('button', { name: 'Sign out of app.example.com' }),
        );

        const note = screen.getByText(/stored data from the agent/i);
        expect(note.textContent).toContain('signs it out');
        expect(note.textContent).not.toContain('may stay signed in');
        expect(note.textContent).not.toContain('does not sign you out at');
        // The one promise that has never changed.
        expect(note.textContent).toContain('signed in yourself');
    });
});


describe('separating a login from a cookie', () => {
    /**
     * The reason `signed_in` exists at all, and it was measured rather than
     * assumed. On a real profile `api.lemma.work` held two HttpOnly session
     * cookies belonging to somebody signed in, and `youtube.com` held six
     * HttpOnly cookies belonging to nobody -- identical on every flag CDP
     * reports. So the split comes from what a person answered to a sign-in
     * request, not from anything read off the cookies.
     */
    it('puts the sites somebody signed in to above the rest', async () => {
        answer.data = {
            items: [
                site({ site: 'doubleclick.net' }),
                site({ site: 'lemma.work', signed_in: true }),
                site({ site: 'youtube.com' }),
            ],
            sleeping: false,
        };
        render(<BrowserLoginsCard />);
        await open();

        expect(screen.getByText('Signed in')).toBeTruthy();
        expect(screen.getByText('Other sites with cookies')).toBeTruthy();

        const rows = screen.getAllByRole('listitem').map((li) => li.textContent);
        expect(rows[0]).toContain('lemma.work');
        // Still listed, and still signable-out-of: an ad network's cookie is
        // worth being able to clear, just not worth reading first.
        expect(rows.join(' ')).toContain('doubleclick.net');
    });

    it('does not split when nothing has been marked', async () => {
        // Every profile that predates the marks file starts with none. A
        // dialog that hid all four sites behind a collapsed "other" because
        // nobody had answered a sign-in yet would be worse than a flat list.
        answer.data = {
            items: [site({ site: 'lemma.work' }), site({ site: 'youtube.com' })],
            sleeping: false,
        };
        render(<BrowserLoginsCard />);
        await open();

        expect(screen.queryByText('Signed in')).toBeNull();
        expect(screen.queryByText('Other sites with cookies')).toBeNull();
        expect(screen.getAllByRole('listitem')).toHaveLength(2);
    });

    it('does not split when every site was signed in to', async () => {
        answer.data = {
            items: [
                site({ site: 'lemma.work', signed_in: true }),
                site({ site: 'other.test', signed_in: true }),
            ],
            sleeping: false,
        };
        render(<BrowserLoginsCard />);
        await open();

        expect(screen.queryByText('Other sites with cookies')).toBeNull();
        expect(screen.getAllByRole('listitem')).toHaveLength(2);
    });
});
