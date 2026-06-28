'use client';

import { type ReactNode } from 'react';
import Image from 'next/image';
import { Bot, Check, Loader2, RefreshCw, Terminal } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import {
    harnessLogo,
    LOCAL_RUNTIME_SETUP_COMMANDS,
    type CustomProviderKind,
    type LocalRuntimeSetupOption,
} from './agent-runtime-helpers';

export function LocalRuntimeUnavailableDetail({
    option,
    isRefreshing,
    onRefresh,
}: {
    option: LocalRuntimeSetupOption;
    isRefreshing: boolean;
    onRefresh?: () => void | Promise<void>;
}) {
    const isOffline = option.statusLabel === 'Offline';
    return (
        <div className="px-2 py-1">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        <RuntimeMark selected={false} logo={harnessLogo(option.harnessKind)} />
                        <div className="min-w-0">
                            <p className="truncate text-sm font-semibold text-[var(--text-primary)]">{option.title}</p>
                            <p className="text-xs text-[var(--text-tertiary)]">{option.statusLabel}</p>
                        </div>
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <span className="chip chip-sm chip-pill state-badge-warning">{option.statusLabel}</span>
                    {onRefresh ? (
                        <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="h-7 gap-1.5 px-2"
                            onClick={() => {
                                void onRefresh();
                            }}
                            disabled={isRefreshing}
                        >
                            {isRefreshing ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <RefreshCw className="h-3.5 w-3.5" />
                            )}
                            Recheck
                        </Button>
                    ) : null}
                </div>
            </div>

            <div className="mt-5 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] px-4 py-4">
                <div className="flex items-start gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)]">
                        <Terminal className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                        <p className="text-sm font-semibold text-[var(--text-primary)]">
                            {isOffline ? 'Local daemon is offline' : `${option.title} is not configured yet`}
                        </p>
                        <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                            {isOffline
                                ? 'Start the Lemma daemon on this machine. If this profile was saved from another device, that device needs to be online.'
                                : `Install or sign in to ${option.title}, then restart the Lemma daemon so Lemma can discover it.`}
                        </p>
                    </div>
                </div>
            </div>

            <div className="mt-4 space-y-2">
                {LOCAL_RUNTIME_SETUP_COMMANDS.map((command) => (
                    <code
                        key={command}
                        className="block rounded-md border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2 font-mono text-xs leading-5 text-[var(--text-primary)]"
                    >
                        {command}
                    </code>
                ))}
            </div>

            <p className="mt-4 text-sm leading-6 text-[var(--text-tertiary)]">
                Once it appears in this list, choose a model and save it as an Agent Runtime profile.
            </p>
        </div>
    );
}

export function HarnessChoiceRow({
    title,
    subtitle,
    selected,
    active,
    logo,
    trailing,
    onClick,
}: {
    title: string;
    subtitle?: string | null;
    selected: boolean;
    active: boolean;
    logo?: string;
    trailing?: ReactNode;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                'agent-runtime-harness-button flex min-h-11 w-full items-center gap-2 rounded-md border border-transparent px-2.5 py-2 text-left transition-gentle hover:bg-[var(--surface-1)]',
                active && 'border-[var(--row-border)] bg-[var(--surface-1)] shadow-[var(--shadow-xs)]',
                selected && 'text-[var(--text-primary)]'
            )}
        >
            <RuntimeMark selected={selected} logo={logo} />
            <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-[var(--text-primary)]">{title}</span>
                {subtitle ? (
                    <span className="block truncate text-xs text-[var(--text-tertiary)]">{subtitle}</span>
                ) : null}
            </span>
            {trailing ? <span className="shrink-0">{trailing}</span> : null}
        </button>
    );
}

export function RuntimeChoiceRow({
    title,
    subtitle,
    trailing,
    selected,
    disabled = false,
    onClick,
}: {
    title: string;
    subtitle?: string | null;
    trailing?: ReactNode;
    selected: boolean;
    disabled?: boolean;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            className={cn(
                'agent-runtime-model-button flex min-h-10 w-full items-center gap-3 rounded-md px-2.5 py-2 text-left transition-gentle hover:bg-[var(--surface-2)] disabled:cursor-not-allowed disabled:opacity-50',
                selected && 'bg-[var(--action-primary-soft)] text-[var(--text-primary)]'
            )}
        >
            <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium leading-5 text-[var(--text-primary)]">{title}</span>
                {subtitle ? (
                    <span className="block truncate font-mono text-xs leading-4 text-[var(--text-tertiary)]">{subtitle}</span>
                ) : null}
            </span>
            {trailing ? <span className="shrink-0">{trailing}</span> : null}
            <span
                className={cn(
                    'flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border',
                    selected
                        ? 'border-[var(--action-primary)] bg-[var(--action-primary)] text-[var(--text-on-brand)]'
                        : 'border-[var(--border-subtle)] text-transparent'
                )}
            >
                <Check className="h-3.5 w-3.5" />
            </span>
        </button>
    );
}

export function CustomProviderForm({
    providerTitle,
    providerKind,
    name,
    baseUrl,
    apiKey,
    modelNames,
    defaultModelName,
    isSaving,
    onNameChange,
    onBaseUrlChange,
    onApiKeyChange,
    onModelNamesChange,
    onDefaultModelNameChange,
    onSubmit,
}: {
    providerTitle: string;
    providerKind: CustomProviderKind;
    name: string;
    baseUrl: string;
    apiKey: string;
    modelNames: string;
    defaultModelName: string;
    isSaving: boolean;
    onNameChange: (value: string) => void;
    onBaseUrlChange: (value: string) => void;
    onApiKeyChange: (value: string) => void;
    onModelNamesChange: (value: string) => void;
    onDefaultModelNameChange: (value: string) => void;
    onSubmit: () => void;
}) {
    return (
        <div className="space-y-4 px-2 py-1">
            <div>
                <p className="text-sm font-semibold text-[var(--text-primary)]">{providerTitle}</p>
                <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                    Save this route and key as an Agent Runtime for the current organization.
                </p>
            </div>

            <div className="grid gap-3">
                <div className="settings-field">
                    <Label className="text-[var(--text-secondary)]">Name</Label>
                    <Input
                        value={name}
                        onChange={(event) => onNameChange(event.target.value)}
                        placeholder={providerKind === 'openai' ? 'OpenRouter' : 'Anthropic'}
                    />
                </div>

                <div className="settings-field">
                    <Label className="text-[var(--text-secondary)]">Base URL</Label>
                    <Input
                        value={baseUrl}
                        onChange={(event) => onBaseUrlChange(event.target.value)}
                        placeholder={providerKind === 'openai' ? 'https://openrouter.ai/api/v1' : 'https://api.anthropic.com'}
                    />
                </div>

                <div className="settings-field">
                    <Label className="text-[var(--text-secondary)]">API key</Label>
                    <Input
                        type="password"
                        value={apiKey}
                        onChange={(event) => onApiKeyChange(event.target.value)}
                        placeholder="sk-..."
                    />
                </div>

                <div className="settings-field">
                    <Label className="text-[var(--text-secondary)]">Models</Label>
                    <textarea
                        value={modelNames}
                        onChange={(event) => onModelNamesChange(event.target.value)}
                        placeholder="model-one&#10;model-two"
                        className="form-field-control min-h-20 w-full resize-y px-3 py-2 text-sm leading-5 text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
                    />
                    <p className="settings-help-text">One model per line or comma-separated. Leave blank when the provider supports discovery.</p>
                </div>

                <div className="settings-field">
                    <Label className="text-[var(--text-secondary)]">Default model</Label>
                    <Input
                        value={defaultModelName}
                        onChange={(event) => onDefaultModelNameChange(event.target.value)}
                        placeholder="Optional"
                    />
                </div>

            </div>

            <div className="sticky bottom-0 z-10 -mx-2 flex justify-end border-t border-[var(--border-subtle)] bg-[var(--surface-1)] px-2 py-3">
                <Button type="button" size="sm" onClick={onSubmit} loading={isSaving} disabled={isSaving}>
                    Save
                </Button>
            </div>
        </div>
    );
}

export function RuntimeMark({ selected, logo }: { selected: boolean; logo?: string }) {
    return (
        <span
            className={cn(
                'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border',
                selected
                    ? 'border-[var(--action-primary)] bg-[var(--action-primary-soft)] text-[var(--action-primary)]'
                    : 'border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)]'
            )}
        >
            {logo ? (
                <Image
                    src={logo}
                    alt=""
                    width={16}
                    height={16}
                    className="h-4 w-4 object-contain"
                />
            ) : (
                <Bot className="h-3.5 w-3.5" />
            )}
        </span>
    );
}
