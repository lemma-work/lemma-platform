'use client';

import { RuntimeProfileScope } from 'lemma-sdk';
import type {
    AgentHarnessInfo,
    AgentHarnessListResponse,
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse,
} from 'lemma-sdk';
import { Loader2, Plus } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import {
    availableHarnessKey,
    harnessLogo,
    modelAdvisory,
    modelPathHint,
    runtimeAvailabilityLabel,
    runtimeKey,
    runtimeModels,
    shortModelName,
    type AvailableHarnessOption,
    type RuntimeModelOption,
} from './agent-runtime-helpers';
import { RuntimeChoiceRow, RuntimeMark } from './agent-runtime-rows';

type ActiveHarnessProfile = AgentRuntimeProfileResponse & { models: RuntimeModelOption[] };
type ActiveAvailableHarness = AvailableHarnessOption;

export function RuntimeListView({
    canEdit,
    isLoading,
    runtimeCatalog,
    availableHarnesses,
    availableLocalHarnesses,
    onAdd,
    onSelectAvailableHarness,
}: {
    canEdit: boolean;
    isLoading: boolean;
    runtimeCatalog?: AgentRuntimeProfileListResponse;
    availableHarnesses?: AgentHarnessListResponse;
    availableLocalHarnesses: AgentHarnessInfo[];
    onAdd: () => void;
    onSelectAvailableHarness: (harnessKey: string) => void;
}) {
    return (
        <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h3 className="settings-title">Harnesses</h3>
                    <p className="settings-description">Saved and detected runtimes available to this workspace.</p>
                </div>
                <Button type="button" size="sm" onClick={onAdd} disabled={!canEdit || isLoading}>
                    <Plus className="mr-2 h-3.5 w-3.5" />
                    Add runtime
                </Button>
            </div>

            {isLoading ? (
                <div className="flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Loading harnesses
                </div>
            ) : null}

            {!isLoading && !(runtimeCatalog?.items?.length || availableLocalHarnesses.length) ? (
                <div className="state-surface-warning rounded-md px-3 py-2 text-sm text-[var(--text-secondary)]">
                    No harnesses are saved or detected yet. Add a provider route or start a local daemon.
                </div>
            ) : null}

            <div className="settings-list">
                {(runtimeCatalog?.items ?? []).map((profile) => {
                    const availabilityLabel = runtimeAvailabilityLabel(profile);
                    return (
                        <div
                            key={profile.id}
                            className="settings-list-row text-left"
                        >
                            <div className="flex min-w-0 items-center gap-3">
                                <RuntimeMark selected logo={harnessLogo(profile.derived_harness_kind)} />
                                <span className="min-w-0">
                                    <span className="block truncate text-sm font-semibold text-[var(--text-primary)]">{profile.name}</span>
                                    <span className="mt-0.5 block truncate text-xs text-[var(--text-tertiary)]">
                                        {runtimeModels(profile, availableHarnesses).length} model{runtimeModels(profile, availableHarnesses).length === 1 ? '' : 's'}
                                    </span>
                                </span>
                            </div>
                            <span className="flex shrink-0 items-center gap-2">
                                {availabilityLabel ? <span className="chip chip-sm chip-pill state-badge-warning">{availabilityLabel}</span> : null}
                            </span>
                        </div>
                    );
                })}

                {availableLocalHarnesses.map((harness) => {
                    const harnessKey = availableHarnessKey(harness);
                    return (
                        <button
                            key={`available-list-${harnessKey}`}
                            type="button"
                            className="agent-runtime-harness-button settings-list-row custom-focus-ring text-left"
                            onClick={() => onSelectAvailableHarness(harnessKey)}
                            disabled={!canEdit}
                        >
                            <div className="flex min-w-0 items-center gap-3">
                                <RuntimeMark selected={false} logo={harnessLogo(harness.harness_kind)} />
                                <span className="min-w-0">
                                    <span className="block truncate text-sm font-semibold text-[var(--text-primary)]">{harness.display_name}</span>
                                    <span className="mt-0.5 block truncate text-xs text-[var(--text-tertiary)]">
                                        {harness.models?.length ?? 0} model{harness.models?.length === 1 ? '' : 's'} from {harness.daemon_display_name ?? 'local daemon'}
                                    </span>
                                </span>
                            </div>
                            <span className="flex shrink-0 items-center gap-2">
                                <span className="chip chip-sm chip-pill chip-muted">Detected</span>
                                <span className="text-xs font-medium text-[var(--text-secondary)]">Add</span>
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

export function AvailableHarnessDetail({
    harness,
    localRuntimeScope,
    activeModelName,
    isSettingUp,
    isPending,
    onScopeChange,
    onSelectModel,
    onSave,
}: {
    harness: ActiveAvailableHarness;
    localRuntimeScope: RuntimeProfileScope;
    activeModelName: string | null;
    isSettingUp: boolean;
    isPending: boolean;
    onScopeChange: (scope: RuntimeProfileScope) => void;
    onSelectModel: (modelName: string) => void;
    onSave: () => void;
}) {
    return (
        <div className="space-y-4 px-2 py-1">
            <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-[var(--text-primary)]">{harness.display_name}</p>
                    <p className="text-xs text-[var(--text-tertiary)]">Detected from {harness.daemon_display_name ?? 'local daemon'}.</p>
                </div>
                {isSettingUp ? (
                    <span className="chip chip-sm chip-pill chip-muted">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        Setting up
                    </span>
                ) : null}
            </div>

            <div className="settings-field">
                <Label className="text-[var(--text-secondary)]">Visibility</Label>
                <div className="grid grid-cols-2 gap-2">
                    <button
                        type="button"
                        onClick={() => onScopeChange(RuntimeProfileScope.ORGANIZATION)}
                        className={cn(
                            'agent-runtime-scope-button rounded-md border px-3 py-2 text-left text-sm transition-gentle',
                            localRuntimeScope === RuntimeProfileScope.ORGANIZATION
                                ? 'border-[var(--action-primary)] bg-[var(--action-primary-soft)] text-[var(--text-primary)]'
                                : 'border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)] hover:bg-[var(--surface-1)]'
                        )}
                    >
                        <span className="block font-medium">Shared with org</span>
                        <span className="mt-0.5 block text-xs text-[var(--text-tertiary)]">Anyone in this org can use it.</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => onScopeChange(RuntimeProfileScope.PERSONAL)}
                        className={cn(
                            'agent-runtime-scope-button rounded-md border px-3 py-2 text-left text-sm transition-gentle',
                            localRuntimeScope === RuntimeProfileScope.PERSONAL
                                ? 'border-[var(--action-primary)] bg-[var(--action-primary-soft)] text-[var(--text-primary)]'
                                : 'border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)] hover:bg-[var(--surface-1)]'
                        )}
                    >
                        <span className="block font-medium">Only me</span>
                        <span className="mt-0.5 block text-xs text-[var(--text-tertiary)]">Visible only in your profile list.</span>
                    </button>
                </div>
            </div>

            {(harness.models ?? []).length > 0 ? (
                <div className="settings-field">
                    <Label className="text-[var(--text-secondary)]">Default model</Label>
                    <div className="space-y-1">
                        {(harness.models ?? []).map((model) => {
                            const hint = model.display_name && model.display_name !== model.name
                                ? model.name
                                : modelPathHint(model.provider_model_name ?? model.name);
                            const advisory = modelAdvisory(model);
                            return (
                                <RuntimeChoiceRow
                                    key={`${harness.harness_kind}-${model.name}`}
                                    title={model.display_name ?? shortModelName(model.name)}
                                    subtitle={hint}
                                    trailing={advisory ? (
                                        <span className="chip chip-sm chip-pill chip-muted">{advisory}</span>
                                    ) : null}
                                    selected={activeModelName === model.name}
                                    disabled={isPending}
                                    onClick={() => onSelectModel(model.name)}
                                />
                            );
                        })}
                    </div>
                </div>
            ) : (
                <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-3 py-3">
                    <p className="text-sm font-medium text-[var(--text-primary)]">No models reported yet</p>
                    <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                        You can save the runtime now and choose a model once the daemon reports it.
                    </p>
                </div>
            )}

            <div className="sticky bottom-0 z-10 -mx-2 flex justify-end border-t border-[var(--border-subtle)] bg-[var(--surface-1)] px-2 py-3 shadow-[var(--shadow-overflow-top)]">
                <Button
                    type="button"
                    size="sm"
                    loading={isSettingUp}
                    disabled={isPending}
                    onClick={onSave}
                >
                    Save Agent Runtime
                </Button>
            </div>
        </div>
    );
}

export function HarnessModelList({
    harness,
    pendingRuntime,
    pendingKey,
    onSelectRuntime,
}: {
    harness: ActiveHarnessProfile;
    pendingRuntime: AgentRuntimeConfig | null;
    pendingKey: string;
    onSelectRuntime: (runtime: AgentRuntimeConfig) => void;
}) {
    return (
        <div className="space-y-1">
            <div className="mb-2 flex items-center justify-between gap-3 px-2">
                <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-[var(--text-primary)]">{harness.name}</p>
                    <p className="text-xs text-[var(--text-tertiary)]">Choose a model for this Agent Runtime.</p>
                </div>
                {pendingRuntime?.profile_id === harness.id ? (
                    <span className="chip chip-sm chip-pill state-badge-success">Selected</span>
                ) : null}
            </div>
            {(harness.models ?? []).map((model) => {
                const modelName = model.name;
                const runtime = {
                    profile_id: harness.id,
                    model_name: modelName,
                };
                const hint = model.display_name && model.display_name !== model.name
                    ? model.name
                    : modelPathHint(model.provider_model_name ?? modelName);
                const advisory = modelAdvisory(model);
                return (
                    <RuntimeChoiceRow
                        key={runtimeKey(runtime)}
                        title={model.display_name ?? shortModelName(modelName)}
                        subtitle={hint}
                        trailing={advisory ? (
                            <span className="chip chip-sm chip-pill chip-muted">{advisory}</span>
                        ) : null}
                        selected={pendingKey === runtimeKey(runtime)}
                        onClick={() => onSelectRuntime(runtime)}
                    />
                );
            })}
        </div>
    );
}
