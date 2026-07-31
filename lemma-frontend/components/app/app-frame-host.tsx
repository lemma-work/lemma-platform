'use client';

import { useState } from 'react';
import { PanelsTopLeft } from '@/components/ui/icons';
import { useApp } from '@/components/app/app-context';
import { AppFrame } from '@/components/app/app-launch';
import { StepLoader } from '@/components/brand/loader';
import { RecoveryState } from '@/components/shared/empty-state';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { cn } from '@/lib/utils';

// Keep at most this many app iframes alive at once; opening a further app evicts
// the oldest (FIFO). The active app is always the newest insertion, so it is
// never the one evicted.
const MAX_LIVE_APPS = 4;

// Keep-alive host for the pod's app iframes. It is mounted once in the pod shell
// — above the router — so it survives route changes. It lazily mounts an iframe
// the first time each app tab is opened, then keeps it mounted, hiding the
// inactive ones with `display:none` rather than unmounting them. A hidden iframe
// keeps running (its state and live connections persist), so switching between
// app tabs is instant with no cold reboot. Only the app matching the current
// `/app/view?page=slug` route is shown.
export function AppFrameHost({
    podId,
    visible,
    activeSlug,
    openAppSlugs,
    canUpdateApp,
}: {
    podId: string;
    visible: boolean;
    activeSlug: string | null;
    openAppSlugs: string[];
    canUpdateApp: boolean;
}) {
    const { pages } = useApp();
    const [activated, setActivated] = useState<string[]>([]);

    // Mark a slug live the first time its tab is opened and keep it mounted
    // (hidden) until it's evicted by the FIFO cap. Adjusting state during render
    // (guarded so it runs once per new slug) derives this from the active-slug
    // prop without an effect + cascading re-render.
    if (activeSlug && !activated.includes(activeSlug)) {
        setActivated((prev) => {
            if (prev.includes(activeSlug)) return prev;
            const next = [...prev, activeSlug];
            return next.length > MAX_LIVE_APPS ? next.slice(next.length - MAX_LIVE_APPS) : next;
        });
    }

    // Pinned app tabs remain eligible for keep-alive after their first activation;
    // the FIFO cap still bounds how many hidden app frames can stay live.
    const livePages = pages.filter(
        (page) => openAppSlugs.includes(page.slug) && activated.includes(page.slug) && page.url,
    );
    const activePage = activeSlug ? pages.find((page) => page.slug === activeSlug) : null;
    const hasActiveFrame = livePages.some((page) => page.slug === activeSlug);

    const missingSlug = visible && !activeSlug;
    const unavailable = visible && !!activeSlug && pages.length > 0 && (!activePage || !activePage.url);
    const loading = visible && !!activeSlug && !hasActiveFrame && !unavailable;

    return (
        <div aria-hidden={!visible} className={cn('absolute inset-0 z-20', visible ? 'block' : 'hidden')}>
            {livePages.map((page) => (
                <div
                    key={page.slug}
                    className={cn('absolute inset-0', page.slug === activeSlug ? 'block' : 'hidden')}
                >
                    <AppFrame
                        podId={podId}
                        appId={page.id}
                        appName={page.title}
                        title={page.title}
                        url={page.url as string}
                        visibility={page.visibility}
                        canShare={resourceAllows(page, 'app.update', canUpdateApp)}
                    />
                </div>
            ))}

            {loading ? (
                <div className="absolute inset-0 flex items-center justify-center">
                    <StepLoader size="sm" />
                </div>
            ) : null}

            {missingSlug ? (
                <div className="absolute inset-0 flex items-center justify-center">
                    <RecoveryState
                        icon={<PanelsTopLeft className="h-5 w-5" />}
                        title="Missing app page"
                        description="Open an app from the Apps list so Lemma knows which workspace to show."
                    />
                </div>
            ) : null}

            {unavailable ? (
                <div className="absolute inset-0 flex items-center justify-center">
                    <RecoveryState
                        icon={<PanelsTopLeft className="h-5 w-5" />}
                        title="App unavailable"
                        description="This app didn't return a web app URL. Try opening it again from the Apps list."
                    />
                </div>
            ) : null}
        </div>
    );
}
