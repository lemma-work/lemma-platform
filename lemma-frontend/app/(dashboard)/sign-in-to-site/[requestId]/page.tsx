'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { use, useState } from 'react';

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
export default function SignInToSitePage({
    params,
}: {
    params: Promise<{ requestId: string }>;
}) {
    const { requestId } = use(params);
    const queryClient = useQueryClient();
    const [forced, setForced] = useState(false);

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
    const host = data.origin.replace(/^https?:\/\//, '');
    const secure = data.origin.startsWith('https://');

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
                    {/* The host, from the request the server holds — not from
                        anything the page was handed. This is what somebody
                        checks before they type a password. */}
                    <h1 className="text-base font-medium">{host}</h1>
                    {!secure ? (
                        <span className="text-xs text-[var(--state-warning)]">
                            not a secure connection
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
                <BrowserPane origin={data.origin} autoControl />
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
