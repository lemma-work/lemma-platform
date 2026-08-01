'use client';

import { useMemo, useState } from 'react';
import { RuntimeProfileScope } from 'lemma-sdk';
import type { AgentRuntimeProfileResponse } from 'lemma-sdk';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    useAgentRuntimeProfile,
    useCreateAgentRuntime,
    useUpdateAgentRuntime,
    type AgentHostHarness,
} from '@/lib/hooks/use-agent-runtime';
import {
    HARNESS_DEFAULT_VALUE,
    agentHostHarnessModelCount,
    canConfigureHarnessProfile,
    harnessConfigControls,
    harnessProfileChanges,
    liveConfigSelections,
} from './agent-runtime-helpers';
import { DialogField } from './provider-profile-dialog';

export type HarnessDialogTarget =
    | { mode: 'create'; harness: AgentHostHarness }
    | { mode: 'edit'; profile: AgentRuntimeProfileResponse };

/** Model names a harness advertises through its `model` config option. */
function harnessModelNames(configOptions?: Array<Record<string, unknown>> | null): string[] {
    const names: string[] = [];
    for (const option of configOptions ?? []) {
        if (option.category !== 'model') continue;
        for (const item of (option.options as Array<Record<string, unknown>> | undefined) ?? []) {
            const value = item.value ?? item.id;
            if (typeof value === 'string' && value) names.push(value);
        }
    }
    return names;
}

type HarnessDraft = {
    key: string;
    name: string;
    description: string;
    scope: RuntimeProfileScope;
    defaultModel: string;
    selections: Record<string, string>;
};

// Same key-stamped draft as ProviderProfileDialog: reset during render rather
// than from an effect, so opening the dialog on another agent cannot show the
// previous one's settings for a frame.
function draftKey(target: HarnessDialogTarget | null): string {
    if (target === null) return '';
    return target.mode === 'edit' ? `edit:${target.profile.id}` : `create:${target.harness.id}`;
}

function freshDraft(target: HarnessDialogTarget | null): HarnessDraft {
    const key = draftKey(target);
    if (target?.mode === 'edit') {
        const editing = target.profile;
        const stored = (editing.config ?? {})['config_selections'];
        return {
            key,
            name: editing.name,
            description: editing.description ?? '',
            scope: editing.scope,
            defaultModel: editing.default_model_name ?? HARNESS_DEFAULT_VALUE,
            selections: stored && typeof stored === 'object'
                ? Object.fromEntries(
                    Object.entries(stored as Record<string, unknown>)
                        .filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
                )
                : {},
        };
    }
    return {
        key,
        name: target?.harness.display_name ?? '',
        description: '',
        scope: RuntimeProfileScope.ORGANIZATION,
        defaultModel: HARNESS_DEFAULT_VALUE,
        selections: {},
    };
}

export function HarnessProfileDialog({
    target,
    organizationId,
    onClose,
    onSaved,
}: {
    target: HarnessDialogTarget | null;
    organizationId: string;
    onClose: () => void;
    onSaved?: () => void;
}) {
    const isEdit = target?.mode === 'edit';
    const profile = target?.mode === 'edit' ? target.profile : null;

    // In edit mode the config options must come from the harness as it is *now*,
    // not as it was when the profile was saved — those are what the backend will
    // validate against and re-pin the snapshot revision to.
    const detail = useAgentRuntimeProfile(organizationId, profile?.id ?? null);
    const harness = target?.mode === 'create' ? target.harness : detail.data?.harness ?? null;

    const [storedDraft, setStoredDraft] = useState<HarnessDraft>(() => freshDraft(null));
    const draft = storedDraft.key === draftKey(target) ? storedDraft : freshDraft(target);
    const edit = (changes: Partial<HarnessDraft>) => setStoredDraft({ ...draft, ...changes });
    const { name, description, scope, defaultModel, selections } = draft;

    const createRuntime = useCreateAgentRuntime();
    const updateRuntime = useUpdateAgentRuntime();
    const pending = createRuntime.isPending || updateRuntime.isPending;

    const controls = useMemo(
        () => harnessConfigControls(harness?.config_options),
        [harness?.config_options],
    );
    const modelNames = useMemo(
        () => harnessModelNames(harness?.config_options),
        [harness?.config_options],
    );

    const save = async () => {
        const trimmedName = name.trim();
        if (!trimmedName) return toast.error('Name this agent');

        try {
            if (target?.mode === 'create') {
                await createRuntime.mutateAsync({
                    organizationId,
                    request: {
                        source: 'AGENT_HOST',
                        name: trimmedName,
                        harness_id: target.harness.id,
                        description: description.trim() || null,
                        scope,
                        default_model_name:
                            defaultModel === HARNESS_DEFAULT_VALUE ? null : defaultModel,
                        config_selections: liveConfigSelections(selections),
                    },
                });
                toast.success(`${trimmedName} is now pickable in chats`);
            } else if (profile) {
                // Only what changed. The backend reaches out to the paired
                // computer solely for an edit that touches the model or the
                // config selections, so sending them unconditionally would make
                // a rename fail whenever that machine is asleep.
                const changes = harnessProfileChanges(freshDraft(target), draft);
                if (Object.keys(changes).length === 0) {
                    onClose();
                    return;
                }
                await updateRuntime.mutateAsync({
                    organizationId,
                    profileId: profile.id,
                    request: { source: 'AGENT_HOST', ...changes },
                });
                toast.success(`${trimmedName} updated`);
            }
            onSaved?.();
            onClose();
        } catch (error) {
            toast.error(
                `Couldn't ${isEdit ? 'save' : 'add it'}: ${error instanceof Error ? error.message : 'Unknown error'}`,
            );
        }
    };

    const loadingHarness = isEdit && detail.isLoading;
    const optionCount = agentHostHarnessModelCount(harness?.config_options ?? []);

    // The model and config selections are the only two fields whose edit reaches
    // the paired computer, so they are the only two withheld when it is
    // unreachable. Renaming still saves. Creation never lands here while offline
    // — the Models page withholds the button entirely.
    const configurable = profile === null || canConfigureHarnessProfile(profile);

    return (
        <Dialog open={Boolean(target)} onOpenChange={(open) => { if (!open && !pending) onClose(); }}>
            <DialogContent className="gap-5">
                <DialogHeader>
                    <DialogTitle>{isEdit ? `Edit ${profile?.name}` : 'Add this agent to chat models'}</DialogTitle>
                    <DialogDescription>
                        {isEdit
                            ? 'These settings apply whenever this agent runs. Anything not listed here is configured on that computer.'
                            : 'It runs on the paired computer, using that machine’s own credentials.'}
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4">
                    <DialogField label="Name">
                        <Input value={name} onChange={(event) => edit({ name: event.target.value })} placeholder="Claude Code" />
                    </DialogField>

                    <DialogField label="Description" hint="Optional">
                        <Input
                            value={description}
                            onChange={(event) => edit({ description: event.target.value })}
                            placeholder="What this agent is for"
                        />
                    </DialogField>

                    {/* Scope is fixed once created: moving a profile between the
                        workspace and one person changes who it belongs to. */}
                    {!isEdit ? (
                        <DialogField label="Who can use it">
                            <Select value={scope} onValueChange={(value) => edit({ scope: value as RuntimeProfileScope })}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={RuntimeProfileScope.ORGANIZATION}>Everyone in this workspace</SelectItem>
                                    <SelectItem value={RuntimeProfileScope.PERSONAL}>Only me</SelectItem>
                                </SelectContent>
                            </Select>
                        </DialogField>
                    ) : null}

                    {loadingHarness ? (
                        <p className="text-sm text-[var(--text-tertiary)]">Reading this agent&apos;s settings…</p>
                    ) : null}

                    {!configurable ? (
                        <p className="text-sm text-[var(--text-tertiary)]">
                            That computer is offline, so its model and settings can&apos;t be changed
                            right now. The name and description still save.
                        </p>
                    ) : null}

                    {modelNames.length ? (
                        <DialogField label="Default model" hint={`${optionCount} available`}>
                            <Select
                                value={defaultModel}
                                disabled={!configurable}
                                onValueChange={(value) => edit({ defaultModel: value })}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={HARNESS_DEFAULT_VALUE}>Let the agent choose</SelectItem>
                                    {modelNames.map((model) => (
                                        <SelectItem key={model} value={model}>{model}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </DialogField>
                    ) : null}

                    {controls.map((control) => (
                        <DialogField key={control.id} label={control.label} hint={control.description ?? undefined}>
                            <Select
                                value={selections[control.selectionKey] ?? HARNESS_DEFAULT_VALUE}
                                disabled={!configurable}
                                onValueChange={(value) =>
                                    edit({ selections: { ...selections, [control.selectionKey]: value } })
                                }
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={HARNESS_DEFAULT_VALUE}>
                                        {control.currentValue
                                            ? `Use this computer's setting (${control.currentValue})`
                                            : "Use this computer's setting"}
                                    </SelectItem>
                                    {control.choices.map((choice) => (
                                        <SelectItem key={choice.value} value={choice.value}>{choice.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </DialogField>
                    ))}

                    {harness && !controls.length && !loadingHarness ? (
                        <p className="text-sm text-[var(--text-tertiary)]">
                            This agent exposes no settings Lemma can change. Configure it on that computer.
                        </p>
                    ) : null}
                </div>

                <DialogFooter>
                    <Button type="button" variant="quiet" size="sm" onClick={onClose} disabled={pending}>
                        Cancel
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        onClick={() => void save()}
                        loading={pending}
                        loadingLabel={isEdit ? 'Saving' : 'Adding'}
                    >
                        {isEdit ? 'Save changes' : 'Add to chat models'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
