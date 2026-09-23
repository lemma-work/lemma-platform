// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { AppFrame } from './app-launch';

const APP_URL = 'https://notes--r3.apps.lemma.localhost/';

/**
 * The frame is the only part of this that can fail invisibly.
 *
 * On macOS the desktop app serves the workspace from `*.localhost` and an app
 * from its own `*.localhost` host, which WebKit treats as cross-site: the frame
 * gets no cookies, the app loads permanently signed out, and its SDK refreshes
 * for ever trying to fix it. Nothing errors. The user sees a spinner that never
 * resolves, and there is no console message that says why.
 *
 * So the interesting assertion is a *negative* one — that no iframe is rendered
 * at all — and it depends on globals that only exist in a browser. Hence the
 * environment above; the rest of this suite is node-only on purpose.
 */
function renderFrame() {
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    return render(
        <QueryClientProvider client={client}>
            <AppFrame podId="pod-1" appId="app-1" appName="Notes" title="Notes" url={APP_URL} />
        </QueryClientProvider>,
    );
}

/** jsdom's own location, put back after a test replaces it. */
const REAL_LOCATION = Object.getOwnPropertyDescriptor(window, 'location')!;

/** Serve the workspace from the host the desktop app serves it from. */
function onLocalhostSubdomain() {
    Object.defineProperty(window, 'location', {
        configurable: true,
        value: { ...window.location, hostname: 'lemma.localhost', href: 'https://lemma.localhost/' },
    });
}

function inDesktopApp(platform?: string) {
    (window as unknown as Record<string, unknown>).__LEMMA_DESKTOP__ = platform
        ? { platform }
        : {};
}

afterEach(() => {
    cleanup();
    delete (window as unknown as Record<string, unknown>).__LEMMA_DESKTOP__;
    Object.defineProperty(window, 'location', REAL_LOCATION);
});

describe('AppFrame', () => {
    it('renders the app in a frame in an ordinary browser', () => {
        renderFrame();

        const frame = document.querySelector('iframe');
        expect(frame).not.toBeNull();
        expect(frame?.getAttribute('src')).toBe(APP_URL);
    });

    /**
     * The sandbox is the app's containment, and every token in it was chosen.
     * `allow-same-origin` is what gives the app its own storage; dropping
     * `allow-top-navigation-by-user-activation` for the unqualified form would
     * let an app navigate the workspace out from under the user.
     */
    it('contains the app with the sandbox it was given', () => {
        renderFrame();
        const sandbox = document.querySelector('iframe')?.getAttribute('sandbox') ?? '';

        for (const token of [
            'allow-same-origin',
            'allow-scripts',
            'allow-forms',
            'allow-popups',
            'allow-downloads',
            'allow-modals',
            'allow-top-navigation-by-user-activation',
        ]) {
            expect(sandbox.split(' ')).toContain(token);
        }
        expect(sandbox.split(' ')).not.toContain('allow-top-navigation');
        expect(document.querySelector('iframe')?.getAttribute('referrerPolicy'))
            .toBe('strict-origin-when-cross-origin');
    });

    /**
     * The failure this component exists to avoid. No frame at all is the fix:
     * the shell routes an app URL opened in a new window to its own window,
     * where the page is first-party and the session works.
     */
    it('offers a window instead of a frame where the frame would have no session', () => {
        onLocalhostSubdomain();
        inDesktopApp('macos');

        renderFrame();

        expect(document.querySelector('iframe')).toBeNull();
        expect(screen.getByText('This app opens in its own window.')).toBeTruthy();
        const link = screen.getByRole('link', { name: /Open app/ });
        // The marker is what tells the shell this is an app being opened rather
        // than an arbitrary link, so a bare href would open it in a browser.
        expect(link.getAttribute('href')).toContain('#');
        expect(link.getAttribute('href')?.startsWith(APP_URL)).toBe(true);
        expect(link.getAttribute('target')).toBe('_blank');
        expect(link.getAttribute('rel')).toBe('noreferrer');
    });

    /**
     * Windows keeps its frame: WebView2 treats `*.localhost` as same-site, so
     * the cookie reaches the app and there is nothing to work around. Costing
     * Windows users a window for a WebKit problem would be a real regression.
     */
    it('keeps the frame in the desktop app on Windows', () => {
        onLocalhostSubdomain();
        inDesktopApp('windows');

        renderFrame();

        expect(document.querySelector('iframe')).not.toBeNull();
    });

    /**
     * A shell too old to say which platform it is. locald serves a frontend
     * pack that updates independently of the shell, so a newer pack can run
     * inside a shell that never injected `platform`. Assuming the permissive
     * case brings back the signed-out frame that retries for ever; assuming the
     * restrictive one costs a window.
     */
    it('assumes the restrictive case when the shell does not say what it is', () => {
        onLocalhostSubdomain();
        inDesktopApp();

        renderFrame();

        expect(document.querySelector('iframe')).toBeNull();
    });
});
