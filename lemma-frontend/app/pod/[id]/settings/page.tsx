'use client';

import { use, useState, type FormEvent } from 'react';
import { Info } from '@/components/ui/icons';

import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PodMark } from '@/components/pod/pod-mark';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { PodBundleSettingsPanel } from '@/components/bundle/pod-bundle-settings';
import { EmojiPicker } from '@/components/shared/emoji-picker';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SettingsChoiceList, SettingsHelpText, SettingsPanel, SettingsStack } from '@/components/settings/settings-kit';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { usePod, useUpdatePod } from '@/lib/hooks/use-pods';
import { PodJoinPolicy } from '@/lib/types';
import { podNameError, normalizePodName } from '@/lib/utils/pod-name';
import { parseResourceIcon } from '@/lib/utils/resource-icon-value';
import { getSurfaceEmail } from '@/lib/utils/surfaces';
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
            {isLoadingPod ? <PodSettingsPanelsFill panels={4} /> : (
            <SettingsStack>
            <PodNamePanel
                podId={podId}
                podName={pod?.name}
                canUpdate={canUpdatePod}
            />

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
 * The pod's name.
 *
 * `PUT /pods/{id}` has accepted a name for as long as pods have existed, and the
 * shared `['pods']` query prefix was already written for "created, imported,
 * renamed or deleted" — the only missing piece was somewhere to type it. Until
 * there was, a pod named in a hurry, or named by whoever exported the bundle it
 * came from, kept that name unless somebody was willing to call the API by hand.
 *
 * Save on submit, not on change like the icon beside it: a name is typed rather
 * than picked, and committing per keystroke would mean a request per character
 * and a pod briefly called "Acm". The rule is mirrored locally so a rejected
 * character is answered as it is typed, but the server is still the judge —
 * a name is org-unique, and only it knows whether this one is taken.
 */
function PodNamePanel({
    podId,
    podName,
    canUpdate,
}: {
    podId: string;
    podName?: string | null;
    canUpdate: boolean;
}) {
    const updatePod = useUpdatePod();
    const [draft, setDraft] = useState<string | null>(null);
    const [rejection, setRejection] = useState<string | null>(null);

    // Only for the caveat below, and only for someone who can act on it — a
    // reader who cannot rename the pod has no use for the request.
    const { data: surfaces } = usePodSurfaces(canUpdate ? podId : undefined);
    // The pod's own mailbox is the agentless one — `acme@` rather than an
    // agent's `sales.acme@` — so it is the only address this panel can honestly
    // call the pod's. When only agents hold one, the caveat still applies and
    // is made without naming an address that belongs to something else.
    const podAddress = (surfaces ?? [])
        .filter((surface) => !surface.agent_id)
        .map(getSurfaceEmail)
        .find(Boolean) ?? null;
    const anyAddress = (surfaces ?? []).some((surface) => getSurfaceEmail(surface));

    const storedName = podName ?? '';
    const value = draft ?? storedName;
    // Silent until the field has been touched: a pod whose stored name predates
    // this rule should not open its settings already showing an error.
    const localError = draft === null ? null : podNameError(value);
    const changed = normalizePodName(value) !== normalizePodName(storedName);
    const problem = localError ?? rejection;

    const handleSubmit = (event: FormEvent) => {
        event.preventDefault();
        if (!canUpdate || !changed || localError) return;

        setRejection(null);
        updatePod.mutate(
            { id: podId, data: { name: normalizePodName(value) } },
            {
                onSuccess: () => {
                    // Back to the server's copy: it just became the same string,
                    // and holding the draft would keep this field out of step
                    // with a rename made anywhere else.
                    setDraft(null);
                    toast.success('Pod renamed');
                },
                // The message is the server's own sentence -- "already exists in
                // this organization" names the one thing the field could not
                // have known, and a paraphrase would lose it.
                onError: (error) => setRejection(error.message),
            },
        );
    };

    return (
        <SettingsPanel
            title="Name"
            description="What this pod is called wherever it appears — the switcher, the sidebar, and every invitation."
        >
            <form onSubmit={handleSubmit} className="max-w-2xl">
                <div className="flex items-start gap-2">
                    <Input
                        value={value}
                        onChange={(event) => {
                            setDraft(event.target.value);
                            setRejection(null);
                        }}
                        disabled={!canUpdate || updatePod.isPending}
                        aria-label="Pod name"
                        aria-invalid={Boolean(problem)}
                        aria-describedby={problem ? 'pod-name-problem' : undefined}
                        className="flex-1"
                    />
                    <Button
                        variant="primary"
                        type="submit"
                        disabled={!canUpdate || !changed || Boolean(localError) || updatePod.isPending}
                        loading={updatePod.isPending}
                        loadingLabel="Saving name"
                        className="h-10 px-4"
                    >
                        Save
                    </Button>
                </div>
                {problem ? (
                    <p id="pod-name-problem" className="mt-2 text-xs text-[var(--state-error)]">
                        {problem}
                    </p>
                ) : !canUpdate ? (
                    <SettingsHelpText className="mt-2">Your role cannot change pod settings.</SettingsHelpText>
                ) : podAddress ? (
                    // A mailbox is minted from the pod's name once, at creation,
                    // and correspondents already write to it — so a rename
                    // leaves it alone rather than breaking an address people
                    // hold. Said here because nothing else says it, and the
                    // first person to notice would otherwise be whoever renamed
                    // the pod and then went looking for a new address.
                    <SettingsHelpText className="mt-2">
                        Renaming does not move mail: this pod keeps receiving at{' '}
                        <span className="text-[var(--text-secondary)]">{podAddress}</span>.
                    </SettingsHelpText>
                ) : anyAddress ? (
                    <SettingsHelpText className="mt-2">
                        Renaming does not move mail: the addresses minted from this name keep it.
                    </SettingsHelpText>
                ) : null}
            </form>
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
