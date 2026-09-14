'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { use, useCallback, useState } from 'react';

import { BrowserPane } from '@/components/workspace/browser-pane';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { PageLoader } from '@/components/brand/loader';
import { AlertTriangle, LockKeyhole } from '@/components/ui/icons';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/**
 * Where the link in "please sign in to this site" lands.
 *
 * Inside `(dashboard)`, so it inherits `ProtectedRoute`. That matters more here
 * than anywhere: the whole point is that somebody opens this on a phone, from a
 * message, quite possibly signed out — and the previous version, which sat
 * outside the shell with no guard, told them the request had expired or was not
 * theirs. Which was a lie, twice over.
 *
 * The id in the URL grants nothing. Every call resolves it against the caller's
 * own session, so a forwarded link answers exactly as an invented one does.
 */
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

export default function SignInToSitePage({
    params,
}: {
    params: Promise<{ requestId: string }>;
}) {
    const { requestId } = use(params);
    const queryClient = useQueryClient();
    const [forced, setForced] = useState(false);
    const [liveUrl, setLiveUrl] = useState<string | null>(null);

    const request = useQuery({
        queryKey: ['sign-in-request', requestId],
        queryFn: () => getLemmaClient().webLogins.signInRequest(requestId),
        retry: false,
    });

    const finish = useMutation({
        mutationFn: (force: boolean) =>
            getLemmaClient().webLogins.finishSignIn(requestId, { force }),
        onSuccess: () =>
            queryClient.invalidateQueries({ queryKey: ['sign-in-request', requestId] }),
    });

    const decline = useMutation({
        mutationFn: () => getLemmaClient().webLogins.declineSignIn(requestId),
        onSuccess: () =>
            queryClient.invalidateQueries({ queryKey: ['sign-in-request', requestId] }),
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

    const data = request.data;
    // Where the browser *is*, not where it was sent. A sign-in is a chain of
    // redirects by design -- to an identity provider, to an MFA step, back --
    // and a header fixed to the requested origin kept naming the first site,
    // with its padlock, above a page served by another one. On the single page
    // in this product whose whole job is "type your password here", that is a
    // claim we cannot make and must not appear to.
    const showing = liveUrl || data.origin;
    const host = hostOf(showing);
    const secure = showing.startsWith('https://');
    // A redirect somewhere else is not a fault, and saying so plainly is worth
    // more than hiding it: an SSO hop is what a real sign-in looks like.
    const elsewhere = hostOf(showing) !== hostOf(data.origin);

    if (data.status === 'SIGNED_IN') {
        return (
            <Centered>
                <EmptyState
                    variant="region"
                    icon={<LockKeyhole />}
                    title="Signed in"
                    description={
                        data.saved
                            ? 'The agent is carrying on, and the login has been kept so you will not be asked next time. You can close this.'
                            : `The agent is carrying on. The login could not be kept${
                                  data.saved_detail ? ` (${data.saved_detail})` : ''
                              }, so you may be asked again.`
                    }
                />
            </Centered>
        );
    }

    if (data.status === 'DECLINED') {
        return (
            <Centered>
                <EmptyState
                    variant="region"
                    title="You declined this"
                    description="The agent has been told and will not wait. Send it a message if you want to try again."
                />
            </Centered>
        );
    }

    return (
        <div className="mx-auto flex h-full w-full max-w-[1100px] flex-col gap-3 p-4">
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
                    <h1 className="text-base font-medium">{host}</h1>
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
                <p className="text-sm text-[var(--text-secondary)]">
                    {/* The agent's own words, quoted as theirs rather than
                        presented as the app speaking. */}
                    The agent says: “{data.reason}”
                </p>
                <p className="text-xs text-[var(--text-tertiary)]">
                    Sign in below as you normally would. Lemma keeps the session so the
                    agent can carry on, and never sees your password.
                </p>
            </header>

            <div className="min-h-0 flex-1">
                <BrowserPane origin={data.origin} autoControl onNavigated={setLiveUrl} />
            </div>

            {finish.isError ? (
                <div className="rounded-lg border border-[var(--state-warning)] px-3 py-2 text-sm">
                    It does not look like you are signed in yet — the browser holds nothing
                    for this site.{' '}
                    <Button
                        variant="quiet"
                        size="xs"
                        onClick={() => {
                            setForced(true);
                            finish.mutate(true);
                        }}
                    >
                        Save anyway
                    </Button>
                </div>
            ) : null}

            <footer className="flex items-center justify-end gap-2">
                <Button
                    variant="quiet"
                    onClick={() => decline.mutate()}
                    disabled={decline.isPending || finish.isPending}
                >
                    Can’t right now
                </Button>
                <Button
                    variant="primary"
                    onClick={() => finish.mutate(forced)}
                    disabled={finish.isPending || decline.isPending}
                >
                    {finish.isPending ? 'Checking…' : 'I’m signed in'}
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
