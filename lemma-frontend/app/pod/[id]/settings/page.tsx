'use client';

import Link from 'next/link';
import { use, useState } from 'react';
import type { AgentRuntimeConfig } from 'lemma-sdk';
import { Info, Loader2, Settings2 } from 'lucide-react';

import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { PodSettingsPanel, PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { SettingsChoiceList, SettingsHelpText } from '@/components/settings/settings-kit';
import {
    useAgentRuntimes,
    useAvailableAgentRuntimeHarnesses,
    useUpdatePodDefaultAgentRuntime,
} from '@/lib/hooks/use-agent-runtime';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod, useUpdatePod } from '@/lib/hooks/use-pods';
import { PodJoinPolicy } from '@/lib/types';

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
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const { data: availableHarnesses } = useAvailableAgentRuntimeHarnesses();
    const updatePodDefaultRuntime = useUpdatePodDefaultAgentRuntime();
    const [runtimeDraft, setRuntimeDraft] = useState<AgentRuntimeConfig | null>(null);

    const canUpdatePod = podAccess.can('pod.update');
    // Prefer the full stored runtime (profile + model); fall back to the legacy
    // provider-only default, resolving its model from the profile for display.
    const storedRuntime = pod?.config?.default_runtime
        ?? (pod?.config?.default_profile_id
            ? resolveDefaultAgentRuntime(runtimeCatalog, pod.config.default_profile_id, availableHarnesses)
            : null);
    const selectedRuntime = runtimeDraft ?? storedRuntime;
    const manageModelsHref = pod?.organization_id
        ? `/organizations/${pod.organization_id}/settings/agent-runtimes`
        : undefined;

    const handleRuntimeCommit = (runtime: AgentRuntimeConfig | null) => {
        setRuntimeDraft(runtime);
        updatePodDefaultRuntime.mutate({
            podId,
            runtime,
        }, {
            onSuccess: () => setRuntimeDraft(null),
        });
    };

    if (isLoadingPod) {
        return (
            <div className="context-shell flex min-h-full items-center justify-center bg-transparent">
                <div className="surface-panel px-5 py-4">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                </div>
            </div>
        );
    }

    return (
        <PodSettingsShell
            podId={podId}
            title="Pod Settings"
            description="Configure defaults that shape how this pod runs."
        >
            <div className="flex w-full max-w-3xl flex-col gap-5">
            <PodSettingsPanel
                title="Default model"
                description="Agents without a pinned model and new conversations use this model."
                action={manageModelsHref ? (
                    <Link
                        href={manageModelsHref}
                        className="inline-flex items-center gap-1.5 text-sm text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                    >
                        <Settings2 className="size-4" />
                        Manage models
                    </Link>
                ) : undefined}
            >
                <RuntimeModelPicker
                    catalog={runtimeCatalog}
                    availableHarnesses={availableHarnesses}
                    defaultRuntime={runtimeCatalog?.default_runtime ?? null}
                    value={selectedRuntime}
                    onChange={handleRuntimeCommit}
                    disabled={!canUpdatePod}
                    title="Pod default model"
                    description="Used by agents without a pinned model and by new conversations in this pod."
                    allowAuto={false}
                    scopeHint="Pod default"
                    manageHref={manageModelsHref}
                />
            </PodSettingsPanel>

            <PodJoinPolicyPanel
                podId={podId}
                currentPolicy={pod?.config?.join_policy ?? PodJoinPolicy.INVITE_ONLY}
                canUpdate={canUpdatePod}
            />
            </div>
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
        <PodSettingsPanel
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
        </PodSettingsPanel>
    );
}
