'use client';

import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { BrowserPane } from '@/components/workspace/browser-pane';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { PageLoader } from '@/components/brand/loader';
import { AlertTriangle, LockKeyhole } from '@/components/ui/icons';
import type { SignInOutcome } from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/** A URL reduced to the host somebody would check before typing a password.
 *
 * Parsed rather than string-trimmed: `https://evil.test/#app.example.com` and
 * `https://app.example.com@evil.test/` both end up reading as the wrong host
 * under a naive prefix strip, and this is the one label on the page that a
 * person is being asked to trust.
 */
const hostOf = (url: string): string => {
    try {
        return new URL(url).host;
    } catch {
        return url.replace(/^https?:\/\//, '').split('/')[0] ?? url;
    }
};

/**
 * One paused `browser_sign_in`, answerable.
 *
 * Shared by the two places a person can meet a sign-in, because the *answer*
 * is the part that must not be missing from either. `answerSignIn` is the only
 * route back to the paused run — nothing detects a completed login on its own —
 * so a surface that shows the browser without these controls lets somebody sign
 * in and leaves the agent waiting for ever.
 *
 * `page` is the standalone route a link from Slack or email lands on; `panel`
 * is the computer panel beside the conversation. They differ in chrome and
 * spacing only: the same query, the same mutation, the same anti-phishing host
 * display, which matters at least as much in the panel as on the page.
 */
export function SignInEmbed({
    conversationId,
    toolCallId,
    variant,
    onResolved,
}: {
    conversationId: string;
    toolCallId: string;
    variant: 'page' | 'panel';
    /** Told when the pause is answered, so a host holding this open in a URL
     *  can put itself away rather than leaving a spent sign-in on screen. */
    onResolved?: () => void;
}) {
    const [liveUrl, setLiveUrl] = useState<string | null>(null);
    // What the person was told, kept here rather than re-read: the answer
    // resolves the pause, so asking again returns nothing at all.
    const [outcome, setOutcome] = useState<SignInOutcome | null>(null);

    const request = useQuery({
        queryKey: ['pending-sign-in', conversationId, toolCallId],
        queryFn: () =>
            getLemmaClient().webLogins.pendingSignIn(conversationId, toolCallId),
        retry: false,
    });

    const answer = useMutation({
        mutationFn: (options: { signedIn: boolean }) =>
            getLemmaClient().webLogins.answerSignIn(conversationId, toolCallId, options),
        onSuccess: (result) => {
            setOutcome(result);
            onResolved?.();
        },
    });

    if (request.isPending) return <PageLoader />;

    if (request.isError) {
        return (
            <Centered>
                <EmptyState
                    variant="region"
                    icon={<AlertTriangle />}
                    title="This link is not for your account"
                    description="Ask the agent to send it again, to the account you are signed in to here."
                />
            </Centered>
        );
    }

    if (outcome) {
        return (
            <Centered>
                <EmptyState
                    variant="region"
                    icon={outcome.signed_in ? <LockKeyhole /> : <AlertTriangle />}
                    title={outcome.signed_in ? 'Signed in' : 'Told the agent'}
                    description={
                        !outcome.signed_in
                            ? 'The agent knows you could not sign in, and will not wait. You can close this.'
                            : outcome.working
                              ? 'The agent is carrying on. The browser stays signed in, so you will not be asked again. You can close this.'
                              : 'The agent is carrying on, but the site was still showing a login form just now — so you may be asked again. You can close this.'
                    }
                />
            </Centered>
        );
    }

    const data = request.data;
    // Where the browser *is*, not where it was sent. A sign-in is a chain of
    // redirects by design -- to an identity provider, to an MFA step, back --
    // and a header fixed to the requested origin kept naming the first site,
    // with its padlock, above a page served by another one. On the one screen
    // in this product whose whole job is "type your password here", that is a
    // claim we cannot make and must not appear to.
    const showing = liveUrl || data.origin;
    const host = hostOf(showing);
    const secure = showing.startsWith('https://');
    // A redirect somewhere else is not a fault, and saying so plainly is worth
    // more than hiding it: an SSO hop is what a real sign-in looks like.
    const elsewhere = hostOf(showing) !== hostOf(data.origin);
    const compact = variant === 'panel';

    return (
        <div className="flex h-full min-h-0 w-full flex-col gap-3">
            <header className="flex flex-col gap-1">
                <div className="flex items-center gap-2">
                    <LockKeyhole
                        className={
                            secure ? 'text-[var(--state-success)]' : 'text-[var(--state-warning)]'
                        }
                    />
                    {/* The host the browser is on, reported by the stream
                        itself. This is what somebody checks before they type a
                        password, so it has to track the page rather than the
                        request that started it. */}
                    <h1 className={compact ? 'text-sm font-medium' : 'text-base font-medium'}>
                        {host}
                    </h1>
                    {!secure ? (
                        <span className="text-xs text-[var(--state-warning)]">
                            not a secure connection
                        </span>
                    ) : null}
                    {elsewhere ? (
                        <span className="text-xs text-[var(--text-tertiary)]">
                            signing in to {hostOf(data.origin)}
                        </span>
                    ) : null}
                </div>
                {compact ? null : (
                    <p className="text-sm text-[var(--text-secondary)]">
                        {/* The agent's own words, quoted as theirs rather than
                            presented as the app speaking. */}
                        The agent says: &ldquo;{data.reason}&rdquo;
                    </p>
                )}
                <p className="text-xs text-[var(--text-tertiary)]">
                    Sign in below as you normally would. Lemma keeps the session so the
                    agent can carry on, and never sees your password.
                </p>
            </header>

            <div className="min-h-0 flex-1">
                <BrowserPane origin={data.origin} onNavigated={setLiveUrl} />
            </div>

            {answer.isError ? (
                <div className="rounded-lg border border-[var(--state-warning)] px-3 py-2 text-sm">
                    That did not reach the agent. It may have stopped waiting — try
                    again, and if it keeps failing you can close this and tell it in
                    the conversation.
                </div>
            ) : null}

            <footer className="flex items-center justify-end gap-2">
                <Button
                    variant="quiet"
                    size={compact ? 'xs' : undefined}
                    onClick={() => answer.mutate({ signedIn: false })}
                    disabled={answer.isPending}
                >
                    Can&rsquo;t right now
                </Button>
                <Button
                    variant="primary"
                    size={compact ? 'xs' : undefined}
                    onClick={() => answer.mutate({ signedIn: true })}
                    disabled={answer.isPending}
                >
                    {answer.isPending ? 'Checking…' : 'I’m signed in'}
                </Button>
            </footer>
        </div>
    );
}

function Centered({ children }: { children: React.ReactNode }) {
    return (
        <div className="flex h-full items-center justify-center p-8">{children}</div>
    );
}
