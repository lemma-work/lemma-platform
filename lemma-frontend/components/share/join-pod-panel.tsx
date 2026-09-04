'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

/**
 * The ask offered when a shared link isn't yours to open.
 *
 * Joining the pod is a bigger step than reading one document, and it is on
 * purpose: the pod is the unit of collaboration, so anything narrower than
 * "anyone with a Lemma account" stops at membership. Making that the visible
 * next step beats the previous behaviour, where the same wall appeared with no
 * explanation of what had gone wrong or what to do about it.
 *
 * Whether this lands instantly or waits for an admin is the pod's own join
 * policy: an `ORG_MEMBERS` pod admits colleagues on the spot, `INVITE_ONLY`
 * queues the request. The response says which happened rather than guessing up
 * front, because the policy is not readable from out here.
 */
export function JoinPodPanel({
    podId,
    intro,
}: {
    podId: string;
    /** Overrides the ask for a link that *is* the pod, where "this lives in a
     *  pod you're not part of" describes nothing the reader clicked. */
    intro?: string;
}) {
    const queryClient = useQueryClient();
    const client = getLemmaClient();
    const requestKey = ['share-join-request', podId];

    const { data: existing, isPending } = useQuery({
        queryKey: requestKey,
        queryFn: async () => {
            try {
                return await client.podJoinRequests.me(podId);
            } catch {
                return null;
            }
        },
        retry: false,
    });

    const join = useMutation({
        mutationFn: () => client.podJoinRequests.create(podId),
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: requestKey });
        },
    });

    const status = (join.data as { status?: string } | undefined)?.status
        ?? (existing as { status?: string } | null)?.status;

    // An open pod admits you on the spot, so there is nothing to wait for —
    // reloading lands you in the workspace.
    if (status === 'APPROVED') {
        return (
            <section className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-5 text-center">
                <p className="text-sm text-[var(--text-secondary)]">
                    You&apos;re in. Reload to open it.
                </p>
                {/* Secondary: the ask already succeeded, so this confirms rather
                    than calls to action — and it keeps one primary per view. */}
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="mt-3"
                    onClick={() => window.location.reload()}
                >
                    Reload
                </Button>
            </section>
        );
    }

    if (status === 'PENDING') {
        return (
            <section className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-5 text-center">
                <p className="text-sm text-[var(--text-secondary)]">
                    Your request to join is with the pod&apos;s admins. You&apos;ll be able to open
                    this once they approve it.
                </p>
            </section>
        );
    }

    return (
        <section className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-5 text-center">
            <p className="text-sm text-[var(--text-secondary)]">
                {intro ?? (
                    <>
                        This lives in a pod you&apos;re not part of. Join it to open this and everything
                        else the pod shares.
                    </>
                )}
            </p>
            <Button
                type="button"
                variant="primary"
                size="sm"
                className="mt-3"
                loading={join.isPending}
                loadingLabel="Requesting"
                disabled={isPending}
                onClick={() => join.mutate()}
            >
                Join pod
            </Button>
            {join.isError ? (
                <p className="mt-2 text-xs text-[var(--state-error)]">
                    Could not send that request.
                </p>
            ) : null}
        </section>
    );
}
