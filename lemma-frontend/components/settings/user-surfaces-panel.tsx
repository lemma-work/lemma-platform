'use client';

import { Check } from '@/components/ui/icons';
import { toast } from 'sonner';

import { useSetDefaultSurface, useUserSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { cn } from '@/lib/utils';
import type { SurfacePlatform } from 'lemma-sdk';
import { StepLoader } from '@/components/brand/loader';

const PLATFORM_LABEL: Record<string, string> = {
    SLACK: 'Slack',
    TEAMS: 'Teams',
    GMAIL: 'Gmail',
    OUTLOOK: 'Outlook',
    TELEGRAM: 'Telegram',
    WHATSAPP: 'WhatsApp',
    RESEND: 'Resend',
};

const platformLabel = (platform: string) => PLATFORM_LABEL[platform] ?? platform;

/**
 * User-scoped surface routing. When two surfaces answer at the *same* address —
 * Lemma's shared bot or number fronting pods in several orgs — only one of them
 * can take a message, so this panel raises that choice. Surfaces on their own
 * address (a pod's own bot, its own mailbox) are listed but never asked about:
 * a message sent to one of those can only ever arrive there.
 */
export function UserSurfacesPanel() {
    const { data, isLoading } = useUserSurfaces();
    const { data: podsData } = useAccessiblePods();
    const { mutate: setDefault, isPending, variables } = useSetDefaultSurface();

    const groups = data?.groups ?? [];

    const podLabel = (podId: string) => {
        const pod = podsData?.items.find((candidate) => candidate.id === podId);
        if (!pod) return 'a pod';
        return pod.organization_name ? `${pod.name} · ${pod.organization_name}` : pod.name;
    };

    const choose = (platform: SurfacePlatform, surfaceId: string) => {
        setDefault(
            { platform, surface_id: surfaceId },
            {
                onSuccess: () => toast.success('Default surface updated'),
                onError: (error) => toast.error(`Couldn’t update default: ${error.message}`),
            }
        );
    };

    if (isLoading) {
        return (
            <div className="flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
                <StepLoader size="sm" /> Loading your surfaces…
            </div>
        );
    }

    if (!groups.length) {
        return (
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
                No surfaces reach you yet. Once a pod answers you in Slack, email, or another surface, it shows up here.
            </p>
        );
    }

    return (
        <div className="grid gap-4">
            {groups.map((group) => {
                const surfaces = group.surfaces ?? [];
                const sharing = surfaces.filter((surface) => surface.shares_address);
                const own = surfaces.filter((surface) => !surface.shares_address);
                const hasConflict = sharing.length > 1;

                return (
                    <div
                        key={group.platform}
                        className="grid gap-2 rounded-lg border border-[color:var(--border-subtle)] bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] p-3"
                    >
                        <div className="flex items-center justify-between gap-2">
                            <p className="text-sm font-medium text-[var(--text-primary)]">{platformLabel(group.platform)}</p>
                            {hasConflict ? (
                                <span className="chip chip-sm state-badge-warning shrink-0">Pick one</span>
                            ) : null}
                        </div>

                        {hasConflict ? (
                            <>
                                <p className="text-xs leading-5 text-[var(--text-secondary)]">
                                    These pods share one {platformLabel(group.platform)} address — choose the one that should answer you.
                                </p>
                                <div className="grid gap-1.5">
                                    {sharing.map((surface) => {
                                        const isDefault =
                                            surface.is_default || group.default_surface_id === surface.id;
                                        const isSaving = isPending && variables?.surface_id === surface.id;
                                        return (
                                            <button
                                                key={surface.id}
                                                type="button"
                                                onClick={() => choose(group.platform, surface.id)}
                                                disabled={isPending}
                                                className={cn(
                                                    'surface-picker-button surface-choice-row custom-focus-ring',
                                                    isDefault && 'is-selected'
                                                )}
                                            >
                                                <span className="surface-choice-icon">
                                                    {isSaving ? (
                                                        <StepLoader size="sm" />
                                                    ) : isDefault ? (
                                                        <Check className="h-4 w-4" />
                                                    ) : (
                                                        <span className="block h-2 w-2 rounded-full bg-[var(--border-strong)]" />
                                                    )}
                                                </span>
                                                <span className="min-w-0 flex-1 text-left">
                                                    <span className="surface-choice-title">{podLabel(surface.pod_id)}</span>
                                                    <span className="surface-choice-copy">{surface.name}</span>
                                                </span>
                                            </button>
                                        );
                                    })}
                                </div>
                            </>
                        ) : null}

                        {own.length ? (
                            <p className="text-xs leading-5 text-[var(--text-secondary)]">
                                {own.length === 1
                                    ? `Answers you from ${podLabel(own[0].pod_id)}.`
                                    : `${own.length} pods answer you, each at its own address: ${own
                                          .map((surface) => podLabel(surface.pod_id))
                                          .join(', ')}.`}
                            </p>
                        ) : null}
                    </div>
                );
            })}
        </div>
    );
}
