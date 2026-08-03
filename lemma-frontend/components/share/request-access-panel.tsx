'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { ShareTarget } from '@/lib/share/share-link';

/**
 * The right-sized ask, offered where the refusal happens.
 *
 * The only request primitive used to be a pod join request, whose approval mints
 * membership with a default role — far more than a sharer means when they send
 * one document. This asks for that document instead, and approving it leaves the
 * requester a non-member.
 */
export function RequestAccessPanel({
    target,
    resourceLabel,
}: {
    target: ShareTarget;
    resourceLabel: string;
}) {
    const queryClient = useQueryClient();
    const [message, setMessage] = useState('');
    const client = getLemmaClient(target.podId);

    // Only an id-addressed target can be asked about — the endpoint keys the
    // request on the resolved resource id.
    const canAsk = Boolean(target.resourceId || target.resourceName);

    const requestKey = ['resource-access-request', target.podId, target.resourceType, target.resourceId];
    const { data: existing, isPending } = useQuery({
        queryKey: requestKey,
        queryFn: async () => {
            try {
                return await client.resourceAccess.myAccessRequest(
                    target.resourceType,
                    target.resourceId!,
                );
            } catch {
                return null;
            }
        },
        enabled: Boolean(target.resourceId),
        retry: false,
    });

    const submit = useMutation({
        mutationFn: () =>
            client.resourceAccess.requestAccess({
                resource_type: target.resourceType as never,
                resource_id: target.resourceId ?? null,
                resource_name: target.resourceName ?? null,
                message: message.trim() || null,
            }),
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: requestKey });
        },
    });

    if (!canAsk) return null;

    const hasPendingRequest = Boolean(existing) || submit.isSuccess;

    return (
        <section className="rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-1)] p-5">
            {hasPendingRequest ? (
                <>
                    <h2 className="text-sm font-medium text-[var(--text-primary)]">
                        Your request was sent
                    </h2>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        Whoever owns this {resourceLabel.toLowerCase()} can approve it. You&apos;ll
                        be able to open it as soon as they do — no pod membership needed.
                    </p>
                </>
            ) : (
                <>
                    <h2 className="text-sm font-medium text-[var(--text-primary)]">
                        Ask for access
                    </h2>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        Request read access to this {resourceLabel.toLowerCase()} on its own. You
                        won&apos;t be added to the pod.
                    </p>
                    <textarea
                        value={message}
                        onChange={(event) => setMessage(event.target.value)}
                        placeholder="Add a note (optional)"
                        rows={2}
                        maxLength={500}
                        className="mt-3 w-full resize-none rounded-md border border-[color:var(--border-subtle)] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)]"
                    />
                    <div className="mt-3 flex items-center gap-3">
                        <Button
                            type="button"
                            variant="primary"
                            size="sm"
                            loading={submit.isPending}
                            loadingLabel="Sending"
                            disabled={isPending}
                            onClick={() => submit.mutate()}
                        >
                            Request access
                        </Button>
                        {submit.isError ? (
                            <span className="text-xs text-[var(--state-error)]">
                                Could not send the request.
                            </span>
                        ) : null}
                    </div>
                </>
            )}
        </section>
    );
}
