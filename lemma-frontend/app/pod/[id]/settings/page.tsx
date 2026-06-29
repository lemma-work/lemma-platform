'use client';

import { use, useState } from 'react';
import type { AgentRuntimeConfig } from 'lemma-sdk';
import { Check, Loader2 } from 'lucide-react';

import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { PodSettingsPanel, PodSettingsShell } from '@/components/pod/pod-settings-shell';
import {
    useAgentRuntimes,
    useAvailableAgentRuntimeHarnesses,
    useUpdatePodDefaultAgentRuntime,
} from '@/lib/hooks/use-agent-runtime';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod, useUpdatePod } from '@/lib/hooks/use-pods';
import { PodJoinPolicy } from '@/lib/types';
import { cn } from '@/lib/utils';

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
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
            <PodSettingsPanel
                title="Default model"
                description="Agents without a pinned model and new conversations use this model."
            >
                <RuntimeModelPicker
                    catalog={runtimeCatalog}
                    availableHarnesses={availableHarnesses}
                    defaultRuntime={runtimeCatalog?.default_runtime ?? null}
                    value={selectedRuntime}
                    onChange={handleRuntimeCommit}
                    disabled={!canUpdatePod}
                    scopeHint="Pod default"
                    manageHref={pod?.organization_id ? `/organizations/${pod.organization_id}/settings/agent-runtimes` : undefined}
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

const POD_JOIN_POLICY_OPTIONS: { value: PodJoinPolicy; label: string; description: string }[] = [
    {
        value: PodJoinPolicy.INVITE_ONLY,
        label: 'Invite only',
        description: 'People join only by invitation or an approved request.',
    },
    {
        value: PodJoinPolicy.ORG_MEMBERS,
        label: 'Organization members',
        description: 'Any member of this pod’s organization can join themselves.',
    },
    {
        value: PodJoinPolicy.PUBLIC,
        label: 'Anyone',
        description: 'Any Lemma user can join, and is added to the organization.',
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
            <div className="settings-list" role="radiogroup" aria-label="Who can join this pod">
                {POD_JOIN_POLICY_OPTIONS.map((option) => {
                    const selected = option.value === policy;
                    return (
                        <button
                            key={option.value}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            disabled={disabled}
                            onClick={() => handleChange(option.value)}
                            data-selected={selected}
                            className="settings-choice-row items-start disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            <span className="flex min-w-0 flex-col gap-0.5">
                                <span className="text-sm font-medium text-[var(--text-primary)]">{option.label}</span>
                                <span className="text-xs leading-5 text-[var(--text-tertiary)]">{option.description}</span>
                            </span>
                            <span
                                aria-hidden
                                className={cn(
                                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-gentle',
                                    selected
                                        ? 'border-[var(--state-success)] bg-[var(--state-success)] text-[var(--text-on-brand)]'
                                        : 'border-[var(--field-border)] text-transparent',
                                )}
                            >
                                <Check className="h-3 w-3" strokeWidth={3} />
                            </span>
                        </button>
                    );
                })}
            </div>
            {!canUpdate ? (
                <p className="settings-help-text mt-3">Your role cannot change pod settings.</p>
            ) : null}
        </PodSettingsPanel>
    );
}
