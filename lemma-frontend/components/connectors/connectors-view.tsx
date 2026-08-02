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
import { Plug, Search } from '@/components/ui/icons';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import type { Account, Connector } from '@/lib/types';
import { useOrganization } from '@/components/dashboard/org-context';
import { ResourceCardGridSkeleton } from '@/components/shared/loading';
import { ConnectorGrid } from './connector-grid';
import { ConnectorMosaic } from './connector-mosaic';
import { ConnectedAccountRow } from './connector-card';
import { ConnectAccountDialog, type CredentialTarget } from './connect-account-dialog';
import { AdvancedConfigDialog, type AdvancedEnablePayload } from './advanced-config';
import {
    findAuthConfigForAccount,
    getAccountStatusMeta,
    getAppLabel,
    getPrimaryKindSpec,
    getKindSpec,
    hasSystemDefault,
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

    const handleConnect = async (app: Connector) => {
        const existing = enabledConfigByAppId.get(app.id) ?? null;
        const capability = existing ? getKindSpec(app, existing.kind) : getPrimaryKindSpec(app);
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
                if (!target.capability || !hasSystemDefault(target.capability)) {
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

            {accounts && accounts.length > 0 && (
                <section className="context-section">
                    <div className="mb-3 flex items-center gap-2">
                        <h2 className="text-base font-normal text-[var(--text-primary)]">Your accounts</h2>
                        <span className="text-xs text-[var(--text-tertiary)]">{accounts.length}</span>
                        {attentionCount > 0 ? (
                            <span className="text-xs font-medium text-[var(--state-warning)]">
                                · {attentionCount} need{attentionCount === 1 ? 's' : ''} attention
                            </span>
                        ) : null}
                    </div>
                    <div className="grid grid-cols-1 gap-x-4 lg:grid-cols-2">
                        {accounts.map((account) => (
                            <ConnectedAccountRow
                                key={account.id}
                                account={account}
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
