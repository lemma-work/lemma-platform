'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { StepLoader } from '@/components/brand/loader';
import { captureEvent } from '@/lib/analytics/client';
import { SharedResourceView } from '@/components/share/shared-resource-view';
import { JoinPodPanel } from '@/components/share/join-pod-panel';
import { useLemmaAuth } from '@/lib/hooks/use-lemma-auth';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { ShareKind, ShareTarget } from '@/lib/share/share-link';

interface ShareLandingProps {
    /** Workspace-relative path, already validated as `/pod/…` on the server. */
    destination: string;
    name: string | null;
    /** Reads after the name: "an agent on Lemma". */
    article: string;
    detail: string;
    cardPath: string;
    kind: ShareKind;
    /** What the link points at, or null when it names no readable resource. */
    target: ShareTarget | null;
    /** The pod the link lives in — set for a pod link, which has no target. */
    podId: string | null;
}

/**
 * What a shared link opens.
 *
 * The markup is server-rendered so a crawler sees the name and the card without
 * running anything.
 *
 * For a signed-in reader this used to redirect into the workspace
 * unconditionally, which was fine for a teammate and a dead end for everyone
 * else: `/pod/…` answers "can I open this?" only by trying to render the whole
 * pod, so someone who was sent one document landed on a "request pod access"
 * wall no matter how widely that document was shared.
 *
 * Now nobody is redirected past the thing they clicked. A link should open what
 * it points at, and bouncing a member into the workspace answered a question
 * they had not asked — losing the document they came for, and their place in a
 * long one. Members get the same page with an "Open in pod" button on it, which
 * is the workspace offered rather than imposed. One case still redirects: a
 * member who cannot read this particular resource, where only the pod can
 * explain itself.
 *
 * A pod link is the other half of that. It names no resource, so a member is
 * still sent straight into the workspace — but everyone else used to be sent
 * there too, and landed on an access wall. That made the one link you would
 * actually paste into a group chat the one link that could not let anybody in,
 * while `/s/agent/…` two routes over has offered `JoinPodPanel` all along. A
 * pod link now makes the same offer, and the pod's own join policy decides
 * whether it lands instantly or waits for an admin.
 */
export function ShareLanding({
    destination,
    name,
    article,
    detail,
    cardPath,
    kind,
    target,
    podId,
}: ShareLandingProps) {
    const router = useRouter();
    const { isAuthenticated, isLoading } = useLemmaAuth();
    // A link naming a pod and nothing inside it.
    const isPodLink = !target && Boolean(podId);
    const accessPodId = target?.podId ?? podId;

    // Does this reader have the pod itself? `pods.get` is the cheapest honest
    // question — it is exactly what the workspace shell asks first, so agreeing
    // with it means no one is redirected into a wall.
    const { data: hasPodAccess, isPending: isCheckingPod } = useQuery({
        queryKey: ['share-pod-access', accessPodId],
        queryFn: async () => {
            try {
                await getLemmaClient().pods.get(accessPodId!);
                return true;
            } catch {
                return false;
            }
        },
        enabled: Boolean(isAuthenticated && accessPodId),
        retry: false,
        staleTime: 30_000,
    });

    const { data: preview, isPending: isCheckingPreview } = useQuery({
        queryKey: ['share-preview', target?.podId, target?.resourceType, target?.resourceId, target?.resourceName],
        queryFn: async () => {
            try {
                return await getLemmaClient(target!.podId).resourceAccess.preview(
                    target!.resourceType,
                    { id: target!.resourceId, name: target!.resourceName },
                );
            } catch {
                // 404 covers both "no such resource" and "not yours", by design.
                return null;
            }
        },
        enabled: Boolean(isAuthenticated && target),
        retry: false,
    });

    // The top of the loop. Recorded before the auth branch below, because the
    // reader who is *not* signed in is exactly the one this funnel is about --
    // waiting for authentication would only ever count people already inside.
    useEffect(() => {
        captureEvent('share_link.viewed', {
            kind,
            viewer_is_member: Boolean(hasPodAccess),
        });
    }, [kind, hasPodAccess]);

    useEffect(() => {
        if (isLoading || !isAuthenticated) return;
        if (isPodLink) {
            // A member already has the workspace; anyone else is shown the ask
            // rather than the wall.
            if (hasPodAccess === true) router.replace(destination);
            return;
        }
        // A malformed link that names neither a resource nor a pod. Nothing to
        // render, so the workspace explains itself as it always did.
        if (!target) {
            router.replace(destination);
            return;
        }
        // A member who cannot read this particular resource. Nothing can be
        // rendered here and the pod is the only place that can say why, so this
        // is the one case that still redirects.
        if (hasPodAccess && preview === null) router.replace(destination);
    }, [isAuthenticated, isLoading, destination, router, target, isPodLink, hasPodAccess, preview]);

    // The pod check only decides whether an "Open in pod" button appears, so it
    // no longer holds the document up — except when there is no preview to show,
    // where it is what separates "redirect a member" from "ask to join".
    const isResolving = isLoading
        // A pod link has nothing to draw until the membership answer is in: the
        // card and the join ask would only flash before the redirect.
        || (isAuthenticated && isPodLink && (isCheckingPod || hasPodAccess === true))
        || (isAuthenticated && Boolean(target) && isCheckingPreview)
        || (isAuthenticated && Boolean(target) && !preview && isCheckingPod)
        || (isAuthenticated && Boolean(target) && !preview && hasPodAccess === true);

    if (isResolving) {
        return (
            <main className="flex min-h-dvh items-center justify-center px-6">
                <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                    <StepLoader size="sm" /> Opening…
                </div>
            </main>
        );
    }

    if (isAuthenticated && target && preview) {
        return (
            <SharedResourceView
                target={target}
                kind={kind}
                preview={preview as never}
                fallbackName={name}
                openInPodHref={hasPodAccess ? destination : null}
            />
        );
    }

    const isDeniedToSignedInReader = isAuthenticated && Boolean(target) && !preview;
    // Who to offer the way in to, and null when there is nothing to ask for —
    // a signed-out reader signs in first, and a member is already through.
    const joinPodId = !isAuthenticated
        ? null
        : isPodLink && hasPodAccess === false
            ? podId
            : isDeniedToSignedInReader
                ? target?.podId ?? null
                : null;

    return (
        <main className="flex min-h-dvh items-center justify-center px-6 py-16">
            <div className="w-full max-w-xl">
                <div className="overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-2)] shadow-[var(--shadow-md)]">
                    {/* eslint-disable-next-line @next/next/no-img-element -- a dynamic route response, not a static asset for the image optimizer. */}
                    <img
                        src={cardPath}
                        alt={name ? `${name} on Lemma` : 'On Lemma'}
                        width={1200}
                        height={630}
                        className="block h-auto w-full"
                    />
                </div>

                <div className="mt-6 text-center">
                    <h1 className="text-xl font-medium text-[var(--text-primary)]">
                        {name || 'Shared on Lemma'}
                    </h1>
                    <p className="mt-1 text-sm text-[var(--text-secondary)]">
                        {isDeniedToSignedInReader ? (
                            'This isn’t shared with you. It may have been deleted, or it may only be open to the pod it lives in.'
                        ) : joinPodId ? (
                            `${name ? `${name} is a pod` : 'This is a pod'} on Lemma — the people, agents and work inside one boundary.`
                        ) : (
                            <>
                                {name ? `${name} is ${article} on Lemma. ` : ''}
                                {detail}
                            </>
                        )}
                    </p>
                </div>

                {joinPodId ? (
                    // The ask belongs at the refusal, not somewhere else in the
                    // product the reader has no way to find.
                    <div className="mt-6">
                        <JoinPodPanel
                            podId={joinPodId}
                            intro={isPodLink
                                ? 'You’re not in this pod yet. Join it to open the workspace and everything the pod shares.'
                                : undefined}
                        />
                    </div>
                ) : (
                    <div className="mt-6 flex flex-col items-center gap-3">
                        <Button variant="primary" asChild size="lg" className="gap-2">
                            <Link href={destination} prefetch={false}>
                                {isAuthenticated ? 'Open it' : 'Sign in to open'}
                                <ArrowRight className="h-4 w-4" />
                            </Link>
                        </Button>
                    </div>
                )}

                <p className="mt-10 text-center text-xs text-[var(--text-tertiary)]">
                    <Link href="/" className="hover:text-[var(--text-secondary)]">
                        Lemma — run your apps and agents, with your team
                    </Link>
                </p>
            </div>
        </main>
    );
}
