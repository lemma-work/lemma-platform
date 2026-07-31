'use client';

import { Check, Loader2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { useSetDefaultSurface, useUserSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { cn } from '@/lib/utils';
import type { SurfacePlatform } from 'lemma-sdk';

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
 * User-scoped surface routing. When the same person is reachable through more
 * than one surface on a platform (e.g. a shared bot spanning orgs), only one
 * can answer — this panel surfaces those conflicts and lets the user pick which
 * surface wins.
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
                <Loader2 className="h-4 w-4 animate-spin" /> Loading your surfaces…
            </div>
        );
    }

    if (!groups.length) {
        return (
            <p className="text-sm leading-6 text-[var(--text-secondary)]">
                No surfaces reach you yet. Once a pod answers you in Slack, email, or another channel, it shows up here.
            </p>
        );
    }

    return (
        <div className="grid gap-4">
            {groups.map((group) => {
                const surfaces = group.surfaces ?? [];
                const hasConflict = Boolean(group.conflict) && surfaces.length > 1;

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

                        {surfaces.length <= 1 ? (
                            <p className="text-xs leading-5 text-[var(--text-secondary)]">
                                Answers you from {podLabel(surfaces[0]?.pod_id ?? '')}.
                            </p>
                        ) : (
                            <>
                                <p className="text-xs leading-5 text-[var(--text-secondary)]">
                                    You’re reachable from several {platformLabel(group.platform)} surfaces — choose the one that should answer you.
                                </p>
                                <div className="grid gap-1.5">
                                    {surfaces.map((surface) => {
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
                                                        <Loader2 className="h-4 w-4 animate-spin" />
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
                        )}
                    </div>
                );
            })}
        </div>
    );
}
