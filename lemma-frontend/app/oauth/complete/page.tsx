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

        // No opener: the popup was blocked and this became a normal navigation.
        // The flow still has to end somewhere sensible, so go where the person
        // started — `from` is set when the request is made, and is a rooted
        // path by construction (the server refuses anything else).
        const from = params.get('from');
        const destination = from && from.startsWith('/') && !from.startsWith('//') ? from : '/';
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
