'use client';

import {
    useAccounts,
    useConnectors,
    useAuthConfigs,
    useCreateConnectRequest,
    useCreateConnectorAccount,
    useDeleteAccount,
    useDeleteAuthConfig,
    useEnableConnector,
    useRefreshAuthConfigOperations,
    useUpdateAuthConfig,
} from '@/lib/hooks/use-connectors';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { Input } from '@/components/ui/input';
import { Plug, Search } from '@/components/ui/icons';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import type { Account, AuthConfig, Connector } from '@/lib/types';
import { useOrganization } from '@/components/dashboard/org-context';
import { ResourceCardGridSkeleton } from '@/components/shared/loading';
import { ConnectorGrid } from './connector-grid';
import { ConnectorMosaic } from './connector-mosaic';
import { ConnectedAccountRow } from './connector-card';
import { AddYourOwnRow, ConnectionRow } from './connection-rows';
import { ConnectAccountDialog, type CredentialTarget } from './connect-account-dialog';
import { AddConnectionDialog, type ConnectionSubmission, type ConnectionTarget } from './add-connection-dialog';
import { AdvancedConfigDialog, type AdvancedEnablePayload } from './advanced-config';
import {
    canConnectWithDefaults,
    describeConnectorError,
    findAuthConfigForAccount,
    getAccountStatusMeta,
    getAppLabel,
    getInstallLabel,
    getPrimaryKindSpec,
    getKindSpec,
    getTenantConfiguredConnectors,
    getTenantConfiguredKindSpec,
    isTenantConfigured,
    usesDirectCredentials,
    type ConnectorKindSpec,
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

    const { data: accounts, isLoading: isLoadingAccounts, refetch: refetchAccounts } = useAccounts({ organizationId: effectiveOrganizationId, limit: 200 });
    const { data: authConfigs, isLoading: isLoadingAuthConfigs } = useAuthConfigs({ organizationId: effectiveOrganizationId, limit: 200 });
    const { data: connectors, isLoading: isLoadingApps } = useConnectors({ limit: 200 });
    const deleteAccount = useDeleteAccount(effectiveOrganizationId);
    const enableConnector = useEnableConnector(effectiveOrganizationId);
    const createConnectRequest = useCreateConnectRequest(effectiveOrganizationId);
    const createConnectorAccount = useCreateConnectorAccount(effectiveOrganizationId);
    const updateAuthConfig = useUpdateAuthConfig(effectiveOrganizationId);
    const deleteAuthConfig = useDeleteAuthConfig(effectiveOrganizationId);
    const refreshOperations = useRefreshAuthConfigOperations(effectiveOrganizationId);

    const [searchTerm, setSearchTerm] = useState('');
    const [busyAppId, setBusyAppId] = useState<string | null>(null);
    const [reconnectAccountId, setReconnectAccountId] = useState<string | null>(null);
    const [deletingAccountId, setDeletingAccountId] = useState<string | null>(null);
    const [advancedApp, setAdvancedApp] = useState<Connector | null>(null);
    const [isEnabling, setIsEnabling] = useState(false);
    const [credentialTarget, setCredentialTarget] = useState<CredentialTarget | null>(null);
    const [isSubmittingCredentials, setIsSubmittingCredentials] = useState(false);
    const [pendingOAuth, setPendingOAuth] = useState<{
        connectorId: string;
        baselineStatuses: Record<string, string>;
        startedAt: number;
    } | null>(null);
    const [accountPendingDisconnect, setAccountPendingDisconnect] = useState<{
        id: string;
        appName: string;
        accountLabel: string;
    } | null>(null);
    const [connectionTarget, setConnectionTarget] = useState<ConnectionTarget | null>(null);
    const [connectionError, setConnectionError] = useState<string | null>(null);
    const [isSavingConnection, setIsSavingConnection] = useState(false);
    const [busyInstallName, setBusyInstallName] = useState<string | null>(null);
    const [installPendingDelete, setInstallPendingDelete] = useState<AuthConfig | null>(null);

    useEffect(() => {
        if (!pendingOAuth) return;
        let cancelled = false;
        const poll = window.setInterval(() => {
            void refetchAccounts().then((result) => {
                if (cancelled) return;
                const current = (result.data ?? []) as Account[];
                const completed = current.find((account) => {
                    if (account.connector_id !== pendingOAuth.connectorId) return false;
                    const previousStatus = pendingOAuth.baselineStatuses[account.id];
                    return previousStatus === undefined || (previousStatus !== account.status && account.status === 'CONNECTED');
                });
                if (completed) {
                    window.clearInterval(poll);
                    setPendingOAuth(null);
                    toast.success(`${getAppLabel(completed.connector as Connector)} connected`);
                    return;
                }
                if (Date.now() - pendingOAuth.startedAt > 120_000) {
                    window.clearInterval(poll);
                    setPendingOAuth(null);
                    toast.info('Connection is still pending. You can retry from this page.');
                }
            });
        }, 2500);
        return () => {
            cancelled = true;
            window.clearInterval(poll);
        };
    }, [pendingOAuth, refetchAccounts]);

    const connectorsById = useMemo(
        () => new Map((connectors || []).map((connector) => [connector.id, connector])),
        [connectors],
    );

    const activeConfigs = useMemo(
        () => (authConfigs || []).filter((config) => config.status === 'ACTIVE'),
        [authConfigs],
    );

    // The install a bare connector answers to. An org may hold several of one
    // connector, and exactly one carries `is_default` — reading whichever came
    // back first made this depend on list order.
    const defaultConfigByAppId = useMemo(() => {
        const byApp = new Map<string, typeof activeConfigs[number]>();
        for (const config of activeConfigs) {
            const held = byApp.get(config.connector_id);
            if (!held || (config.is_default && !held.is_default)) byApp.set(config.connector_id, config);
        }
        return byApp;
    }, [activeConfigs]);

    // Connections the org configured itself — databases, APIs, MCP servers.
    // They get their own section and their own entry point, so they are also
    // taken out of the catalog grid below.
    const tenantConfiguredConnectors = useMemo(
        () => getTenantConfiguredConnectors(connectors),
        [connectors],
    );
    const tenantConfiguredIds = useMemo(
        () => new Set(tenantConfiguredConnectors.map((app) => app.id)),
        [tenantConfiguredConnectors],
    );
    const connections = useMemo(
        () =>
            activeConfigs
                .filter((config) => isTenantConfigured(getKindSpec(connectorsById.get(config.connector_id), config.kind)))
                .sort((a, b) => a.name.localeCompare(b.name)),
        [activeConfigs, connectorsById],
    );
    const existingInstallNames = useMemo(
        () => activeConfigs.map((config) => config.name),
        [activeConfigs],
    );

    const connectionInstallIds = useMemo(
        () => new Set(connections.map((install) => install.id)),
        [connections],
    );

    // A connection's account is the same fact as its connection row, so listing
    // both says everything twice. The exception is an account that needs
    // attention: editing a connection can invalidate its credentials, and
    // reconnecting is only reachable from the account row.
    const listedAccounts = useMemo(
        () =>
            (accounts || []).filter(
                (account) =>
                    !connectionInstallIds.has(account.auth_config_id) ||
                    getAccountStatusMeta(account.status).needsAttention,
            ),
        [accounts, connectionInstallIds],
    );

    /** Which connection an account belongs to, so its row names the right one. */
    const installNameByAccountId = useMemo(() => {
        const byInstall = new Map(connections.map((install) => [install.id, install.name]));
        return new Map(
            (accounts || [])
                .map((account) => [account.id, byInstall.get(account.auth_config_id)] as const)
                .filter((entry): entry is readonly [string, string] => Boolean(entry[1])),
        );
    }, [accounts, connections]);

    const connectedAppIds = useMemo(
        () => new Set((accounts || []).map((account) => account.connector_id)),
        [accounts],
    );

    const filteredApps = useMemo(() => {
        const query = searchTerm.toLowerCase();
        const matches = (connectors || []).filter(
            (app) =>
                !tenantConfiguredIds.has(app.id) &&
                ((app.title && app.title.toLowerCase().includes(query)) ||
                    (app.name && app.name.toLowerCase().includes(query)) ||
                    (app.description && app.description.toLowerCase().includes(query))),
        );
        // Float connected connectors to the top, then enabled ones, keeping the
        // original order stable within each group.
        const rank = (app: Connector) =>
            connectedAppIds.has(app.id) ? 0 : defaultConfigByAppId.has(app.id) ? 1 : 2;
        return matches
            .map((app, index) => ({ app, index }))
            .sort((a, b) => rank(a.app) - rank(b.app) || a.index - b.index)
            .map((entry) => entry.app);
    }, [connectors, searchTerm, connectedAppIds, defaultConfigByAppId, tenantConfiguredIds]);

    const attentionCount = useMemo(
        () => (accounts || []).filter((account) => getAccountStatusMeta(account.status).needsAttention).length,
        [accounts],
    );

    const openCredentialDialog = (
        app: Connector,
        capability: ConnectorKindSpec | null,
        authConfigId: string | null,
        mode: 'connect' | 'reconnect' = 'connect',
        accountId?: string,
    ) => {
        setCredentialTarget({ connector: app, capability, authConfigId, mode, accountId });
    };

    // OAuth needs a round-trip to fetch the authorization URL before we can act.
    const startOAuth = async (connectorId: string, authConfigId: string) => {
        const response = await createConnectRequest.mutateAsync({ connectorId, authConfigId });
        if (response.authorization_url) {
            setPendingOAuth({
                connectorId,
                baselineStatuses: Object.fromEntries((accounts || []).map((account) => [account.id, account.status])),
                startedAt: Date.now(),
            });
            openAuthorization(response.authorization_url);
        }
    };

    const openConnectionDialog = (app: Connector, install: AuthConfig | null = null) => {
        setConnectionError(null);
        setConnectionTarget({ connector: app, install });
    };

    const handleConnect = async (app: Connector) => {
        const existing = defaultConfigByAppId.get(app.id) ?? null;
        const capability = existing ? getKindSpec(app, existing.kind) : getPrimaryKindSpec(app);
        if (!capability) {
            toast.error('This connector is not available yet');
            return;
        }

        // A connection the org configures: always a new install, never a second
        // account on the existing one. "Add another database" means another
        // database, and reusing the install would have pointed it at the first.
        if (isTenantConfigured(getTenantConfiguredKindSpec(app))) {
            openConnectionDialog(app);
            return;
        }

        // Credential apps: open the form immediately so keystrokes land in the field,
        // not the page. Enabling (if needed) is deferred to submit time.
        if (usesDirectCredentials(capability)) {
            if (!existing && !canConnectWithDefaults(capability)) {
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
                if (!canConnectWithDefaults(capability)) {
                    setAdvancedApp(app);
                    return;
                }
                authConfig = await enableConnector.mutateAsync({
                    connectorId: app.id,
                    kind: capability.kind,
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

    /**
     * Creating a connection is two calls that read as one action: the install
     * carries the address, the account carries the credentials. An account is
     * always created, even with an empty credential set — execution resolves
     * one even for an MCP server that needs no auth.
     */
    const handleConnectionSubmit = async (submission: ConnectionSubmission) => {
        const target = connectionTarget;
        if (!target) return;
        const capability = getTenantConfiguredKindSpec(target.connector);
        if (!capability) return;

        setIsSavingConnection(true);
        setConnectionError(null);
        try {
            if (target.install) {
                const result = await updateAuthConfig.mutateAsync({
                    authConfigName: target.install.name,
                    name: submission.name,
                    config: submission.config,
                });
                const reauth = result?.accounts_marked_for_reauth ?? 0;
                toast.success(
                    reauth > 0
                        ? `Updated ${submission.name} · ${reauth} account${reauth === 1 ? '' : 's'} need${reauth === 1 ? 's' : ''} to reconnect`
                        : `Updated ${submission.name}`,
                );
            } else {
                const install = await enableConnector.mutateAsync({
                    connectorId: target.connector.id,
                    kind: capability.kind,
                    // The org supplied the connection itself, which is what
                    // ORG_CUSTOM records. Nothing branches on it for these
                    // kinds, but the column is immutable once written.
                    configSource: 'ORG_CUSTOM',
                    config: submission.config,
                    name: submission.name,
                });
                await createConnectorAccount.mutateAsync({
                    authConfigId: install.id,
                    credentials: submission.credentials,
                });
                toast.success(`Added ${submission.name}`);
            }
            setConnectionTarget(null);
        } catch (error) {
            console.error('Failed to save connection:', error);
            setConnectionError(describeConnectorError(error, 'Could not save this connection'));
        } finally {
            setIsSavingConnection(false);
        }
    };

    const handleRefreshInstall = async (install: AuthConfig) => {
        setBusyInstallName(install.name);
        try {
            const result = await refreshOperations.mutateAsync(install.name);
            const count = result?.operation_count ?? 0;
            toast.success(
                count > 0
                    ? `${install.name}: ${count} operation${count === 1 ? '' : 's'}`
                    : `${install.name} responded, but exposed no operations`,
            );
        } catch (error) {
            console.error('Failed to refresh operations:', error);
            toast.error(describeConnectorError(error, 'Could not reach this connection'));
        } finally {
            setBusyInstallName(null);
        }
    };

    const handleDeleteInstall = async () => {
        if (!installPendingDelete) return;
        setBusyInstallName(installPendingDelete.name);
        try {
            await deleteAuthConfig.mutateAsync(installPendingDelete.name);
            toast.success(`${installPendingDelete.name} deleted`);
            setInstallPendingDelete(null);
        } catch (error) {
            console.error('Failed to delete connection:', error);
            toast.error(describeConnectorError(error, 'Could not delete this connection'));
        } finally {
            setBusyInstallName(null);
        }
    };

    const handleAdvancedEnable = async (payload: AdvancedEnablePayload) => {
        if (!advancedApp) return;
        const app = advancedApp;
        setIsEnabling(true);
        try {
            const authConfig = await enableConnector.mutateAsync({
                connectorId: app.id,
                kind: payload.kind,
                configSource: payload.configSource,
                config: payload.config,
                name: payload.name,
            });
            toast.success('Connector enabled');
            setAdvancedApp(null);

            const capability = getKindSpec(app, authConfig.kind);
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
        const capability = getKindSpec(app, authConfig.kind);

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
                if (!target.capability || !canConnectWithDefaults(target.capability)) {
                    throw new Error('Connector is not configured for direct credentials');
                }
                const authConfig = await enableConnector.mutateAsync({
                    connectorId: target.connector.id,
                    kind: target.capability.kind,
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
                variant="region"
                icon={<Plug className="h-5 w-5" />}
                title="Select an organization"
                description="Connectors are enabled and connected inside an organization."
            />
        );
    }

    if (isLoadingAccounts || isLoadingApps || isLoadingAuthConfigs) {
        return (
            <div className={embedded ? 'min-h-[30vh] bg-transparent' : 'context-shell min-h-full bg-transparent pb-8'}>
                <ResourceCardGridSkeleton count={6} />
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

    // Band first, controls under it — the logos are the masthead, so the copy and
    // the search sit on the panel's own footer rather than fighting it for space.
    // The `<h1>` only prints on the standalone route; embedded, the pod shell
    // already names the section and repeating it read as a settings screen.
    const masthead = (
        <>
            {showHeader ? (
                <h1 className="mb-4 font-display text-4xl font-normal text-[var(--text-primary)]">Connectors</h1>
            ) : null}
            <div className="connector-masthead relative mb-6 overflow-hidden">
                <ConnectorMosaic connectors={connectors || []} />
                <div className="flex flex-col gap-3 px-4 pb-4 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-[var(--text-secondary)]">
                        Connect the apps you use, and they’re available across every pod in{' '}
                        {effectiveOrganizationName || 'this organization'}.
                    </p>
                    {searchField}
                </div>
            </div>
        </>
    );

    return (
        <div className={embedded ? 'min-h-full bg-transparent' : 'context-shell min-h-full bg-transparent pb-8'}>
            {masthead}

            <AddYourOwnRow connectors={tenantConfiguredConnectors} onAdd={(app) => openConnectionDialog(app)} />

            {connections.length > 0 && (
                <section className="context-section">
                    <div className="mb-3 flex items-center gap-2">
                        <h2 className="text-base font-normal text-[var(--text-primary)]">Your connections</h2>
                        <span className="text-xs text-[var(--text-tertiary)]">{connections.length}</span>
                    </div>
                    <div className="grid grid-cols-1 gap-x-4 lg:grid-cols-2">
                        {connections.map((install) => (
                            <ConnectionRow
                                key={install.id}
                                install={install}
                                connector={connectorsById.get(install.connector_id) ?? null}
                                organizationId={effectiveOrganizationId}
                                isBusy={busyInstallName === install.name}
                                onEdit={(target) =>
                                    openConnectionDialog(
                                        connectorsById.get(target.connector_id) as Connector,
                                        target,
                                    )
                                }
                                onRefresh={(target) => void handleRefreshInstall(target)}
                                onDelete={setInstallPendingDelete}
                            />
                        ))}
                    </div>
                </section>
            )}

            {listedAccounts.length > 0 && (
                <section className="context-section">
                    <div className="mb-3 flex items-center gap-2">
                        <h2 className="text-base font-normal text-[var(--text-primary)]">Your accounts</h2>
                        <span className="text-xs text-[var(--text-tertiary)]">{listedAccounts.length}</span>
                        {attentionCount > 0 ? (
                            <span className="text-xs font-medium text-[var(--state-warning)]">
                                · {attentionCount} need{attentionCount === 1 ? 's' : ''} attention
                            </span>
                        ) : null}
                    </div>
                    <div className="grid grid-cols-1 gap-x-4 lg:grid-cols-2">
                        {listedAccounts.map((account) => (
                            <ConnectedAccountRow
                                key={account.id}
                                account={account}
                                label={installNameByAccountId.get(account.id)}
                                isBusy={
                                    reconnectAccountId === account.id
                                    || deletingAccountId === account.id
                                    || pendingOAuth?.connectorId === account.connector_id
                                }
                                onReconnect={handleReconnect}
                                onDisconnect={(acc) =>
                                    setAccountPendingDisconnect({
                                        id: acc.id,
                                        appName: acc.connector?.title || acc.connector?.name || 'this app',
                                        accountLabel: acc.display_name || acc.email || acc.connector?.title || acc.connector?.name || 'Connected account',
                                    })
                                }
                            />
                        ))}
                    </div>
                </section>
            )}

            <section>
                <div className="mb-4 flex items-center gap-2">
                    <h2 className="text-base font-normal text-[var(--text-primary)]">Browse apps</h2>
                    <span className="text-xs text-[var(--text-tertiary)]">{filteredApps.length}</span>
                </div>
                <ConnectorGrid
                    connectors={filteredApps}
                    connectedAppIds={connectedAppIds}
                    busyAppId={busyAppId || pendingOAuth?.connectorId || null}
                    searchTerm={searchTerm}
                    onConnect={handleConnect}
                    onAdvanced={setAdvancedApp}
                />
            </section>

            <AddConnectionDialog
                target={connectionTarget}
                isSubmitting={isSavingConnection}
                existingNames={existingInstallNames}
                error={connectionError}
                onOpenChange={(open) => {
                    if (!open) {
                        setConnectionTarget(null);
                        setConnectionError(null);
                    }
                }}
                onSubmit={(submission) => void handleConnectionSubmit(submission)}
            />

            <DestructiveConfirmationDialog
                open={Boolean(installPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setInstallPendingDelete(null);
                }}
                title="Delete connection"
                description={`Delete ${installPendingDelete?.name ?? 'this connection'}? Every account connected through it is removed with it.`}
                resourceName={
                    installPendingDelete
                        ? getInstallLabel(
                              installPendingDelete,
                              connectorsById.get(installPendingDelete.connector_id) ?? null,
                          )
                        : 'connection'
                }
                confirmationText="delete"
                consequences={[
                    'Accounts connected through this connection are deleted with it.',
                    'Agents and workflows using its operations will lose access.',
                ]}
                confirmLabel="Delete"
                pendingLabel="Deleting..."
                isPending={Boolean(busyInstallName && installPendingDelete?.name === busyInstallName)}
                onConfirm={() => void handleDeleteInstall()}
            />

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
