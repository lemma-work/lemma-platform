'use client';

import { toast } from 'sonner';

import { SettingsChoiceList } from '@/components/settings/settings-kit';
import { useSetDefaultSurface, useUserSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import type { SurfacePlatform } from 'lemma-sdk';
import { StepLoader } from '@/components/brand/loader';

const PLATFORM_LABEL: Record<string, string> = {
    SLACK: 'Slack',
    TEAMS: 'Teams',
    TELEGRAM: 'Telegram',
    WHATSAPP: 'WhatsApp',
    RESEND: 'Resend',
};

/**
 * What the shared inbound identity is *called* on each platform, so the line
 * that explains the choice names the thing the person actually messages rather
 * than the abstract "address".
 */
const PLATFORM_ADDRESS: Record<string, string> = {
    SLACK: 'Slack app',
    TEAMS: 'Teams app',
    TELEGRAM: 'Telegram bot',
    WHATSAPP: 'WhatsApp number',
    RESEND: 'mailbox',
};

const platformLabel = (platform: string) => PLATFORM_LABEL[platform] ?? platform;

const platformAddress = (platform: string) =>
    PLATFORM_ADDRESS[platform] ?? `${platformLabel(platform)} address`;

/** "Slack", "Slack and Telegram", "Slack, Telegram and Resend". */
const joinNames = (names: string[]) =>
    names.length <= 1
        ? (names[0] ?? '')
        : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;

/**
 * User-scoped surface routing. When two surfaces answer at the *same* address —
 * Lemma's shared bot or number fronting pods in several orgs — only one of them
 * can take a message, so this panel raises that choice.
 *
 * Only those choices are drawn. A surface on its own address (a pod's own bot,
 * its own mailbox) can only ever receive what was sent to it, so listing it
 * asks nothing; naming every such pod turned this panel into a wall of repeated
 * names that answered no question. The platforms they sit on are named in one
 * closing line instead, so "which surfaces reach me" still has an answer.
 */
export function UserSurfacesPanel() {
    const { data, isLoading } = useUserSurfaces();
    const { data: podsData } = useAccessiblePods();
    const { mutate: setDefault, isPending, variables } = useSetDefaultSurface();

    const groups = data?.groups ?? [];
    const choices = groups
        .map((group) => ({
            group,
            contended: (group.surfaces ?? []).filter((surface) => surface.shares_address),
        }))
        .filter(({ contended }) => contended.length > 1);
    const settled = groups.filter(
        (group) => !choices.some(({ group: chosen }) => chosen.platform === group.platform)
    );

    const podLabel = (podId: string) => {
        const pod = podsData?.items.find((candidate) => candidate.id === podId);
        if (!pod) return null;
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
        <div className="grid gap-6">
            {choices.map(({ group, contended }) => {
                const label = platformLabel(group.platform);
                // While a pick is in flight the row the user clicked reads as
                // chosen: the list re-renders from the server answer, and a
                // check that jumps back for one round trip reads as a failure.
                const saving = isPending && variables?.platform === group.platform;
                const selectedId =
                    (saving ? variables?.surface_id : group.default_surface_id) ?? '';

                return (
                    <div key={group.platform} className="grid gap-2">
                        <div className="flex items-center justify-between gap-3">
                            <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
                            {group.default_surface_id ? null : (
                                <span className="chip chip-sm state-badge-warning shrink-0">Pick one</span>
                            )}
                        </div>
                        <p className="text-xs leading-5 text-[var(--text-secondary)]">
                            {contended.length} pods share one {platformAddress(group.platform)} — choose the one that
                            answers you.
                        </p>
                        <SettingsChoiceList
                            ariaLabel={`Which pod answers you on ${label}`}
                            value={selectedId}
                            disabled={isPending}
                            onChange={(surfaceId) => choose(group.platform, surfaceId)}
                            options={contended.map((surface) => {
                                const title = podLabel(surface.pod_id) ?? surface.name;
                                // The surface's own name earns a second line only
                                // when it says something the row does not already:
                                // inside the WhatsApp group, a surface named
                                // "whatsapp" is the platform said twice.
                                const detail =
                                    surface.name !== title &&
                                    surface.name.toLowerCase() !== label.toLowerCase()
                                        ? surface.name
                                        : undefined;
                                return { value: surface.id, label: title, description: detail };
                            })}
                        />
                    </div>
                );
            })}

            {settled.length ? (
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                    {joinNames(settled.map((group) => platformLabel(group.platform)))}{' '}
                    {settled.length === 1 ? 'reaches' : 'reach'} you at{' '}
                    {settled.length === 1 ? 'its own address' : 'their own addresses'} — nothing to choose there.
                </p>
            ) : null}
        </div>
    );
}
