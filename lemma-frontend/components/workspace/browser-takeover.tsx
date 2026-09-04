'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { BrowserCanvas } from '@/components/workspace/browser-canvas';
import { Check, Lock, ShieldAlert } from '@/components/ui/icons';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/**
 * How often to touch the browser so it is still there when they finish.
 *
 * `agent-browser` closes Chrome after 120s without a command. Half that leaves
 * room for one heartbeat to fail without the page dying under the person.
 */
const HEARTBEAT_MS = 55_000;

/** The origin, as a person reads it, plus whether it is actually protected. */
function readOrigin(raw: string): { host: string; secure: boolean; valid: boolean } {
    try {
        const url = new URL(raw);
        return {
            host: url.host,
            secure: url.protocol === 'https:',
            valid: true,
        };
    } catch {
        return { host: raw, secure: false, valid: false };
    }
}

/**
 * The person drives the agent's browser.
 *
 * The chrome around the frame is the point of this component, not decoration.
 * We are asking somebody to type a password into a page rendered inside our own
 * UI, which is the exact shape of a phishing screen — so the site's real host
 * and whether it is on TLS are stated plainly, above the frame, before they
 * start. Without that there is no way for a careful person to tell this apart
 * from the thing they are right to refuse.
 */
export function BrowserTakeover({ requestId }: { requestId: string }) {
    const [finished, setFinished] = useState(false);

    const { data, isPending, error, refetch } = useQuery({
        queryKey: ['takeover', requestId],
        queryFn: () => getLemmaClient().workspace.openTakeover(requestId),
        // The signed view URL expires; re-minting well inside that window keeps
        // a long login from dying halfway through.
        refetchInterval: 5 * 60_000,
        retry: false,
    });

    const resolve = useMutation({
        mutationFn: (done: boolean) =>
            getLemmaClient().workspace.resolveTakeover(requestId, done),
        onSuccess: () => setFinished(true),
    });

    useEffect(() => {
        if (!data || finished) return;
        const client = getLemmaClient();
        const beat = () => {
            void client.workspace.heartbeatTakeover(requestId).catch(() => {
                // A missed beat is not worth interrupting somebody mid-password.
                // The next one either lands or the frame visibly fails.
            });
        };
        const timer = setInterval(beat, HEARTBEAT_MS);
        return () => clearInterval(timer);
    }, [data, finished, requestId]);

    const origin = useMemo(() => readOrigin(data?.origin ?? ''), [data?.origin]);

    const finish = useCallback((done: boolean) => resolve.mutate(done), [resolve]);

    if (isPending) {
        return (
            <main className="flex min-h-dvh items-center justify-center p-8">
                <p className="text-sm text-[var(--text-tertiary)]">Opening the browser…</p>
            </main>
        );
    }

    if (error || !data) {
        return (
            <main className="flex min-h-dvh flex-col items-center justify-center gap-2 p-8 text-center">
                <p className="text-sm text-[var(--text-primary)]">
                    This request has expired, or it is not yours.
                </p>
                <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                    Ask the agent to try again — it will send a fresh link.
                </p>
            </main>
        );
    }

    if (finished || data.status !== 'pending') {
        return (
            <main className="flex min-h-dvh flex-col items-center justify-center gap-2 p-8 text-center">
                <Check className="size-5 text-[var(--text-secondary)]" />
                <p className="text-sm text-[var(--text-primary)]">
                    Done. You can close this.
                </p>
                <p className="max-w-prose text-sm text-[var(--text-tertiary)]">
                    The agent has what it needs and is carrying on.
                </p>
            </main>
        );
    }

    return (
        <main className="flex min-h-dvh flex-col">
            <header className="flex flex-col gap-3 border-b border-[var(--row-border)] px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-sm text-[var(--text-secondary)]">
                        You are signing in to
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                        {origin.secure ? (
                            <Lock className="size-3.5 text-[var(--text-secondary)]" />
                        ) : (
                            <ShieldAlert className="size-3.5 text-[var(--state-warning)]" />
                        )}
                        <span className="text-sm text-[var(--text-primary)]">
                            {origin.host}
                        </span>
                    </span>
                </div>
                {!origin.secure ? (
                    <p className="text-xs text-[var(--state-warning)]">
                        This site is not using a secure connection. Anything you type can be
                        read in transit — do not enter a password here.
                    </p>
                ) : null}
                {data.reason ? (
                    <p className="text-xs text-[var(--text-tertiary)]">{data.reason}</p>
                ) : null}
                <p className="max-w-prose text-xs text-[var(--text-tertiary)]">
                    This is the agent&rsquo;s own browser, running on your computer in Lemma.
                    What you type goes to the site, and Lemma does not keep your password.
                    Press Done when you are signed in.
                </p>
            </header>

            {/* A canvas, not a frame: the sites somebody needs to sign in to
                are exactly the ones that refuse to be embedded, and a frame
                could only ever be watched anyway. */}
            <div className="min-h-0 flex-1">
                <BrowserCanvas />
            </div>

            <footer className="flex items-center justify-end gap-2 border-t border-[var(--row-border)] px-4 py-3">
                <Button
                    variant="quiet"
                    size="sm"
                    onClick={() => finish(false)}
                    disabled={resolve.isPending}
                >
                    Cancel
                </Button>
                <Button
                    variant="primary"
                    size="sm"
                    onClick={() => finish(true)}
                    loading={resolve.isPending}
                >
                    Done, I&rsquo;m signed in
                </Button>
                <Button
                    variant="quiet"
                    size="sm"
                    onClick={() => void refetch()}
                    disabled={resolve.isPending}
                >
                    Reload
                </Button>
            </footer>
        </main>
    );
}
