'use client';

import { useEffect, useState } from 'react';
import { Check, Copy } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { config } from '@/lib/config';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { buildSchemaFormPayload, buildSchemaFormValues } from 'lemma-sdk';
import { toast } from 'sonner';
import type { Connector } from '@/lib/types';
import { CreateSlackAppButton } from './create-slack-app-button';
import { SchemaFields } from './schema-fields';
import {
    getAppLabel,
    getConfigSchema,
    getManagedConfigCopy,
    getPrimaryKind,
    getKindSpec,
    getKindDescription,
    getKindLabel,
    getSupportedKinds,
    hasSystemDefault,
    supportsCustomConfig,
    type AuthConfigMode,
    type SchemaValues,
} from './connector-utils';
import { StepLoader } from '@/components/brand/loader';

export interface AdvancedEnablePayload {
    kind: string;
    configSource: 'SYSTEM_DEFAULT' | 'ORG_CUSTOM';
    config?: Record<string, unknown> | null;
    name?: string | null;
}

/**
 * The callback URL the provider must have registered, beside the fields that
 * only work once it is.
 *
 * Your own OAuth app is a two-sided setup and the UI used to show one side: you
 * paste the app's client id and secret here, and the app has to know Lemma's
 * callback — but nothing named it, so the first sign of a mismatch was the
 * provider's own error page after the redirect. It is the same URL for every
 * connector, which is why it lives here and not in a per-connector schema.
 */
function OAuthRedirectField() {
    const [copied, setCopied] = useState(false);
    const redirectUri = `${config.API_URL.replace(/\/$/, '')}/connectors/connect-requests/oauth/callback`;

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(redirectUri);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Could not copy to clipboard');
        }
    };

    return (
        <div className="grid gap-1">
            <Label className="text-xs">Add this URL to your app</Label>
            <button
                type="button"
                onClick={() => void copy()}
                className="surface-copy-field custom-focus-ring"
                aria-label="Copy redirect URL"
            >
                <span className="min-w-0 break-all font-mono text-xs text-[var(--text-primary)]">
                    {redirectUri}
                </span>
                {copied ? (
                    <Check className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]" />
                ) : (
                    <Copy className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                )}
            </button>
            <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                It’s where you land after signing in. Without it, signing in fails on the
                other side — not here. It has to start with https.
            </p>
        </div>
    );
}

export function AdvancedConfigDialog({
    app,
    isEnabling,
    onOpenChange,
    onEnable,
}: {
    app: Connector | null;
    isEnabling: boolean;
    onOpenChange: (open: boolean) => void;
    onEnable: (payload: AdvancedEnablePayload) => void;
}) {
    const [kind, setKind] = useState<string>('package');
    const [mode, setMode] = useState<AuthConfigMode>('MANAGED');
    const [showCustomForm, setShowCustomForm] = useState(false);
    const [values, setValues] = useState<SchemaValues>({});
    const [customName, setCustomName] = useState('');

    useEffect(() => {
        if (!app) return;
        const initialKind = getPrimaryKind(app);
        const capability = getKindSpec(app, initialKind);
        setKind(initialKind);
        setMode(hasSystemDefault(capability) ? 'MANAGED' : 'CUSTOM');
        setShowCustomForm(!hasSystemDefault(capability) && supportsCustomConfig(capability));
        setValues(buildSchemaFormValues(getConfigSchema(capability)));
        setCustomName('');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [app?.id]);

    const isSlack = String(app?.id ?? '').toLowerCase() === 'slack';
    const capability = getKindSpec(app, kind);
    const schema = getConfigSchema(capability);
    const systemDefault = hasSystemDefault(capability);
    const customSupported = supportsCustomConfig(capability);
    const kinds = getSupportedKinds(app);

    const handleKindChange = (nextKind: string) => {
        const nextCapability = getKindSpec(app, nextKind);
        const hasDefault = hasSystemDefault(nextCapability);
        setKind(nextKind);
        setMode(hasDefault ? 'MANAGED' : 'CUSTOM');
        setShowCustomForm(!hasDefault && supportsCustomConfig(nextCapability));
        setValues(buildSchemaFormValues(getConfigSchema(nextCapability)));
        setCustomName('');
    };

    const canEnable = Boolean(
        app && ((mode === 'MANAGED' && systemDefault) || (mode === 'CUSTOM' && customSupported)),
    );

    const handleEnable = () => {
        if (!app) return;
        if (mode === 'MANAGED') {
            if (!systemDefault) {
                toast.error(`Lemma has no sign-in of its own for ${getAppLabel(app)}`);
                return;
            }
            onEnable({ kind, configSource: 'SYSTEM_DEFAULT' });
            return;
        }

        const payload = buildSchemaFormPayload(schema, values);
        if (!payload.isValid) {
            toast.error(Object.values(payload.errors)[0] || 'Fill in the fields above first');
            return;
        }
        onEnable({
            kind,
            configSource: 'ORG_CUSTOM',
            config: payload.data,
            name: customName.trim() || null,
        });
    };

    return (
        <Dialog open={Boolean(app)} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Advanced setup</DialogTitle>
                    <DialogDescription>
                        How {getAppLabel(app)} signs in for your team. The default works for
                        almost everyone — you only need this if you want to use your own app.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    {app && kinds.length > 1 ? (
                        <div className="space-y-2">
                            <Label>Kind</Label>
                            <RadioGroup
                                value={kind}
                                onValueChange={handleKindChange}
                                className="grid gap-2 sm:grid-cols-2"
                            >
                                {kinds.map((option) => (
                                    <Label
                                        key={option}
                                        className="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3 text-[var(--text-primary)]"
                                        data-selected={kind === option}
                                    >
                                        <RadioGroupItem value={option} className="mt-0.5" />
                                        <span className="grid gap-1">
                                            <span className="text-sm font-medium text-[var(--text-primary)]">
                                                {getKindLabel(option, getKindSpec(app, option))}
                                            </span>
                                            <span className="text-xs leading-5 text-[var(--text-secondary)]">
                                                {getKindDescription(option, getKindSpec(app, option))}
                                            </span>
                                        </span>
                                    </Label>
                                ))}
                            </RadioGroup>
                        </div>
                    ) : null}

                    {systemDefault ? (
                        <div className="flex items-start justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
                            <span className="grid gap-1">
                                <span className="text-sm font-medium text-[var(--text-primary)]">Use Lemma’s</span>
                                <span className="text-xs leading-5 text-[var(--text-secondary)]">
                                    {getManagedConfigCopy(kind, capability)}
                                </span>
                            </span>
                            {customSupported ? (
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="quiet"
                                    className="h-7 shrink-0 px-2 text-xs"
                                    onClick={() => {
                                        setMode('CUSTOM');
                                        setShowCustomForm(true);
                                    }}
                                >
                                    Use my own
                                </Button>
                            ) : null}
                        </div>
                    ) : customSupported ? (
                        <div className="surface-panel-muted px-3 py-2 text-sm text-[var(--text-secondary)]">
                            Add your own app details to turn this on.
                        </div>
                    ) : (
                        <div className="state-surface-error rounded-lg px-3 py-3 text-sm text-[var(--text-secondary)]">
                            There’s no way to sign in to this yet.
                        </div>
                    )}

                    {mode === 'CUSTOM' && showCustomForm ? (
                        <div className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <Label>Your own app</Label>
                                {systemDefault ? (
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant="quiet"
                                        className="h-7 px-2 text-xs"
                                        onClick={() => {
                                            setMode('MANAGED');
                                            setShowCustomForm(false);
                                        }}
                                    >
                                        Use Lemma’s
                                    </Button>
                                ) : null}
                            </div>
                            {isSlack ? <CreateSlackAppButton /> : null}
                            <Input
                                placeholder="Give it a name"
                                value={customName}
                                onChange={(event) => setCustomName(event.target.value)}
                            />
                            <SchemaFields
                                schema={schema}
                                values={values}
                                onChange={setValues}
                                emptyMessage="There’s nothing else to fill in."
                            />
                            {/* The manifest already registered this URL, so on Slack
                                it is a reference rather than an instruction. Every
                                other connector still has to be told. */}
                            {isSlack ? null : <OAuthRedirectField />}
                        </div>
                    ) : null}
                </div>
                <DialogFooter>
                    <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={isEnabling}>
                        Cancel
                    </Button>
                    <Button variant="primary" onClick={handleEnable} disabled={!canEnable || isEnabling}>
                        {isEnabling ? <StepLoader size="sm" className="mr-2" /> : null}
                        Enable
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
