'use client';

import { useState } from 'react';
import { RuntimeProfileProtocol } from 'lemma-sdk';
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
import { Label } from '@/components/ui/label';
import { useCreateAgentRuntime, useUpdateAgentRuntime } from '@/lib/hooks/use-agent-runtime';
import { splitModelNames, type CustomProviderKind } from './agent-runtime-helpers';

export type ProviderDialogTarget =
    | { mode: 'connect'; kind: CustomProviderKind; name: string; baseUrl: string }
    | { mode: 'edit'; profile: AgentRuntimeProfileResponse };

// Which PATCH schema an existing profile takes. `protocol` is what the backend
// discriminates a provider profile on; `kind` distinguishes provider from
// harness and cannot tell the two provider flavours apart.
function editKind(profile: AgentRuntimeProfileResponse): CustomProviderKind {
    return profile.protocol === RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE ? 'anthropic' : 'openai';
}

// The stored base URL, which the backend returns in the clear - only key-like
// config entries are redacted. Reading it back is what lets an edit change the
// name without silently rewriting the route.
function storedBaseUrl(profile: AgentRuntimeProfileResponse): string {
    const value = (profile.config ?? {})['base_url'];
    return typeof value === 'string' ? value : '';
}

type ProviderDraft = {
    key: string;
    name: string;
    baseUrl: string;
    apiKey: string;
    clearApiKey: boolean;
    models: string;
    defaultModel: string;
};

// The dialog's identity: reopening it on a different provider must not show the
// last one's draft. Stamping the draft with this and comparing during render is
// how the rest of the app resets form state (see DestructiveConfirmationDialog)
// — an effect that calls setState would only cascade an extra render.
function draftKey(target: ProviderDialogTarget | null): string {
    if (target === null) return '';
    return target.mode === 'edit'
        ? `edit:${target.profile.id}`
        : `connect:${target.kind}:${target.name}`;
}

function freshDraft(target: ProviderDialogTarget | null): ProviderDraft {
    const base = { key: draftKey(target), apiKey: '', clearApiKey: false };
    if (target?.mode === 'edit') {
        const editing = target.profile;
        return {
            ...base,
            name: editing.name,
            baseUrl: storedBaseUrl(editing),
            models: (editing.model_catalog ?? []).map((model) => model.name).join('\n'),
            defaultModel: editing.default_model_name ?? '',
        };
    }
    return {
        ...base,
        name: target?.name ?? '',
        baseUrl: target?.baseUrl ?? '',
        models: '',
        defaultModel: '',
    };
}

export function ProviderProfileDialog({
    target,
    organizationId,
    onClose,
    onSaved,
}: {
    target: ProviderDialogTarget | null;
    organizationId: string;
    onClose: () => void;
    onSaved?: () => void;
}) {
    const isEdit = target?.mode === 'edit';
    const profile = target?.mode === 'edit' ? target.profile : null;
    const kind = target === null
        ? 'openai'
        : target.mode === 'edit'
            ? editKind(target.profile)
            : target.kind;

    const [storedDraft, setStoredDraft] = useState<ProviderDraft>(() => freshDraft(null));
    const draft = storedDraft.key === draftKey(target) ? storedDraft : freshDraft(target);
    const edit = (changes: Partial<ProviderDraft>) => setStoredDraft({ ...draft, ...changes });
    const { name, baseUrl, apiKey, clearApiKey, models, defaultModel } = draft;

    const createRuntime = useCreateAgentRuntime();
    const updateRuntime = useUpdateAgentRuntime();
    const pending = createRuntime.isPending || updateRuntime.isPending;

    const connect = async () => {
        if (target?.mode !== 'connect') return;
        const trimmedName = name.trim();
        const modelNames = splitModelNames(models);
        const defaultModelName = defaultModel.trim() || modelNames[0] || undefined;
        if (!trimmedName) return toast.error('Name this provider');
        if (kind === 'openai' && !baseUrl.trim()) return toast.error('Enter the provider base URL');
        if (kind === 'anthropic' && !apiKey.trim()) return toast.error('Enter the API key');

        await createRuntime.mutateAsync({
            organizationId,
            request: kind === 'openai'
                ? {
                    source: 'OPENAI_COMPATIBLE',
                    name: trimmedName,
                    base_url: baseUrl.trim(),
                    api_key: apiKey.trim() || null,
                    default_model_name: defaultModelName,
                    model_names: modelNames,
                }
                : {
                    source: 'ANTHROPIC_COMPATIBLE',
                    name: trimmedName,
                    base_url: baseUrl.trim() || null,
                    api_key: apiKey.trim(),
                    default_model_name: defaultModelName,
                    model_names: modelNames,
                },
        });
        toast.success(`${trimmedName} connected`);
    };

    const saveEdit = async () => {
        if (!profile) return;
        const trimmedName = name.trim();
        if (!trimmedName) return toast.error('Name this provider');

        // Only what actually changed. Sending an unchanged api_key would rotate
        // the stored credential to the same value on every save; sending an
        // empty one would clear it. Omitting the key keeps it.
        const changes: Record<string, unknown> = {};
        if (trimmedName !== profile.name) changes.name = trimmedName;

        const nextBaseUrl = baseUrl.trim();
        if (nextBaseUrl !== storedBaseUrl(profile)) {
            if (kind === 'openai' && !nextBaseUrl) return toast.error('Enter the provider base URL');
            changes.base_url = kind === 'anthropic' && !nextBaseUrl ? null : nextBaseUrl;
        }

        if (clearApiKey) {
            if (kind === 'anthropic') {
                return toast.error('An Anthropic-compatible provider needs a key. Enter a new one instead.');
            }
            changes.api_key = null;
        } else if (apiKey.trim()) {
            changes.api_key = apiKey.trim();
        }

        const modelNames = splitModelNames(models);
        const storedModels = (profile.model_catalog ?? []).map((model) => model.name);
        if (modelNames.join('\n') !== storedModels.join('\n')) changes.model_names = modelNames;

        const nextDefault = defaultModel.trim();
        if (nextDefault !== (profile.default_model_name ?? '')) {
            changes.default_model_name = nextDefault || null;
        }

        if (Object.keys(changes).length === 0) {
            onClose();
            return;
        }

        await updateRuntime.mutateAsync({
            organizationId,
            profileId: profile.id,
            request: {
                source: kind === 'openai' ? 'OPENAI_COMPATIBLE' : 'ANTHROPIC_COMPATIBLE',
                ...changes,
            } as Parameters<typeof updateRuntime.mutateAsync>[0]['request'],
        });
        toast.success(`${trimmedName} updated`);
    };

    const save = async () => {
        try {
            if (isEdit) {
                await saveEdit();
            } else {
                await connect();
            }
            onSaved?.();
            onClose();
        } catch (error) {
            toast.error(
                `Couldn't ${isEdit ? 'save' : 'connect'}: ${error instanceof Error ? error.message : 'Unknown error'}`,
            );
        }
    };

    return (
        <Dialog open={Boolean(target)} onOpenChange={(open) => { if (!open && !pending) onClose(); }}>
            <DialogContent className="gap-5">
                <DialogHeader>
                    <DialogTitle>{isEdit ? `Edit ${profile?.name}` : 'Connect a provider'}</DialogTitle>
                    <DialogDescription>
                        {isEdit
                            ? 'Everyone in this workspace uses these settings. Leave the key blank to keep the one already stored.'
                            : 'Your key is stored encrypted and shared with everyone in this workspace.'}
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-4">
                    <div className="grid gap-4 sm:grid-cols-2">
                        <DialogField label="Name">
                            <Input
                                value={name}
                                onChange={(event) => edit({ name: event.target.value })}
                                placeholder={kind === 'openai' ? 'OpenRouter' : 'Anthropic'}
                            />
                        </DialogField>
                        <DialogField label="Base URL">
                            <Input
                                value={baseUrl}
                                onChange={(event) => edit({ baseUrl: event.target.value })}
                                placeholder={kind === 'openai' ? 'https://openrouter.ai/api/v1' : 'https://api.anthropic.com'}
                            />
                        </DialogField>
                    </div>

                    <DialogField
                        label="API key"
                        hint={isEdit && profile?.has_credentials ? 'Stored — leave blank to keep it' : undefined}
                    >
                        <Input
                            type="password"
                            value={apiKey}
                            onChange={(event) => edit({ apiKey: event.target.value })}
                            disabled={clearApiKey}
                            placeholder={isEdit && profile?.has_credentials ? '••••••••' : 'sk-...'}
                        />
                    </DialogField>

                    {isEdit && profile?.has_credentials && kind === 'openai' ? (
                        <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                            <input
                                type="checkbox"
                                checked={clearApiKey}
                                onChange={(event) => edit({ clearApiKey: event.target.checked })}
                            />
                            Remove the stored key (for a provider that needs none)
                        </label>
                    ) : null}

                    <div className="grid gap-4 sm:grid-cols-2">
                        <DialogField label="Models" hint="One per line">
                            <textarea
                                value={models}
                                onChange={(event) => edit({ models: event.target.value })}
                                placeholder="one model per line"
                                className="form-field-control min-h-20 w-full resize-y px-3 py-2 text-sm leading-5 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                            />
                        </DialogField>
                        <DialogField label="Default model" hint="Optional">
                            <Input
                                value={defaultModel}
                                onChange={(event) => edit({ defaultModel: event.target.value })}
                                placeholder="First listed model is used by default"
                            />
                        </DialogField>
                    </div>
                </div>

                <DialogFooter>
                    <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={pending}>
                        Cancel
                    </Button>
                    <Button
                        type="button"
                        size="sm"
                        onClick={() => void save()}
                        loading={pending}
                        loadingLabel={isEdit ? 'Saving' : 'Connecting'}
                    >
                        {isEdit ? 'Save changes' : 'Connect'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export function DialogField({
    label,
    hint,
    children,
}: {
    label: string;
    hint?: string;
    children: React.ReactNode;
}) {
    return (
        <div className="flex flex-col gap-1.5">
            <Label className="text-[var(--text-secondary)]">
                {label}
                {hint ? <span className="ml-1 font-normal text-[var(--text-tertiary)]">{hint}</span> : null}
            </Label>
            {children}
        </div>
    );
}
