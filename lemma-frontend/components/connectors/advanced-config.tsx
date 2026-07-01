'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Loader2 } from 'lucide-react';
import { buildSchemaFormPayload, buildSchemaFormValues } from 'lemma-sdk';
import { toast } from 'sonner';
import type { Connector } from '@/lib/types';
import { SchemaFields } from './schema-fields';
import {
    getAppLabel,
    getAuthConfigSchema,
    getManagedConfigCopy,
    getPrimaryProvider,
    getProviderCapability,
    getProviderDescription,
    getProviderLabel,
    getSupportedProviders,
    hasSystemDefault,
    supportsCustomConfig,
    type AuthConfigMode,
    type SchemaValues,
} from './connector-utils';

export interface AdvancedEnablePayload {
    provider: string;
    configSource: 'SYSTEM_DEFAULT' | 'ORG_CUSTOM';
    credentialConfig?: Record<string, unknown> | null;
    name?: string | null;
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
    const [provider, setProvider] = useState<string>('LEMMA');
    const [mode, setMode] = useState<AuthConfigMode>('MANAGED');
    const [showCustomForm, setShowCustomForm] = useState(false);
    const [values, setValues] = useState<SchemaValues>({});
    const [customName, setCustomName] = useState('');

    useEffect(() => {
        if (!app) return;
        const initialProvider = getPrimaryProvider(app);
        const capability = getProviderCapability(app, initialProvider);
        setProvider(initialProvider);
        setMode(hasSystemDefault(capability) ? 'MANAGED' : 'CUSTOM');
        setShowCustomForm(!hasSystemDefault(capability) && supportsCustomConfig(capability));
        setValues(buildSchemaFormValues(getAuthConfigSchema(capability)));
        setCustomName('');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [app?.id]);

    const capability = getProviderCapability(app, provider);
    const schema = getAuthConfigSchema(capability);
    const systemDefault = hasSystemDefault(capability);
    const customSupported = supportsCustomConfig(capability);
    const providers = getSupportedProviders(app);

    const handleProviderChange = (nextProvider: string) => {
        const nextCapability = getProviderCapability(app, nextProvider);
        const hasDefault = hasSystemDefault(nextCapability);
        setProvider(nextProvider);
        setMode(hasDefault ? 'MANAGED' : 'CUSTOM');
        setShowCustomForm(!hasDefault && supportsCustomConfig(nextCapability));
        setValues(buildSchemaFormValues(getAuthConfigSchema(nextCapability)));
        setCustomName('');
    };

    const canEnable = Boolean(
        app && ((mode === 'MANAGED' && systemDefault) || (mode === 'CUSTOM' && customSupported)),
    );

    const handleEnable = () => {
        if (!app) return;
        if (mode === 'MANAGED') {
            if (!systemDefault) {
                toast.error('This provider does not expose managed credentials for this app');
                return;
            }
            onEnable({ provider, configSource: 'SYSTEM_DEFAULT' });
            return;
        }

        const payload = buildSchemaFormPayload(schema, values);
        if (!payload.isValid) {
            toast.error(Object.values(payload.errors)[0] || 'Custom config is incomplete');
            return;
        }
        onEnable({
            provider,
            configSource: 'ORG_CUSTOM',
            credentialConfig: payload.data,
            name: customName.trim() || null,
        });
    };

    return (
        <Dialog open={Boolean(app)} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Advanced setup</DialogTitle>
                    <DialogDescription>
                        Choose how {getAppLabel(app)} should be authorized for this organization. Most apps work with the
                        recommended default — you only need this to use a different provider or your own credentials.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    {app && providers.length > 1 ? (
                        <div className="space-y-2">
                            <Label>Provider</Label>
                            <RadioGroup
                                value={provider}
                                onValueChange={handleProviderChange}
                                className="grid gap-2 sm:grid-cols-2"
                            >
                                {providers.map((option) => (
                                    <Label
                                        key={option}
                                        className="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3 text-[var(--text-primary)]"
                                        data-selected={provider === option}
                                    >
                                        <RadioGroupItem value={option} className="mt-0.5" />
                                        <span className="grid gap-1">
                                            <span className="text-sm font-medium text-[var(--text-primary)]">
                                                {getProviderLabel(option, getProviderCapability(app, option))}
                                            </span>
                                            <span className="text-xs leading-5 text-[var(--text-secondary)]">
                                                {getProviderDescription(option, getProviderCapability(app, option))}
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
                                <span className="text-sm font-medium text-[var(--text-primary)]">System default</span>
                                <span className="text-xs leading-5 text-[var(--text-secondary)]">
                                    {getManagedConfigCopy(provider, capability)}
                                </span>
                            </span>
                            {customSupported ? (
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    className="h-7 shrink-0 px-2 text-xs"
                                    onClick={() => {
                                        setMode('CUSTOM');
                                        setShowCustomForm(true);
                                    }}
                                >
                                    Use custom config
                                </Button>
                            ) : null}
                        </div>
                    ) : customSupported ? (
                        <div className="surface-panel-muted px-3 py-2 text-sm text-[var(--text-secondary)]">
                            Add an organization configuration to enable this app.
                        </div>
                    ) : (
                        <div className="state-surface-error rounded-lg px-3 py-3 text-sm text-[var(--text-secondary)]">
                            This provider does not have an available auth configuration yet.
                        </div>
                    )}

                    {mode === 'CUSTOM' && showCustomForm ? (
                        <div className="space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <Label>Custom configuration</Label>
                                {systemDefault ? (
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant="ghost"
                                        className="h-7 px-2 text-xs"
                                        onClick={() => {
                                            setMode('MANAGED');
                                            setShowCustomForm(false);
                                        }}
                                    >
                                        Use default
                                    </Button>
                                ) : null}
                            </div>
                            <Input
                                placeholder="Config name"
                                value={customName}
                                onChange={(event) => setCustomName(event.target.value)}
                            />
                            <SchemaFields
                                schema={schema}
                                values={values}
                                onChange={setValues}
                                emptyMessage="No custom configuration fields are available for this provider."
                            />
                        </div>
                    ) : null}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isEnabling}>
                        Cancel
                    </Button>
                    <Button onClick={handleEnable} disabled={!canEnable || isEnabling}>
                        {isEnabling ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        Enable
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
