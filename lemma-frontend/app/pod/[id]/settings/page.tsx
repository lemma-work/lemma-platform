'use client';

import { use, useState } from 'react';
import { Info } from '@/components/ui/icons';

import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PodMark } from '@/components/pod/pod-mark';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { PodBundleSettingsPanel } from '@/components/bundle/pod-bundle-settings';
import { EmojiPicker } from '@/components/shared/emoji-picker';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { Button } from '@/components/ui/button';
import { SettingsChoiceList, SettingsHelpText, SettingsPanel, SettingsStack } from '@/components/settings/settings-kit';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod, useUpdatePod } from '@/lib/hooks/use-pods';
import { PodJoinPolicy } from '@/lib/types';
import { parseResourceIcon } from '@/lib/utils/resource-icon-value';
import { PodSettingsPanelsFill } from '@/components/pod/route-skeletons';

export default function PodSettingsPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodSettingsPageContent params={params} />
        </ProtectedRoute>
    );
}

function PodSettingsPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const { data: pod, isLoading: isLoadingPod } = usePod(podId);

    const canUpdatePod = podAccess.can('pod.update');

    return (
        <PodSettingsShell
            podId={podId}
            title="General"
            width="form"
        >
            {/* The header, the nav and the width are known before the pod is,
                so waiting on it fills the body rather than replacing the page —
                and it fills it with the shape that is about to arrive. */}
            {isLoadingPod ? <PodSettingsPanelsFill panels={3} /> : (
            <SettingsStack>
            <PodIconPanel
                podId={podId}
                podName={pod?.name}
                iconUrl={pod?.icon_url}
                canUpdate={canUpdatePod}
            />

            <PodJoinPolicyPanel
                podId={podId}
                currentPolicy={pod?.config?.join_policy ?? PodJoinPolicy.INVITE_ONLY}
                canUpdate={canUpdatePod}
            />

            <PodBundleSettingsPanel
                podId={podId}
                podName={pod?.name}
                canUpdate={canUpdatePod}
                recipes={pod?.config?.recipes ?? []}
            />
            </SettingsStack>
            )}
        </PodSettingsShell>
    );
}

// Auto-joiners always receive the base pod role ("User"). Elevated roles
// (Editor / Admin) are granted only via invite or by approving a join request —
// see the note rendered below the selector and lemma-work/lemma-platform#30.
const POD_JOIN_POLICY_OPTIONS: {
    value: PodJoinPolicy;
    label: string;
    description: string;
    /** Role a person receives when they add themselves under this policy. */
    selfJoinRole?: string;
}[] = [
    {
        value: PodJoinPolicy.INVITE_ONLY,
        label: 'Invite only',
        description:
            'Nobody can add themselves. People join by invitation or an approved join request — the only way to grant Editor or Admin access.',
    },
    {
        value: PodJoinPolicy.ORG_MEMBERS,
        label: 'Organization members',
        description: 'Any member of this pod’s organization can add themselves to it.',
        selfJoinRole: 'User',
    },
    {
        value: PodJoinPolicy.PUBLIC,
        label: 'Anyone',
        description: 'Any Lemma user can add themselves, and is added to the organization as a member.',
        selfJoinRole: 'User',
    },
];

function PodJoinPolicyPanel({
    podId,
    currentPolicy,
    canUpdate,
}: {
    podId: string;
    currentPolicy: PodJoinPolicy;
    canUpdate: boolean;
}) {
    const updatePod = useUpdatePod();
    const [policy, setPolicy] = useState<PodJoinPolicy>(currentPolicy);

    const handleChange = (next: PodJoinPolicy) => {
        if (next === policy) return;
        const previous = policy;
        setPolicy(next);
        updatePod.mutate(
            { id: podId, data: { config: { join_policy: next } } },
            {
                onSuccess: () => toast.success('Pod access updated'),
                onError: (error) => {
                    setPolicy(previous);
                    toast.error(`Failed to update access: ${error.message}`);
                },
            },
        );
    };

    const disabled = !canUpdate || updatePod.isPending;

    return (
        <SettingsPanel
            title="Who can join"
            description="Decide whether people can add themselves to this pod or need an invite."
        >
            <SettingsChoiceList
                ariaLabel="Who can join this pod"
                options={POD_JOIN_POLICY_OPTIONS.map((option) => ({
                    value: option.value,
                    label: option.label,
                    description: option.selfJoinRole ? (
                        <span className="flex flex-col gap-1.5">
                            <span>{option.description}</span>
                            <span className="inline-flex w-fit items-center gap-1 rounded-full border border-[var(--chip-border)] bg-[var(--chip-bg)] px-2 py-0.5 text-xs font-medium text-[var(--chip-fg)]">
                                Joins as {option.selfJoinRole}
                            </span>
                        </span>
                    ) : (
                        option.description
                    ),
                }))}
                value={policy}
                onChange={handleChange}
                disabled={disabled}
            />
            {canUpdate ? (
                <SettingsHelpText className="mt-3 flex items-start gap-1.5">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span>
                        People who add themselves always get the base <strong>User</strong> role. To grant
                        Editor or Admin access, invite them or approve their join request from the Members tab.
                    </span>
                </SettingsHelpText>
            ) : (
                <SettingsHelpText className="mt-3">Your role cannot change pod settings.</SettingsHelpText>
            )}
        </SettingsPanel>
    );
}

/**
 * The pod's icon.
 *
 * A pod had no icon control at all — `icon_url` was only ever set by a bundle
 * import or the API — so an emoji that renders is an emoji nobody can choose.
 * The mark itself is the trigger: the thing you are changing is the thing you
 * click, and a chosen emoji commits on the spot rather than waiting behind a
 * Save button for a one-tap decision.
 */
function PodIconPanel({
    podId,
    podName,
    iconUrl,
    canUpdate,
}: {
    podId: string;
    podName?: string | null;
    iconUrl?: string | null;
    canUpdate: boolean;
}) {
    const updatePod = useUpdatePod();
    const storedIcon = parseResourceIcon(iconUrl);
    const storedGlyph = storedIcon?.kind === 'glyph' ? storedIcon.glyph : null;
    const disabled = !canUpdate || updatePod.isPending;

    const commit = (nextIcon: string | null) => {
        updatePod.mutate(
            { id: podId, data: { icon_url: nextIcon } },
            {
                onSuccess: () => toast.success(nextIcon ? 'Pod icon updated' : 'Pod icon cleared'),
                onError: (error) => toast.error(`Failed to update icon: ${error.message}`),
            },
        );
    };

    return (
        <SettingsPanel
            title="Icon"
            description="An emoji shown wherever this pod appears — the switcher, the sidebar, and your pod list."
        >
            <div className="flex items-center gap-3">
                <EmojiPicker
                    value={storedGlyph}
                    onSelect={(glyph) => commit(glyph)}
                    onClear={() => commit(null)}
                    disabled={disabled}
                >
                    <Button
                        variant="quiet"
                        size="icon"
                        disabled={disabled}
                        aria-label={storedGlyph ? `Change pod icon, currently ${storedGlyph}` : 'Choose a pod icon'}
                        className="h-11 w-11 shrink-0 rounded-lg p-0"
                    >
                        <ResourceIcon
                            iconUrl={iconUrl}
                            alt={`${podName || 'Pod'} icon`}
                            label={podName || 'Pod'}
                            identityKind="team"
                            identitySeed={podId}
                            identitySize={44}
                            className="h-11 w-11 shrink-0 rounded-lg bg-transparent"
                            fallback={<PodMark name={podName} size="lg" />}
                        />
                    </Button>
                </EmojiPicker>
                <SettingsHelpText>
                    {!canUpdate
                        ? 'Your role cannot change pod settings.'
                        : storedIcon?.kind === 'url'
                            ? 'This pod uses an uploaded image. Choosing an emoji replaces it.'
                            : 'Click the mark to choose one.'}
                </SettingsHelpText>
            </div>
        </SettingsPanel>
    );
}
