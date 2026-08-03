'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { StepLoader } from '@/components/brand/loader';
import { GuestResourceView } from '@/components/share/guest-resource-view';
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
 * wall no matter how widely that document was shared. Now the redirect happens
 * only for people who actually have pod access, and everyone else gets the
 * resource itself if it is theirs to read.
 */
export function ShareLanding({
    destination,
    name,
    article,
    detail,
    cardPath,
    kind,
    target,
}: ShareLandingProps) {
    const router = useRouter();
    const { isAuthenticated, isLoading } = useLemmaAuth();

    // Does this reader have the pod itself? `pods.get` is the cheapest honest
    // question — it is exactly what the workspace shell asks first, so agreeing
    // with it means no one is redirected into a wall.
    const { data: hasPodAccess, isPending: isCheckingPod } = useQuery({
        queryKey: ['share-pod-access', target?.podId],
        queryFn: async () => {
            try {
                await getLemmaClient().pods.get(target!.podId);
                return true;
            } catch {
                return false;
            }
        },
        enabled: Boolean(isAuthenticated && target),
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
        enabled: Boolean(isAuthenticated && target && hasPodAccess === false),
        retry: false,
    });

    useEffect(() => {
        if (isLoading || !isAuthenticated) return;
        // No addressable resource (a pod link) — keep the old behaviour and let
        // the workspace explain itself.
        if (!target) {
            router.replace(destination);
            return;
        }
        if (hasPodAccess) router.replace(destination);
    }, [isAuthenticated, isLoading, destination, router, target, hasPodAccess]);

    const isResolving = isLoading
        || (isAuthenticated && Boolean(target) && (isCheckingPod || hasPodAccess === true))
        || (isAuthenticated && hasPodAccess === false && isCheckingPreview);

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
            <GuestResourceView
                target={target}
                kind={kind}
                preview={preview as never}
                fallbackName={name}
            />
        );
    }

    const isDeniedToSignedInReader = isAuthenticated && Boolean(target) && !preview;

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
                        ) : (
                            <>
                                {name ? `${name} is ${article} on Lemma. ` : ''}
                                {detail}
                            </>
                        )}
                    </p>
                </div>

                {isDeniedToSignedInReader && target ? (
                    // The ask belongs at the refusal, not somewhere else in the
                    // product the reader has no way to find.
                    <div className="mt-6">
                        <JoinPodPanel podId={target.podId} />
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
