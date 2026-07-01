'use client';

import {
    useAccounts,
    useConnectors,
    useAuthConfigs,
    useCreateConnectRequest,
    useCreateConnectorAccount,
    useDeleteAccount,
    useEnableConnector,
} from '@/lib/hooks/use-connectors';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { Input } from '@/components/ui/input';
import { CheckCircle2, Loader2, Plug, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import type { Account, Connector } from '@/lib/types';
import { useOrganization } from '@/components/dashboard/org-context';
import { ConnectorGrid } from './connector-grid';
import { ConnectedAccountCard } from './connector-card';
import { ConnectAccountDialog, type CredentialTarget } from './connect-account-dialog';
import { AdvancedConfigDialog, type AdvancedEnablePayload } from './advanced-config';
import {
    findAuthConfigForAccount,
    getAccountStatusMeta,
    getAppLabel,
    getPrimaryCapability,
    getProviderCapability,
    hasSystemDefault,
    usesDirectCredentials,
    type ProviderCapability,
} from './connector-utils';

interface ConnectorsViewProps {
    organizationId?: string;
    organizationName?: string;
    embedded?: boolean;
    showHeader?: boolean;
}

const openAuthorization = (url?: string | null) => {
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
};

export function ConnectorsView({ organizationId, organizationName, embedded = false, showHeader = true }: ConnectorsViewProps) {
    const { currentOrg, organizations } = useOrganization();
    const effectiveOrganizationId = organizationId || currentOrg?.id;
    const effectiveOrganizationName =
        organizationName ||
        organizations.find((org) => org.id === effectiveOrganizationId)?.name ||
        currentOrg?.name;

    const { data: accounts, isLoading: isLoadingAccounts } = useAccounts({ organizationId: effectiveOrganizationId, limit: 200 });
    const { data: authConfigs, isLoading: isLoadingAuthConfigs } = useAuthConfigs({ organizationId: effectiveOrganizationId, limit: 200 });
    const { data: connectors, isLoading: isLoadingApps } = useConnectors({ limit: 200 });
    const deleteAccount = useDeleteAccount(effectiveOrganizationId);
    const enableConnector = useEnableConnector(effectiveOrganizationId);
    const createConnectRequest = useCreateConnectRequest(effectiveOrganizationId);
    const createConnectorAccount = useCreateConnectorAccount(effectiveOrganizationId);

    const [searchTerm, setSearchTerm] = useState('');
    const [busyAppId, setBusyAppId] = useState<string | null>(null);
    const [reconnectAccountId, setReconnectAccountId] = useState<string | null>(null);
    const [deletingAccountId, setDeletingAccountId] = useState<string | null>(null);
    const [advancedApp, setAdvancedApp] = useState<Connector | null>(null);
    const [isEnabling, setIsEnabling] = useState(false);
    const [credentialTarget, setCredentialTarget] = useState<CredentialTarget | null>(null);
    const [isSubmittingCredentials, setIsSubmittingCredentials] = useState(false);
    const [accountPendingDisconnect, setAccountPendingDisconnect] = useState<{
        id: string;
        appName: string;
        accountLabel: string;
    } | null>(null);

    const connectorsById = useMemo(
        () => new Map((connectors || []).map((connector) => [connector.id, connector])),
        [connectors],
    );

    const enabledConfigByAppId = useMemo(
        () =>
            new Map(
                (authConfigs || [])
                    .filter((config) => config.status === 'ACTIVE')
                    .map((config) => [config.connector_id, config]),
            ),
        [authConfigs],
    );

    const connectedAppIds = useMemo(
        () => new Set((accounts || []).map((account) => account.connector_id)),
        [accounts],
    );

    const filteredApps = useMemo(() => {
        const query = searchTerm.toLowerCase();
        const matches = (connectors || []).filter(
            (app) =>
                (app.title && app.title.toLowerCase().includes(query)) ||
                (app.name && app.name.toLowerCase().includes(query)) ||
                (app.description && app.description.toLowerCase().includes(query)),
        );
        // Float connected connectors to the top, then enabled ones, keeping the
        // original order stable within each group.
        const rank = (app: Connector) =>
            connectedAppIds.has(app.id) ? 0 : enabledConfigByAppId.has(app.id) ? 1 : 2;
        return matches
            .map((app, index) => ({ app, index }))
            .sort((a, b) => rank(a.app) - rank(b.app) || a.index - b.index)
            .map((entry) => entry.app);
    }, [connectors, searchTerm, connectedAppIds, enabledConfigByAppId]);

    const attentionCount = useMemo(
        () => (accounts || []).filter((account) => getAccountStatusMeta(account.status).needsAttention).length,
        [accounts],
    );

    const openCredentialDialog = (
        app: Connector,
        capability: ProviderCapability | null,
        authConfigId: string | null,
        mode: 'connect' | 'reconnect' = 'connect',
        accountId?: string,
    ) => {
        setCredentialTarget({ connector: app, capability, authConfigId, mode, accountId });
    };

    // OAuth needs a round-trip to fetch the authorization URL before we can act.
    const startOAuth = async (connectorId: string, authConfigId: string) => {
        const response = await createConnectRequest.mutateAsync({ connectorId, authConfigId });
        openAuthorization(response.authorization_url);
    };

    const handleConnect = async (app: Connector) => {
        const existing = enabledConfigByAppId.get(app.id) ?? null;
        const capability = existing ? getProviderCapability(app, existing.provider) : getPrimaryCapability(app);
        if (!capability) {
            toast.error('This connector is not available yet');
            return;
        }

        // Credential apps: open the form immediately so keystrokes land in the field,
        // not the page. Enabling (if needed) is deferred to submit time.
        if (usesDirectCredentials(capability)) {
            if (!existing && !hasSystemDefault(capability)) {
                setAdvancedApp(app);
                return;
            }
            openCredentialDialog(app, capability, existing?.id ?? null, 'connect');
            return;
        }

        // OAuth apps: auto-enable the managed default (if needed), then open the flow.
        setBusyAppId(app.id);
        try {
            let authConfig = existing;
            if (!authConfig) {
                if (!hasSystemDefault(capability)) {
                    setAdvancedApp(app);
                    return;
                }
                authConfig = await enableConnector.mutateAsync({
                    connectorId: app.id,
                    provider: capability.provider,
                    configSource: 'SYSTEM_DEFAULT',
                });
            }
            await startOAuth(app.id, authConfig.id);
        } catch (error) {
            console.error('Failed to connect:', error);
            toast.error('Failed to connect');
        } finally {
            setBusyAppId(null);
        }
    };

    const handleAdvancedEnable = async (payload: AdvancedEnablePayload) => {
        if (!advancedApp) return;
        const app = advancedApp;
        setIsEnabling(true);
        try {
            const authConfig = await enableConnector.mutateAsync({
                connectorId: app.id,
                provider: payload.provider,
                configSource: payload.configSource,
                credentialConfig: payload.credentialConfig,
                name: payload.name,
            });
            toast.success('Connector enabled');
            setAdvancedApp(null);

            const capability = getProviderCapability(app, authConfig.provider);
            if (usesDirectCredentials(capability)) {
                openCredentialDialog(app, capability, authConfig.id, 'connect');
                return;
            }
            await startOAuth(app.id, authConfig.id);
        } catch (error) {
            console.error('Failed to enable connector:', error);
            toast.error('Failed to enable connector');
        } finally {
            setIsEnabling(false);
        }
    };

    const handleReconnect = async (account: Account) => {
        const app = connectorsById.get(account.connector_id) ?? (account.connector as Connector | undefined) ?? null;
        const authConfig = findAuthConfigForAccount(account, authConfigs);
        if (!app || !authConfig) {
            toast.error('Unable to reconnect this account');
            return;
        }
        const capability = getProviderCapability(app, authConfig.provider);

        // Credential accounts re-link via the form (delete + recreate). OAuth accounts
        // re-run the flow on the same account_id — the backend only blocks CONNECTED.
        if (usesDirectCredentials(capability)) {
            openCredentialDialog(app, capability, authConfig.id, 'reconnect', account.id);
            return;
        }

        setReconnectAccountId(account.id);
        try {
            await startOAuth(account.connector_id, authConfig.id);
        } catch (error) {
            console.error('Failed to reconnect:', error);
            toast.error('Failed to start reconnect');
        } finally {
            setReconnectAccountId(null);
        }
    };

    const handleCredentialSubmit = async (data: Record<string, unknown>) => {
        const target = credentialTarget;
        if (!target) return;
        setIsSubmittingCredentials(true);
        try {
            // Enable the managed default now if the org hasn't configured this connector yet.
            let authConfigId = target.authConfigId;
            if (!authConfigId) {
                if (!target.capability || !hasSystemDefault(target.capability)) {
                    throw new Error('Connector is not configured for direct credentials');
                }
                const authConfig = await enableConnector.mutateAsync({
                    connectorId: target.connector.id,
                    provider: target.capability.provider,
                    configSource: 'SYSTEM_DEFAULT',
                });
                authConfigId = authConfig.id;
            }

            if (target.mode === 'reconnect' && target.accountId) {
                await deleteAccount.mutateAsync(target.accountId);
            }
            await createConnectorAccount.mutateAsync({ authConfigId, credentials: data });
            toast.success(`${getAppLabel(target.connector)} ${target.mode === 'reconnect' ? 'reconnected' : 'connected'}`);
            setCredentialTarget(null);
        } catch (error) {
            console.error('Failed to save credentials:', error);
            toast.error('Failed to save credentials');
        } finally {
            setIsSubmittingCredentials(false);
        }
    };

    const handleDisconnect = async () => {
        if (!accountPendingDisconnect) return;
        try {
            setDeletingAccountId(accountPendingDisconnect.id);
            await deleteAccount.mutateAsync(accountPendingDisconnect.id);
            toast.success(`${accountPendingDisconnect.appName} disconnected`);
            setAccountPendingDisconnect(null);
        } catch (error) {
            console.error('Failed to disconnect account:', error);
            toast.error('Failed to disconnect account');
        } finally {
            setDeletingAccountId(null);
        }
    };

    if (!effectiveOrganizationId) {
        return (
            <EmptyState
                variant="panel"
                icon={<Plug className="h-5 w-5" />}
                title="Select an organization"
                description="Connectors are enabled and connected inside an organization."
            />
        );
    }

    if (isLoadingAccounts || isLoadingApps || isLoadingAuthConfigs) {
        return (
            <div className={embedded ? 'flex min-h-[30vh] items-center justify-center bg-transparent' : 'context-shell flex min-h-full items-center justify-center bg-transparent pb-8'}>
                <Loader2 className="h-8 w-8 animate-spin text-[var(--text-tertiary)]" />
            </div>
        );
    }

    const searchField = (
        <div className="relative w-full max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
            <Input
                type="search"
                name="connector-search"
                autoComplete="off"
                data-1p-ignore
                data-lpignore="true"
                placeholder="Search apps"
                className="pl-9"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
            />
        </div>
    );

    return (
        <div className={embedded ? 'min-h-full bg-transparent' : 'context-shell min-h-full bg-transparent pb-8'}>
            {showHeader ? (
                <>
                    <div className="context-header">
                        <div>
                            <p className="section-label">Connectors</p>
                            <h1 className="font-display text-4xl font-normal text-[var(--text-primary)]">Connectors</h1>
                            <p className="mt-2 max-w-2xl text-sm text-[var(--text-secondary)]">
                                Connect the apps you use, and they’re available to you across every pod in {effectiveOrganizationName || 'this organization'}.
                            </p>
                        </div>
                        {searchField}
                    </div>
                    <p className="context-inline-note">
                        Click Connect and we’ll set up the recommended integration automatically. Use Advanced only to pick a
                        different provider or your own credentials.
                    </p>
                </>
            ) : (
                <div className="mb-8 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <p className="max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">
                        Connect the apps you use, and they’re available to you across every pod in {effectiveOrganizationName || 'this organization'}.
                    </p>
                    {searchField}
                </div>
            )}

            {accounts && accounts.length > 0 && (
                <section className="context-section">
                    <div className="mb-3 flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 text-[var(--state-success)]" />
                        <h2 className="text-base font-normal text-[var(--text-primary)]">Your accounts</h2>
                        <span className="text-xs text-[var(--text-tertiary)]">{accounts.length}</span>
                        {attentionCount > 0 ? (
                            <span className="text-xs font-medium text-[var(--state-warning)]">
                                · {attentionCount} need{attentionCount === 1 ? 's' : ''} attention
                            </span>
                        ) : null}
                    </div>
                    <div className="resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
                        {accounts.map((account) => (
                            <ConnectedAccountCard
                                key={account.id}
                                account={account}
                                isBusy={reconnectAccountId === account.id || deletingAccountId === account.id}
                                onReconnect={handleReconnect}
                                onDisconnect={(acc) =>
                                    setAccountPendingDisconnect({
                                        id: acc.id,
                                        appName: acc.connector?.title || acc.connector?.name || 'this app',
                                        accountLabel: acc.email || acc.connector?.title || acc.connector?.name || 'Connected account',
                                    })
                                }
                            />
                        ))}
                    </div>
                </section>
            )}

            <section>
                <div className="mb-3 flex items-center gap-2">
                    <Plug className="h-4 w-4 text-[var(--text-tertiary)]" />
                    <h2 className="text-base font-normal text-[var(--text-primary)]">All connectors</h2>
                    <span className="text-xs text-[var(--text-tertiary)]">{filteredApps.length}</span>
                </div>
                <ConnectorGrid
                    connectors={filteredApps}
                    connectedAppIds={connectedAppIds}
                    busyAppId={busyAppId}
                    searchTerm={searchTerm}
                    onConnect={handleConnect}
                    onAdvanced={setAdvancedApp}
                />
            </section>

            <AdvancedConfigDialog
                app={advancedApp}
                isEnabling={isEnabling}
                onOpenChange={(open) => {
                    if (!open) setAdvancedApp(null);
                }}
                onEnable={handleAdvancedEnable}
            />

            <ConnectAccountDialog
                target={credentialTarget}
                isSubmitting={isSubmittingCredentials}
                onOpenChange={(open) => {
                    if (!open) setCredentialTarget(null);
                }}
                onSubmit={handleCredentialSubmit}
            />

            <DestructiveConfirmationDialog
                open={Boolean(accountPendingDisconnect)}
                onOpenChange={(open) => {
                    if (!open) setAccountPendingDisconnect(null);
                }}
                title="Disconnect connector"
                description={`Disconnect ${accountPendingDisconnect?.appName ?? 'this connector'}? This revokes the account connection.`}
                resourceName={accountPendingDisconnect?.accountLabel ?? 'connected account'}
                confirmationText="disconnect"
                consequences={[
                    'Agents and workflows using this account will lose access.',
                    'You can reconnect the app later, but existing runs may fail until access is restored.',
                ]}
                confirmLabel="Disconnect"
                pendingLabel="Disconnecting..."
                isPending={Boolean(deletingAccountId)}
                onConfirm={() => void handleDisconnect()}
            />
        </div>
    );
}
