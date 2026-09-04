'use client';

import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { BrowserCanvas } from '@/components/workspace/browser-canvas';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/**
 * What the agent's browser is looking at.
 *
 * Three states, not two, and the distinction is the point: **asleep is not
 * unreachable**. A workspace releases its compute after fifteen minutes idle,
 * which is the ordinary resting state of a machine nobody is using — showing
 * "can't reach it" for that teaches people to ignore the one message that
 * should mean something, and to press Retry at a thing that is working
 * correctly.
 */
export function WorkspaceScreenPane() {
    const [wake, setWake] = useState(false);

    // Whether the workspace is up is answered by the file listing, which does
    // not start one. Asking that first is what keeps opening this tab from
    // being the thing that boots a sandbox.
    const state = useQuery({
        queryKey: ['workspace-screen', wake],
        queryFn: async () => {
            const listing = await getLemmaClient().workspace.listFiles({ wake });
            return { sleeping: listing.sleeping };
        },
        // The signed view URL expires; re-minting well inside that window keeps
        // a long look from going blank.
        retry: false,
    });

    // Half the daemon's two-minute idle timeout, so one missed beat does not
    // take the view down. Without this the picture simply vanishes after about
    // two minutes of watching, which reads as a crash rather than a timeout.
    const live = Boolean(state.data && !state.data.sleeping);
    useEffect(() => {
        if (!live) return;
        const client = getLemmaClient();
        const timer = setInterval(() => {
            void client.workspace.heartbeatBrowser().catch(() => {
                // A missed beat is not worth interrupting somebody watching.
                // The next one lands, or the frame visibly stops.
            });
        }, 55_000);
        return () => clearInterval(timer);
    }, [live]);

    const start = useCallback(() => setWake(true), []);

    if (state.isPending) {
        return (
            <p className="p-4 text-sm text-[var(--text-tertiary)]">
                Looking at this computer…
            </p>
        );
    }

    if (state.error) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <p className="text-sm text-[var(--text-secondary)]">
                    This computer is not reachable right now.
                </p>
                <Button variant="secondary" size="sm" onClick={() => void state.refetch()}>
                    Try again
                </Button>
            </div>
        );
    }

    if (state.data?.sleeping) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <p className="text-sm text-[var(--text-secondary)]">
                    This computer is asleep.
                </p>
                <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                    Nothing is wrong — it powers down when no one is using it, and its
                    files are still there.
                </p>
                <Button variant="secondary" size="sm" onClick={start}>
                    Wake it and watch
                </Button>
            </div>
        );
    }

    return <BrowserCanvas />;
}
