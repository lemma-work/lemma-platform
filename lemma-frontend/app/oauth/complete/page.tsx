'use client';

import { useEffect, useState } from 'react';

/**
 * Where a connector round trip lands when it was started in a popup.
 *
 * The point of this page is that you never see it. Connecting an app used to
 * navigate the whole tab away to the provider, so somebody who was halfway
 * through something in Lemma lost it for the sake of authorising an app. A
 * popup keeps the page they were on alive — but a popup can only report back
 * if it can reach its opener, so this hands the outcome over and closes.
 *
 * The opener is trusted only to the extent of its origin: this posts to
 * `window.location.origin` rather than `'*'`, so the message cannot be read by
 * a window on another origin that happens to be listening.
 */
/**
 * A path inside this app, resolved and origin-checked, or `/`.
 *
 * Returns the path (not the absolute URL) so the caller navigates relative to
 * the current origin and nothing can smuggle a host in.
 */
function sameOriginPath(value: string | null): string {
    if (!value) return '/';
    try {
        const candidate = new URL(value, window.location.origin);
        if (candidate.origin !== window.location.origin) return '/';
        return `${candidate.pathname}${candidate.search}`;
    } catch {
        return '/';
    }
}

export default function OAuthCompletePage() {
    const [stranded, setStranded] = useState(false);

    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const outcome = {
            source: 'lemma-connect',
            connect: params.get('connect'),
            connector: params.get('connector'),
            account: params.get('account'),
            code: params.get('code'),
            reason: params.get('reason'),
        };

        const opener = window.opener;
        if (opener && !opener.closed) {
            opener.postMessage(outcome, window.location.origin);
            window.close();
            // `window.close()` is refused for a window the script did not open,
            // which happens when a blocked popup turned into an ordinary tab.
            // Falling through to the redirect below is the recovery.
        }

        // No opener: the tab was blocked and this became a normal navigation.
        // The flow still has to end somewhere sensible, so go where the person
        // started.
        //
        // Checked here rather than trusted from the server, because this value
        // reaches `location.replace` and that is what makes it an open-redirect
        // sink. A prefix test is not enough: `/%5Cevil.example` decodes to
        // `/\evil.example`, which passes "starts with a single slash" and which
        // URL parsing then reads as a host. So the candidate is resolved and its
        // origin compared, which is the only check that cannot be spelled around.
        const destination = sameOriginPath(params.get('from'));
        const query = new URLSearchParams();
        for (const [key, value] of Object.entries(outcome)) {
            if (key !== 'source' && value) query.set(key, value);
        }
        const timer = window.setTimeout(() => {
            setStranded(true);
            window.location.replace(
                `${destination}${destination.includes('?') ? '&' : '?'}${query.toString()}`,
            );
        }, 150);
        return () => window.clearTimeout(timer);
    }, []);

    return (
        <div className="flex min-h-screen items-center justify-center text-sm text-[var(--text-tertiary)]">
            {stranded ? 'Taking you back…' : 'Finishing up…'}
        </div>
    );
}
