'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { capturePageview, startAnalytics } from '@/lib/analytics/client';

/**
 * Starts analytics once and records App Router navigations.
 *
 * Renders nothing and mounts inside the existing provider tree. On a
 * Desktop-local installation every call below is a no-op, decided in
 * `lib/analytics/client` by the same `isLocalDeployment()` the rest of the app
 * uses — there is no second notion of "is this local" to keep in sync.
 */
export function AnalyticsProvider() {
    const pathname = usePathname();

    useEffect(() => {
        // Fire and forget: posthog-js is loaded dynamically so an unconfigured
        // or Desktop-local build never downloads it, and nothing about rendering
        // may wait on that chunk.
        void startAnalytics();
    }, []);

    useEffect(() => {
        if (!pathname) return;
        capturePageview(pathname);
    }, [pathname]);

    return null;
}
