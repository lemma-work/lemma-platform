'use client';

import { CheckCircle2, Info } from '@/components/ui/icons';
import { toast } from 'sonner';

import { SurfaceMobileIdentity } from '@/components/surfaces/surface-mobile-identity';
import { SurfaceReachCard, hasReachCard } from '@/components/surfaces/surface-reach-card';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { useSetDefaultSurface, useUserSurfaces } from '@/lib/hooks/use-pod-surfaces';
import type { SurfacePlatformDefinition } from '@/lib/surfaces/registry';
import type { AssistantSurface } from '@/lib/types';
import type { SurfacePlatform } from 'lemma-sdk';
import { cn } from '@/lib/utils';
import { StepLoader } from '@/components/brand/loader';

/**
 * The proof state: the surface exists, and here is what to do with it.
 *
 * Usually that means its address — a handle, a link, a QR. Slack and Teams have
 * no address worth showing (their handle is a bot's display name, which you
 * never type anywhere), so there it is orientation instead: what happens next,
 * and where.
 *
 * For platforms running on Lemma's shared bot/number this is also the only
 * honest moment to raise the cross-org question — the same number can front
 * pods in several orgs, and which one answers *you* is a personal setting, not
 * an org one. It is asked here, when it has just become true, rather than in a
 * settings page nobody opens.
 */
export function SurfaceLiveStep({
    definition,
    surface,
}: {
    definition: SurfacePlatformDefinition;
    surface: AssistantSurface;
}) {
    // Slack and Teams have no address to show (see `hasReachCard`), and only
    // Slack has an `afterConnect` block to put in its place — without this the
    // proof state could render as an empty box, which reads as a failure.
    const hasProof =
        hasReachCard(surface)
        || Boolean(definition.afterConnect)
        || definition.capabilities.autoWebhook;

    return (
        <div className="grid gap-4">
            <SurfaceReachCard surface={surface} />

            {hasProof ? null : (
                <p className="surface-verdict is-valid">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Connected. {definition.label} can reach this agent now.
                </p>
            )}

            {definition.capabilities.autoWebhook ? (
                <p className="surface-verdict is-valid">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Lemma wired up delivery — nothing else to configure.
                </p>
            ) : null}

            {definition.afterConnect ? (
                <div className="surface-panel-muted grid gap-2 p-3">
                    <p className="text-sm font-medium text-[var(--text-primary)]">
                        {definition.afterConnect.title}
                    </p>
                    <ul className="grid gap-1.5">
                        {definition.afterConnect.lines.map((line) => (
                            <li
                                key={line}
                                className="flex items-start gap-2 text-xs leading-5 text-[var(--text-secondary)]"
                            >
                                <span
                                    className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-[var(--text-tertiary)]"
                                    aria-hidden
                                />
                                {line}
                            </li>
                        ))}
                    </ul>
                </div>
            ) : null}

            <SurfaceMobileIdentity surface={surface} />

            <ReachableElsewhereNotice surface={surface} />
        </div>
    );
}

/** Only renders when the address just shown really could answer this person in
 * more than one pod — Lemma's shared bot or number, fronting pods in several
 * orgs. A bot the pod brought itself has its own handle, so a message to it can
 * only land there; raising the question anyway suggests it might not. */
function ReachableElsewhereNotice({ surface }: { surface: AssistantSurface }) {
    const platform = String(surface.platform || '').toUpperCase();
    const { data: userSurfaces, isLoading } = useUserSurfaces(Boolean(platform));
    const { data: podsData } = useAccessiblePods();
    const { mutate: setDefault, isPending } = useSetDefaultSurface();

    const group = userSurfaces?.groups?.find(
        (candidate) => String(candidate.platform).toUpperCase() === platform,
    );
    // `shares_address` marks the surfaces answering at one address; the rest own
    // theirs. The choice is only this surface's to make when it is one of them.
    const sharing = (group?.surfaces ?? []).filter((candidate) => candidate.shares_address);
    if (isLoading || sharing.length < 2) return null;
    if (!sharing.some((candidate) => candidate.id === surface.id)) return null;

    const podNames = new Map((podsData?.items ?? []).map((pod) => [pod.id, pod.name]));
    const selectedId = group?.default_surface_id ?? surface.id;

    return (
        <div className="surface-panel-muted grid gap-2.5 p-3">
            <p className="flex items-start gap-2 text-xs leading-5 text-[var(--text-secondary)]">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                You’re in {sharing.length} pods reachable at this address. Your messages go to:
            </p>
            <div className="grid gap-1">
                {sharing.map((candidate) => {
                    const checked = candidate.id === selectedId;
                    return (
                        <button
                            key={candidate.id}
                            type="button"
                            disabled={isPending}
                            onClick={() =>
                                setDefault(
                                    {
                                        platform: platform as SurfacePlatform,
                                        surface_id: candidate.id,
                                    },
                                    {
                                        onError: (error) =>
                                            toast.error(`Couldn’t change that: ${error.message}`),
                                    },
                                )
                            }
                            className={cn('surface-default-option custom-focus-ring', checked && 'is-selected')}
                            aria-pressed={checked}
                        >
                            <span className="surface-radio" aria-hidden />
                            <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                                {podNames.get(candidate.pod_id) || candidate.name}
                            </span>
                            {isPending && checked ? <StepLoader size="xs" /> : null}
                        </button>
                    );
                })}
            </div>
            <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                Change this any time. It only affects you.
            </p>
        </div>
    );
}
