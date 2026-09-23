'use client';

import { useQuery } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { createPortal } from 'react-dom';

import { BrowserPane } from '@/components/workspace/browser-pane';
import { Button } from '@/components/ui/button';
import { AppWindow } from '@/components/ui/icons';
import { usePictureInPicture } from '@/lib/hooks/use-picture-in-picture';
import { WorkspaceFilesPane } from '@/components/workspace/workspace-files-pane';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { cn } from '@/lib/utils';

export type ComputerTab = 'files' | 'browser';

/**
 * Which half of the computer is showing, and who decided.
 *
 * Derived, not stored-and-synced. The panel is one long-lived instance: it is
 * usually already mounted, and often sitting on Files, when somebody clicks
 * "Sign in to ..." — so lazy initial state would miss every click after the
 * first, and an effect that called `setTab` would be a state write during
 * render's shadow (and is what `react-hooks/set-state-in-effect` exists to
 * stop). Instead the sign-in decides the tab, and a person's own click
 * overrides it only for as long as that same sign-in is on screen: when a
 * *different* pause arrives the override stops matching and the browser tab
 * comes back.
 *
 * A hook rather than state inside the panel because the answer is wanted in
 * two places. The stage around the panel offers a link to the full-size page,
 * and that link has to open on the half the person is actually looking at —
 * it used to always open on Files, so switching to the browser and then going
 * full-screen put you back where you started.
 */
export function useComputerTab(
    signInKey: string | null,
): [ComputerTab, (tab: ComputerTab) => void] {
    const [override, setOverride] = useState<{
        tab: ComputerTab;
        forSignIn: string | null;
    } | null>(null);
    const tab: ComputerTab =
        override && override.forSignIn === signInKey
            ? override.tab
            : signInKey
              ? 'browser'
              : 'files';
    const choose = useCallback(
        (next: ComputerTab) => setOverride({ tab: next, forSignIn: signInKey }),
        [signInKey],
    );
    return [tab, choose];
}

/**
 * The full-size page, opened where the pane already is.
 *
 * Everything a reader would expect to survive the jump: the conversation's
 * own directory, the half that is showing, and which conversation's browser
 * to attach to. It used to carry only the directory, so switching to the
 * browser and then going full-screen put you back on Files -- and the page
 * attached to no conversation at all.
 */
export function computerHref(
    podId: string,
    {
        workspaceCwd,
        tab,
        conversationId,
    }: { workspaceCwd?: string; tab: ComputerTab; conversationId?: string },
): string {
    const params = new URLSearchParams();
    if (workspaceCwd) params.set('path', workspaceCwd);
    // Only when it is not the default, so the ordinary link stays readable.
    if (tab === 'browser') params.set('view', 'browser');
    if (conversationId) params.set('conversation', conversationId);
    const query = params.toString();
    return `/pod/${podId}/computer${query ? `?${query}` : ''}`;
}

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
    tab,
    onTabChange,
}: {
    workspaceCwd?: string;
    conversationId?: string;
    /** A paused `browser_sign_in` to put in front of the person, named by the
     *  URL. When set, the browser tab shows that sign-in — steered at the site
     *  and answerable — rather than a plain watch of this conversation. */
    signInToolCallId?: string | null;
    /** From `useComputerTab`, held by whatever also needs to link to this. */
    tab: ComputerTab;
    onTabChange: (tab: ComputerTab) => void;
}) {
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
    const pip = usePictureInPicture();

    return (
        <div className="flex h-full min-h-0 flex-col gap-2">
            <div className="flex items-center gap-1 px-1">
                {(['files', 'browser'] as const).map((name) => (
                    <Button
                        key={name}
                        variant="quiet"
                        size="xs"
                        onClick={() => onTabChange(name)}
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
                {tab === 'browser' && pip.supported ? (
                    <Button
                        variant="quiet"
                        size="xs"
                        className="ml-auto text-[var(--text-tertiary)]"
                        onClick={() => (pip.pipWindow ? pip.close() : void pip.open())}
                    >
                        <AppWindow className="size-3.5" />
                        {pip.pipWindow ? 'Bring it back' : 'Pop out'}
                    </Button>
                ) : null}
            </div>

            <div className="min-h-0 flex-1">
                {tab === 'files' ? (
                    <WorkspaceFilesPane workspaceCwd={workspaceCwd} />
                ) : pip.pipWindow ? (
                    // The picture is in the floating window; saying so beats
                    // a second live connection to the same display drawn
                    // behind it, which costs a socket and an encode for a
                    // view nobody is looking at.
                    <div className="flex h-full items-center justify-center p-8 text-center text-sm text-[var(--text-tertiary)]">
                        The browser is in its own window. Close that window, or
                        press “Bring it back”, to watch it here again.
                    </div>
                ) : (
                    // VNC shows this person's whole sandbox display. There is
                    // one browser per sandbox now, so nothing here picks
                    // between sessions: `origin` only steers it at the site a
                    // sign-in named, and the answering happens on the card in
                    // the conversation.
                    <BrowserPane conversationId={conversationId} origin={steerTo} />
                )}
            </div>

            {/* A second viewer of the same display, in a window that floats
                above everything. `autoResize` off: one display serves the
                sandbox, so two viewers of different shapes both asking for a
                fit would fight and each would keep seeing the other's size.
                This one takes whatever shape the display already has and lets
                noVNC scale it. */}
            {pip.pipWindow
                ? createPortal(
                      <div className="h-full w-full bg-[var(--bg-canvas)]">
                          <BrowserPane
                              conversationId={conversationId}
                              origin={steerTo}
                              autoResize={false}
                          />
                      </div>,
                      pip.pipWindow.document.body,
                  )
                : null}
        </div>
    );
}
