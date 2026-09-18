'use client';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { BrowserPane } from '@/components/workspace/browser-pane';
import { Button } from '@/components/ui/button';
import { WorkspaceFilesPane } from '@/components/workspace/workspace-files-pane';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { cn } from '@/lib/utils';

type Tab = 'files' | 'browser';

/**
 * The agent's computer, in one panel.
 *
 * Two tabs, and the browser one is deliberately not the default: attaching
 * starts a browser if none is running, and a panel that did that on render
 * would hold a few hundred megabytes open for as long as it was on screen.
 */
export function ComputerPanel({
    workspaceCwd,
    conversationId,
    signInToolCallId,
}: {
    workspaceCwd?: string;
    conversationId?: string;
    /** A paused `browser_sign_in` to put in front of the person, named by the
     *  URL. When set, the browser tab shows that sign-in — steered at the site
     *  and answerable — rather than a plain watch of this conversation. */
    signInToolCallId?: string | null;
}) {
    // Derived, not stored-and-synced. This panel is one long-lived instance:
    // it is usually already mounted, and often sitting on Files, when somebody
    // clicks "Sign in to ..." — so lazy initial state would miss every click
    // after the first, and an effect that called `setTab` would be a state
    // write during render's shadow (and is what `react-hooks/set-state-in-effect`
    // exists to stop). Instead the sign-in decides the tab, and a person's own
    // click overrides it only for as long as that same sign-in is on screen:
    // when a *different* pause arrives the override stops matching and the
    // browser tab comes back.
    const signInKey = signInToolCallId ?? null;
    const [override, setOverride] = useState<{ tab: Tab; forSignIn: string | null } | null>(
        null,
    );
    const tab: Tab =
        override && override.forSignIn === signInKey
            ? override.tab
            : signInKey
              ? 'browser'
              : 'files';

    // A sign-in names a site, and the browser has to be pointed at it. The id
    // travels in the URL rather than the origin (an origin there survives
    // reload and would re-steer a shared browser at a site the person has
    // finished with), so it is resolved from the pause here — the same call
    // the standalone page makes.
    const signInRequest = useQuery({
        queryKey: ['pending-sign-in', conversationId, signInToolCallId],
        queryFn: () =>
            getLemmaClient().webLogins.pendingSignIn(conversationId!, signInToolCallId!),
        enabled: tab === 'browser' && !!signInToolCallId && !!conversationId,
        retry: false,
    });
    const steerTo = signInRequest.data?.origin;

    return (
        <div className="flex h-full min-h-0 flex-col gap-2">
            <div className="flex items-center gap-1 px-1">
                {(['files', 'browser'] as const).map((name) => (
                    <Button
                        key={name}
                        variant="quiet"
                        size="xs"
                        onClick={() => setOverride({ tab: name, forSignIn: signInKey })}
                        aria-pressed={tab === name}
                        className={cn(
                            tab === name
                                ? 'text-[var(--text-primary)]'
                                : 'text-[var(--text-tertiary)]',
                        )}
                    >
                        {name === 'files' ? 'Files' : 'Browser'}
                    </Button>
                ))}
            </div>

            <div className="min-h-0 flex-1">
                {tab === 'files' ? (
                    <WorkspaceFilesPane workspaceCwd={workspaceCwd} />
                ) : (
                    // VNC shows this person's whole sandbox display, shared by
                    // every conversation's agent -- but *whether a browser is
                    // even running* is checked per session, and this
                    // conversation's own agent commands run in a session named
                    // for it (`run_browser_script`), not the shared default.
                    // Without this the pane checked the wrong session and
                    // refused forever with "no browser running" while the
                    // agent's browser was live the whole time.
                    //
                    // `origin` steers it at the site a sign-in named; the
                    // answering happens on the card in the conversation, so
                    // nothing but the browser is drawn here.
                    <BrowserPane conversationId={conversationId} origin={steerTo} />
                )}
            </div>
        </div>
    );
}
