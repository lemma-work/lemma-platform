'use client';

import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { getLemmaApiBaseUrl } from '@/lib/sdk/lemma-client';
import { attachBrowserViewer } from '@/lib/workspace/cdp-viewer';

import type { ViewerState } from '@/lib/workspace/cdp-viewer';

const streamUrl = (): string => {
    const api = getLemmaApiBaseUrl().replace(/^http/, 'ws').replace(/\/$/, '');
    return `${api}/workspace/apps/browser/stream`;
};

/**
 * The agent's browser, live and drivable.
 *
 * A canvas rather than an iframe because this is not a page we are allowed to
 * embed — it is a picture of one, streamed frame by frame, with clicks and
 * keystrokes sent back. That is also what makes it work at all: the pages
 * somebody needs to sign in to are exactly the ones that refuse to be framed.
 *
 * The canvas is focusable and takes focus on click, because keystrokes have to
 * land somewhere and a person who has just clicked a password field expects the
 * next thing they type to go there.
 */
export function BrowserCanvas({ onReady }: { onReady?: (live: boolean) => void }) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const [state, setState] = useState<ViewerState>('connecting');
    const [attempt, setAttempt] = useState(0);
    const [slow, setSlow] = useState(false);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const viewer = attachBrowserViewer({
            canvas,
            url: streamUrl(),
            onState: setState,
        });
        return () => viewer.close();
    }, [attempt]);

    useEffect(() => {
        onReady?.(state === 'live');
    }, [state, onReady]);

    // A browser that is not already running has to be started, and that is tens
    // of seconds: the config, an X server and Chrome, on an emulated image.
    // Saying only "Connecting…" for that long reads as broken — which is the
    // exact impression this panel spent a while giving for real — so once the
    // wait stops looking instant, say what is actually happening.
    useEffect(() => {
        if (state !== 'connecting') return;
        const timer = setTimeout(() => setSlow(true), 4000);
        // Cleared on the way out rather than on the way in, so that leaving
        // "connecting" is what resets this and the next attempt starts quiet.
        return () => {
            clearTimeout(timer);
            setSlow(false);
        };
    }, [state, attempt]);

    const retry = () => setAttempt((value) => value + 1);

    return (
        <div className="relative h-full w-full bg-[var(--bg-canvas)]">
            <canvas
                ref={canvasRef}
                tabIndex={0}
                onMouseDown={() => canvasRef.current?.focus()}
                aria-label="The agent's browser — click to take over"
                className="h-full w-full object-contain outline-none"
            />

            {state !== 'live' ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[var(--bg-canvas)] p-8 text-center">
                    {state === 'connecting' ? (
                        <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                            {slow
                                ? 'Starting the browser — the first time takes a moment.'
                                : 'Connecting to the browser…'}
                        </p>
                    ) : state === 'no-browser' ? (
                        <>
                            <p className="text-sm text-[var(--text-secondary)]">
                                The browser would not start.
                            </p>
                            <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                                This computer may be low on memory. Try again, or ask the
                                agent to open a page.
                            </p>
                            <Button variant="secondary" size="sm" onClick={retry}>
                                Try again
                            </Button>
                        </>
                    ) : state === 'stale-workspace' ? (
                        <>
                            <p className="text-sm text-[var(--text-secondary)]">
                                This computer is running an older version.
                            </p>
                            <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                                It started before the browser could be handed over, so there
                                is nothing to reconnect to. Restart it and try again.
                            </p>
                        </>
                    ) : state === 'refused' ? (
                        <p className="max-w-prose text-sm text-[var(--text-secondary)]">
                            You are not signed in, so this browser cannot be shown.
                        </p>
                    ) : (
                        <>
                            <p className="text-sm text-[var(--text-secondary)]">
                                The connection dropped.
                            </p>
                            <Button variant="secondary" size="sm" onClick={retry}>
                                Reconnect
                            </Button>
                        </>
                    )}
                </div>
            ) : null}
        </div>
    );
}
