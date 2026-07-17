'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ExternalLink, Loader2, Plug, Plus, X } from '@/components/ui/icons';
import { buildSchemaFormPayload, buildSchemaFormValues } from 'lemma-sdk';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
    useAccounts,
    useAuthConfigs,
    useConnectors,
    useCreateConnectorAccount,
    useCreateConnectRequest,
    useEnableConnector,
} from '@/lib/hooks/use-connectors';
import { SchemaFields } from '@/components/connectors/schema-fields';
import {
    getAccountStatusMeta,
    getCredentialSchema,
    getPrimaryCapability,
    getProviderCapability,
    hasSystemDefault,
    schemaHasFields,
    usesDirectCredentials,
    type SchemaValues,
} from '@/components/connectors/connector-utils';
import type { Account, Connector } from '@/lib/types';

interface AccountVariableFieldProps {
    organizationId?: string;
    podId?: string | null;
    /** The connector this variable needs an account for (e.g. "slack"),
     * matched against the connector catalog's name/title/slug. Named
     * `connectorId` (not `connector`) to avoid shadowing the resolved
     * Connector entity this component looks up below. */
    connectorId: string;
    /** Auth provider ("LEMMA" or "COMPOSIO") the bundle needs for this
     * connector. When set, only accounts/auth configs of that provider are
     * offered — an org can have both a native and a Composio-backed auth
     * config for the same connector, and only one is the right fit here. */
    provider?: string | null;
    label: string;
    description?: string | null;
    required?: boolean;
    value: string;
    onChange: (value: string) => void;
}

function matchesConnector(connector: Connector, connectorId: string): boolean {
    const p = connectorId.toLowerCase();
    const slug = (connector as { slug?: string }).slug;
    return (
        connector.name?.toLowerCase() === p ||
        connector.title?.toLowerCase() === p ||
        slug?.toLowerCase() === p
    );
}

function accountLabel(account: Account): string {
    return account.display_name || account.email || account.provider_account_id || 'Connected account';
}

export function AccountVariableField({
    organizationId,
    podId,
    connectorId,
    provider,
    label,
    description,
    required,
    value,
    onChange,
}: AccountVariableFieldProps) {
    const { data: connectors = [] } = useConnectors({ limit: 200 });
    const connector = useMemo(
        () => connectors.find((c) => matchesConnector(c, connectorId)) ?? null,
        [connectors, connectorId],
    );

    const { data: accountsForConnector = [], refetch } = useAccounts({
        organizationId,
        connectorId: connector?.id,
        limit: 100,
        enabled: Boolean(organizationId && connector),
    });
    // An org can have both a native and a Composio-backed auth config for the
    // same connector; only accounts through the provider this variable needs
    // are valid picks.
    const accounts = useMemo(
        () =>
            provider
                ? accountsForConnector.filter((a) => !a.provider || a.provider === provider)
                : accountsForConnector,
        [accountsForConnector, provider],
    );
    const { data: authConfigsForConnector = [] } = useAuthConfigs({
        organizationId,
        limit: 200,
        enabled: Boolean(organizationId && connector),
    });
    const authConfigs = useMemo(
        () =>
            provider
                ? authConfigsForConnector.filter((cfg) => cfg.provider === provider)
                : authConfigsForConnector,
        [authConfigsForConnector, provider],
    );
    const enableConnector = useEnableConnector(organizationId);
    const createConnectRequest = useCreateConnectRequest(organizationId);
    const createConnectorAccount = useCreateConnectorAccount(organizationId);

    // When the variable pins a provider, connect/create through that specific
    // capability instead of the connector's default (e.g. Composio-first) pick.
    const capability = useMemo(
        () => (provider ? getProviderCapability(connector, provider) : getPrimaryCapability(connector)),
        [connector, provider],
    );
    const credentialSchema = useMemo(() => getCredentialSchema(capability), [capability]);
    const isOAuth = Boolean(capability && hasSystemDefault(capability) && !usesDirectCredentials(capability));
    const canCredential = usesDirectCredentials(capability);

    const [awaitingOAuth, setAwaitingOAuth] = useState(false);
    const [showCredentials, setShowCredentials] = useState(false);
    const [credValues, setCredValues] = useState<SchemaValues>({});
    const [submitting, setSubmitting] = useState(false);

    const seenIdsRef = useRef<Set<string>>(new Set());
    const onChangeRef = useRef(onChange);
    useEffect(() => {
        onChangeRef.current = onChange;
    }, [onChange]);

    // Convenience: a single existing account satisfies a required field on its own.
    useEffect(() => {
        if (!value && accounts.length === 1) onChangeRef.current(accounts[0].id);
    }, [accounts, value]);

    // While an OAuth tab is open, poll for the new account and auto-select it.
    useEffect(() => {
        if (!awaitingOAuth) return;
        const started = Date.now();
        const timer = setInterval(async () => {
            const res = await refetch();
            const items = (res.data ?? []) as Account[];
            const fresh = items.find((a) => !seenIdsRef.current.has(a.id));
            if (fresh) {
                onChangeRef.current(fresh.id);
                setAwaitingOAuth(false);
                toast.success(`Connected ${connectorId}`);
            } else if (Date.now() - started > 120_000) {
                setAwaitingOAuth(false);
            }
        }, 2500);
        return () => clearInterval(timer);
    }, [awaitingOAuth, refetch, connectorId]);

    async function resolveAuthConfigId(): Promise<string> {
        const active = authConfigs.find((cfg) => cfg.connector_id === connector!.id && cfg.status === 'ACTIVE');
        if (active) return active.id;
        if (!capability || !hasSystemDefault(capability)) {
            throw new Error('This connector needs setup in Connectors first.');
        }
        const authConfig = await enableConnector.mutateAsync({
            connectorId: connector!.id,
            provider: capability.provider,
            configSource: 'SYSTEM_DEFAULT',
        });
        return authConfig.id;
    }

    async function startOAuth() {
        if (!connector) return;
        seenIdsRef.current = new Set(accounts.map((a) => a.id));
        setAwaitingOAuth(true);
        try {
            const authConfigId = await resolveAuthConfigId();
            const resp = await createConnectRequest.mutateAsync({ connectorId: connector.id, authConfigId });
            if (resp.authorization_url) {
                window.open(resp.authorization_url, '_blank', 'noopener,noreferrer');
            }
        } catch (error) {
            setAwaitingOAuth(false);
            toast.error(error instanceof Error ? error.message : `Could not connect ${connectorId}`);
        }
    }

    async function submitCredentials() {
        if (!connector) return;
        const payload = buildSchemaFormPayload(credentialSchema, credValues);
        if (!payload.isValid) {
            toast.error(Object.values(payload.errors)[0] || 'Credentials are incomplete');
            return;
        }
        setSubmitting(true);
        try {
            const authConfigId = await resolveAuthConfigId();
            const account = (await createConnectorAccount.mutateAsync({
                authConfigId,
                credentials: payload.data,
            })) as { id?: string };
            await refetch();
            if (account?.id) onChange(account.id);
            setShowCredentials(false);
            toast.success(`Connected ${connectorId}`);
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Could not save credentials');
        } finally {
            setSubmitting(false);
        }
    }

    function handleConnectClick() {
        if (isOAuth) {
            void startOAuth();
            return;
        }
        if (canCredential) {
            // No fields to fill (e.g. NOAUTH) → connect directly.
            if (!schemaHasFields(credentialSchema)) {
                void submitCredentials();
                return;
            }
            setCredValues(buildSchemaFormValues(credentialSchema));
            setShowCredentials(true);
            return;
        }
        // Fallback: unknown auth style — let them set it up in Connectors.
        if (podId) window.open(`/pod/${podId}/connectors`, '_blank', 'noopener,noreferrer');
    }

    const title = connectorId.charAt(0).toUpperCase() + connectorId.slice(1);

    return (
        <div className="space-y-2">
            <div>
                <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
                    <Plug className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />
                    {label}
                    {required ? <span className="text-[var(--state-error)]">*</span> : null}
                </div>
                {description ? (
                    <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{description}</p>
                ) : null}
            </div>

            {/* No matching connector in the catalog — never block; let them paste an id. */}
            {!connector ? (
                <Input
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={`${connectorId} account id`}
                />
            ) : (
                <div className="space-y-1.5">
                    {accounts.map((account) => {
                        const selected = value === account.id;
                        const meta = getAccountStatusMeta(account.status);
                        return (
                            <button
                                key={account.id}
                                type="button"
                                onClick={() => onChange(account.id)}
                                className={cn(
                                    'flex w-full items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors',
                                    selected
                                        ? 'border-[var(--action-primary)] bg-[var(--surface-2)]'
                                        : 'border-[var(--border-subtle)] hover:bg-[var(--surface-2)]',
                                )}
                            >
                                <span
                                    className={cn(
                                        'flex h-4 w-4 shrink-0 items-center justify-center rounded-full border',
                                        selected
                                            ? 'border-[var(--action-primary)] bg-[var(--action-primary)] text-[var(--button-primary-fg)]'
                                            : 'border-[var(--border-strong)]',
                                    )}
                                >
                                    {selected ? <Check className="h-3 w-3" /> : null}
                                </span>
                                <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                                    {accountLabel(account)}
                                </span>
                                <span
                                    className={cn(
                                        'shrink-0 text-xs font-medium uppercase',
                                        meta.needsAttention
                                            ? 'text-[var(--state-warning)]'
                                            : 'text-[var(--text-tertiary)]',
                                    )}
                                >
                                    {meta.label}
                                </span>
                            </button>
                        );
                    })}

                    {/* Inline credential form (bot token / API key) — no new tab. */}
                    {showCredentials ? (
                        <div className="space-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-3">
                            <div className="flex items-center justify-between">
                                <span className="text-xs font-medium text-[var(--text-secondary)]">
                                    Connect {title}
                                </span>
                                <button
                                    type="button"
                                    onClick={() => setShowCredentials(false)}
                                    className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                                    aria-label="Cancel"
                                >
                                    <X className="h-3.5 w-3.5" />
                                </button>
                            </div>
                            <SchemaFields
                                schema={credentialSchema}
                                values={credValues}
                                onChange={setCredValues}
                                emptyMessage="No credentials are required for this app."
                                autoFocusFirst
                            />
                            <Button
                                type="button"
                                size="sm"
                                className="w-full"
                                onClick={submitCredentials}
                                loading={submitting}
                                loadingLabel="Connecting…"
                            >
                                Connect
                            </Button>
                        </div>
                    ) : awaitingOAuth ? (
                        <div className="flex items-center justify-between gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-secondary)]">
                            <span className="flex items-center gap-2">
                                <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--action-primary)]" />
                                Waiting for {title} authorization…
                            </span>
                            <button
                                type="button"
                                onClick={() => setAwaitingOAuth(false)}
                                className="text-xs text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                            >
                                Cancel
                            </button>
                        </div>
                    ) : (
                        <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="w-full"
                            onClick={handleConnectClick}
                        >
                            {accounts.length > 0 ? (
                                <Plus className="mr-2 h-3.5 w-3.5" />
                            ) : isOAuth ? (
                                <ExternalLink className="mr-2 h-3.5 w-3.5" />
                            ) : (
                                <Plug className="mr-2 h-3.5 w-3.5" />
                            )}
                            {accounts.length > 0 ? `Connect another ${title} account` : `Connect ${title}`}
                        </Button>
                    )}
                </div>
            )}
        </div>
    );
}
