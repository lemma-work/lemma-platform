'use client';

import {
    useAccounts,
    useConnectors,
    useAuthConfigs,
    useAccountInstallations,
    useBindAccountInstallation,
    useCreateConnectRequest,
    useCreateInstallRequest,
    useCreateConnectorAccount,
    useDeleteAccount,
    useDeleteAuthConfig,
    useRotateAccountCredentials,
    useEnableConnector,
    useRefreshAuthConfigOperations,
    useUpdateAuthConfig,
} from '@/lib/hooks/use-connectors';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { Input } from '@/components/ui/input';
import { Plug, Search } from '@/components/ui/icons';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import type { Account, AuthConfig, Connector } from '@/lib/types';
import type { InstallationChoiceSchema } from 'lemma-sdk';
import { useOrganization } from '@/components/dashboard/org-context';
import { ResourceCardGridSkeleton } from '@/components/shared/loading';
import { ResourceFeedbackBanner } from '@/components/shared/resource-feedback';
import { ConnectorGrid } from './connector-grid';
import { ConnectorMosaic } from './connector-mosaic';
import { ConnectedAccountRow } from './connector-card';
import { AddYourOwnRow, ConnectionRow } from './connection-rows';
import { ConnectAccountDialog, type CredentialTarget } from './connect-account-dialog';
import { AddConnectionDialog, type ConnectionSubmission, type ConnectionTarget } from './add-connection-dialog';
import { AdvancedConfigDialog, type AdvancedEnablePayload } from './advanced-config';
import { InstallChoiceDialog } from './install-choice-dialog';
import type { AuthConfigMode } from './connector-utils';
import {
    canConnectWithDefaults,
    describeConnectorError,
    findAuthConfigForAccount,
    getAccountStatusMeta,
    INSTALL_STATE,
    getAppLabel,
    getInstallLabel,
    getPrimaryKindSpec,
    getKindSpec,
    getTenantConfiguredConnectors,
    getTenantConfiguredKindSpec,
    installUsesOAuth,
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

/** Where a popup round trip comes back to. See `app/oauth/complete`. */
export const OAUTH_COMPLETE_PATH = '/oauth/complete';

/**
 * Where the provider should send the browser back to, and what to do if there
 * turned out to be no opener. `from` is the page in progress, so a tab that
 * cannot report home still ends up back where it started.
 */
const completionPath = () =>
    `${OAUTH_COMPLETE_PATH}?from=${encodeURIComponent(window.location.pathname)}`;

/**
 * Open the provider in a new tab, as before — minus `noopener`, which is the
 * only thing that changes here.
 *
 * `noopener` makes `window.opener` null in the new tab, so the result could
 * never be handed back and completion was guessed at by polling this page every
 * 2.5 seconds. Dropping it lets the completion page post the outcome home and
 * close itself, which is what removes the polling.
 *
 * The cost is that the opened page can reach `window.opener`. That page is
 * GitHub, over https, at a URL this app just built — the standard trade every
 * OAuth tab makes.
 */
const openAuthorization = (url?: string | null) => {
    if (!url) return;
    // A new tab, as it always was. No window features, so the browser opens a
    // tab rather than a window.
    const opened = window.open(url, '_blank');
    // Only if the browser refused outright, so the flow still finishes.
    if (!opened) window.location.assign(url);
};

export function ConnectorsView({ organizationId, organizationName, embedded = false, showHeader = true }: ConnectorsViewProps) {
    const { currentOrg, organizations } = useOrganization();
    const effectiveOrganizationId = organizationId || currentOrg?.id;
    const effectiveOrganizationName =
        organizationName ||
        organizations.find((org) => org.id === effectiveOrganizationId)?.name ||
        currentOrg?.name;

    const { data: accounts, isLoading: isLoadingAccounts, error: accountsError, refetch: refetchAccounts } = useAccounts({ organizationId: effectiveOrganizationId, limit: 200 });
    const { data: authConfigs, isLoading: isLoadingAuthConfigs, error: authConfigsError } = useAuthConfigs({ organizationId: effectiveOrganizationId, limit: 200 });
    const { data: connectors, isLoading: isLoadingApps, error: connectorsError } = useConnectors({ limit: 200 });
    const deleteAccount = useDeleteAccount(effectiveOrganizationId);
    const enableConnector = useEnableConnector(effectiveOrganizationId);
    const createConnectRequest = useCreateConnectRequest(effectiveOrganizationId);
    const createInstallRequest = useCreateInstallRequest(effectiveOrganizationId);
    const refreshInstallations = useAccountInstallations(effectiveOrganizationId);
    const bindInstallation = useBindAccountInstallation(effectiveOrganizationId);
    const createConnectorAccount = useCreateConnectorAccount(effectiveOrganizationId);
    const updateAuthConfig = useUpdateAuthConfig(effectiveOrganizationId);
    const deleteAuthConfig = useDeleteAuthConfig(effectiveOrganizationId);
    const rotateAccountCredentials = useRotateAccountCredentials(effectiveOrganizationId);
    const refreshOperations = useRefreshAuthConfigOperations(effectiveOrganizationId);

    const [searchTerm, setSearchTerm] = useState('');
    const [busyAppId, setBusyAppId] = useState<string | null>(null);
    const [reconnectAccountId, setReconnectAccountId] = useState<string | null>(null);
    const [deletingAccountId, setDeletingAccountId] = useState<string | null>(null);
    const [advancedApp, setAdvancedApp] = useState<Connector | null>(null);
    const [isEnabling, setIsEnabling] = useState(false);
    const [credentialTarget, setCredentialTarget] = useState<CredentialTarget | null>(null);
    const [isSubmittingCredentials, setIsSubmittingCredentials] = useState(false);
    // Only "a redirect is on its way": the connect request round trip happens
    // before we can navigate, and a row that looks inert in the meantime reads
    // as a click that did nothing.
    const [pendingOAuth, setPendingOAuth] = useState<{ connectorId: string } | null>(null);
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
    const [handledInstallParam, setHandledInstallParam] = useState(false);
    const [handledConnectResult, setHandledConnectResult] = useState(false);
    const [installingAccountId, setInstallingAccountId] = useState<string | null>(null);
    const [installChoices, setInstallChoices] = useState<{
        account: Account;
        choices: InstallationChoiceSchema[];
    } | null>(null);
    const [advancedMode, setAdvancedMode] = useState<AuthConfigMode | undefined>(undefined);

    /**
     * `?install=<connector>` opens this page straight on its own-app form.
     *
     * Where the link comes from is the point: making a Slack app happens in
     * Slack, and the three credentials it produces can only be pasted here.
     * Landing on the connector grid instead left the person holding a client
     * secret with nothing on screen asking for it — the offer to make the app
     * is over there, and the only place to finish is over here.
     *
     * Once, hence the flag: reopening the dialog every render would make it
     * impossible to close, and the param outlives the first visit.
     */
    useEffect(() => {
        if (handledInstallParam || !connectors?.length) return;
        const requested = new URLSearchParams(window.location.search).get('install');
        if (!requested) return;
        setHandledInstallParam(true);
        const app = connectors.find((connector) => connector.id === requested.toLowerCase());
        if (app) {
            // Straight to the form the credentials go in.
            setAdvancedMode('CUSTOM');
            setAdvancedApp(app);
        }
    }, [connectors, handledInstallParam]);

    /**
     * What the round trip established, however it got back here.
     *
     * Two ways in, one handler: a popup posts the outcome to its opener, and a
     * full navigation (popup blocked, or somebody following the link directly)
     * arrives with the same values in the query string.
     */
    /**
     * Ask GitHub about every account that has nothing recorded yet.
     *
     * Used when an installation arrived without naming an account -- installed
     * from GitHub's own page rather than through a link this app minted.
     */
    const reconcileUnboundAccounts = useCallback(async () => {
        const current = ((await refetchAccounts()).data ?? []) as Account[];
        const unbound = current.filter(
            (account) =>
                account.connector_id === 'github'
                && account.install_state !== INSTALL_STATE.READY,
        );
        await Promise.all(
            unbound.map((account) =>
                refreshInstallations
                    .mutateAsync({ accountId: account.id, refresh: true })
                    .catch(() => undefined),
            ),
        );
    }, [refetchAccounts, refreshInstallations]);

    const reportConnectOutcome = useCallback(
        (outcome: string | null, accountId?: string | null, reason?: string | null) => {
            // The round trip is over however it ended, so the row stops saying
            // "Connecting". This used to be cleared by the polling loop that
            // watched for the account to change; the loop is gone, and without
            // this the spinner ran forever after a perfectly successful connect.
            setPendingOAuth(null);
            switch (outcome) {
                case 'connected':
                    toast.success('Connected');
                    break;
                case 'install_required':
                    // Deliberately not phrased as a failure. The credential is
                    // fine; what is missing is the app's access to any repository.
                    toast.info('Almost there — the app still needs installing to reach your repositories.');
                    if (accountId) void refreshInstallations.mutateAsync({ accountId }).catch(() => undefined);
                    break;
                case 'pending_approval':
                    toast.info('Requested. An owner of that organisation has to approve the app before it can be installed.');
                    break;
                case 'install_received':
                    // An install started from GitHub's own page, so there was no
                    // request here to complete and no account named on the way
                    // back. Nothing is wrong -- but nothing has been recorded
                    // either, and `install_state` is derived locally, so a plain
                    // refetch would leave the row still saying "Install
                    // required". Ask GitHub, for whichever accounts are unbound.
                    toast.success('Installation received');
                    void reconcileUnboundAccounts();
                    break;
                case 'error':
                    toast.error(reason || 'The account was not connected.');
                    break;
                default:
                    return;
            }
            void refetchAccounts();
        },
        [reconcileUnboundAccounts, refetchAccounts, refreshInstallations],
    );

    /**
     * A trip that never reports back — the tab was closed on the provider's
     * page, or the browser lost the opener. Nothing has gone wrong that we can
     * name, but the row must stop claiming to be mid-flight.
     */
    useEffect(() => {
        if (!pendingOAuth) return;
        const timer = window.setTimeout(() => setPendingOAuth(null), 5 * 60_000);
        return () => window.clearTimeout(timer);
    }, [pendingOAuth]);

    /** The opened tab handing its result back before it closes. */
    useEffect(() => {
        const onMessage = (event: MessageEvent) => {
            // Origin first, always: this listener is reachable by any window
            // that holds a handle to this one.
            if (event.origin !== window.location.origin) return;
            const data = event.data;
            if (!data || typeof data !== 'object' || data.source !== 'lemma-connect') return;
            reportConnectOutcome(data.connect ?? null, data.account ?? null, data.reason ?? null);
        };
        window.addEventListener('message', onMessage);
        return () => window.removeEventListener('message', onMessage);
    }, [reportConnectOutcome]);

    /**
     * The same outcome arriving as a query string, when there was no popup to
     * report it. Read once and stripped: a reload is not a second connection.
     */
    useEffect(() => {
        if (handledConnectResult) return;
        const params = new URLSearchParams(window.location.search);
        const outcome = params.get('connect');
        if (!outcome) return;
        setHandledConnectResult(true);

        const accountId = params.get('account');
        const reason = params.get('reason');
        for (const key of ['connect', 'connector', 'account', 'code', 'reason']) params.delete(key);
        const query = params.toString();
        window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}`);

        reportConnectOutcome(outcome, accountId, reason);
    }, [handledConnectResult, reportConnectOutcome]);

    /**
     * Finish an installation, or settle which one an account speaks for.
     *
     * Asking first is what makes this cheap for the commonest case by far:
     * somebody who installed the App on GitHub and came back without following
     * a link we minted is already done, and telling them to install it again
     * would be wrong.
     */
    const handleInstall = async (account: Account) => {
        setInstallingAccountId(account.id);
        try {
            const found = await refreshInstallations.mutateAsync({
                accountId: account.id,
                refresh: true,
            });
            if (found.install_state === INSTALL_STATE.READY) {
                toast.success('Installation found');
                return;
            }
            if (found.install_state === INSTALL_STATE.CHOOSE_INSTALL) {
                setInstallChoices({ account, choices: found.choices ?? [] });
                return;
            }
            const request = await createInstallRequest.mutateAsync({
                accountId: account.id,
                returnTo: completionPath(),
            });
            openAuthorization(request.authorization_url);
        } catch (error) {
            toast.error(describeConnectorError(error, 'Could not start the installation.'));
        } finally {
            setInstallingAccountId(null);
        }
    };

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

    /** The account a connection authenticates with, so its token can be rotated. */
    const accountIdByInstallId = useMemo(() => {
        const byInstall = new Map<string, string>();
        for (const account of accounts || []) {
            if (!connectionInstallIds.has(account.auth_config_id)) continue;
            const held = byInstall.get(account.auth_config_id);
            if (!held || account.is_default) byInstall.set(account.auth_config_id, account.id);
        }
        return byInstall;
    }, [accounts, connectionInstallIds]);

    /**
     * Connections that sign in through a browser and have nobody signed in yet.
     *
     * An MCP install whose server described its own authorization is one of
     * these, and until it was distinguishable from a token install it was
     * created with an empty credential set and left unreachable — connected in
     * appearance, 401 on every call, with no control anywhere that offered to
     * fix it.
     */
    const installIdsNeedingSignIn = useMemo(() => {
        const connected = new Set(
            (accounts || [])
                .filter((account) => !getAccountStatusMeta(account.status).needsAttention)
                .map((account) => account.auth_config_id),
        );
        return new Set(
            connections
                .filter(
                    (install) =>
                        installUsesOAuth(
                            install,
                            getKindSpec(connectorsById.get(install.connector_id), install.kind),
                        ) && !connected.has(install.id),
                )
                .map((install) => install.id),
        );
    }, [accounts, connections, connectorsById]);

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
        const response = await createConnectRequest.mutateAsync({
            connectorId,
            authConfigId,
            returnTo: completionPath(),
        });
        if (response.authorization_url) {
            setPendingOAuth({ connectorId });
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
            toast.error(describeConnectorError(error, 'Failed to connect'));
        } finally {
            setBusyAppId(null);
        }
    };

    /**
     * Creating a connection is two calls that read as one action: the install
     * carries the address, the account carries the credentials. An account is
     * always created, even with an empty credential set — execution resolves
     * one even for an MCP server that needs no auth, and the backend validates
     * credentials against the kind's schema rather than merely requiring them
     * to be non-empty, so a server whose token field is optional connects with
     * nothing filled in.
     *
     * Because it is two calls and only one action, a failure on the second
     * must not leave the first behind. It used to: the install was committed,
     * the account POST failed, and the person was left with a connection that
     * can never run — every execution resolves an account — under a name now
     * taken, so even retrying was refused. The install is removed on that
     * path; it was created moments ago in this same action and has no other
     * accounts, so there is nothing else to lose with it.
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
                // Creating the install is what asks the server how it wants to
                // be authorized, so only now is this knowable. A server that
                // answered with an authorization server is signed into, and
                // posting an empty credential set to it produced an account
                // that looked connected, held no token, and 401'd every call —
                // with no way back, because nothing in this view offered a
                // sign-in. The install stays either way: it is valid, and
                // "Sign in" on its row is the way back if the window is closed.
                if (installUsesOAuth(install, capability)) {
                    setConnectionTarget(null);
                    toast.success(`Added ${submission.name} · sign in to finish`);
                    await startOAuth(target.connector.id, install.id);
                    return;
                }
                try {
                    await createConnectorAccount.mutateAsync({
                        authConfigId: install.id,
                        credentials: submission.credentials,
                    });
                } catch (accountError) {
                    // Best-effort: if the cleanup itself fails the original
                    // error is still what the person needs to see, and a
                    // stranded install is no worse than before.
                    try {
                        await deleteAuthConfig.mutateAsync(install.name);
                    } catch (cleanupError) {
                        console.error('Failed to remove the partial connection:', cleanupError);
                    }
                    throw accountError;
                }
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

    /**
     * Choose which install a bare connector id resolves to.
     *
     * The API has accepted `is_default` on the install PATCH all along and the
     * hook forwards it, but nothing in the app ever passed it — so an
     * organization with two Slack apps, or two of any connector, was
     * permanently stuck with whichever it created first. That matters because
     * the default is what a bare connector id resolves to, in the backend's
     * own unique index and in every resolver on this side.
     */
    const handleMakeDefault = async (install: AuthConfig) => {
        setBusyInstallName(install.name);
        try {
            await updateAuthConfig.mutateAsync({
                authConfigName: install.name,
                isDefault: true,
            });
            toast.success(`${getInstallLabel(install, connectorsById.get(install.connector_id) ?? null)} is now the default`);
        } catch (error) {
            console.error('Failed to set the default connection:', error);
            toast.error(describeConnectorError(error, 'Could not set the default connection'));
        } finally {
            setBusyInstallName(null);
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
            if (!installUsesOAuth(authConfig, capability) && usesDirectCredentials(capability)) {
                openCredentialDialog(app, capability, authConfig.id, 'connect');
                return;
            }
            await startOAuth(app.id, authConfig.id);
        } catch (error) {
            console.error('Failed to enable connector:', error);
            toast.error(describeConnectorError(error, 'Failed to enable connector'));
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
        //
        // The install is asked first, because `usesDirectCredentials` reads the
        // catalog and the catalog is wrong for exactly one kind: every `mcp`
        // install claims API_KEY there, so reconnecting one that signs in
        // through a browser offered a bearer-token box instead of the flow.
        if (!installUsesOAuth(authConfig, capability) && usesDirectCredentials(capability)) {
            openCredentialDialog(app, capability, authConfig.id, 'reconnect', account.id);
            return;
        }

        setReconnectAccountId(account.id);
        try {
            await startOAuth(account.connector_id, authConfig.id);
        } catch (error) {
            console.error('Failed to reconnect:', error);
            toast.error(describeConnectorError(error, 'Failed to start reconnect'));
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
                // Rotated in place. This used to delete the account and create
                // a replacement, which loses everything if the create fails —
                // the old one is already gone, revoked upstream on the way out
                // — and issues a new id when it succeeds, stranding every
                // schedule, surface and grant pinned to the old one.
                await rotateAccountCredentials.mutateAsync({
                    accountId: target.accountId,
                    credentials: data,
                });
            } else {
                await createConnectorAccount.mutateAsync({ authConfigId, credentials: data });
            }
            toast.success(`${getAppLabel(target.connector)} ${target.mode === 'reconnect' ? 'reconnected' : 'connected'}`);
            setCredentialTarget(null);
        } catch (error) {
            console.error('Failed to save credentials:', error);
            toast.error(describeConnectorError(error, 'Failed to save credentials'));
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
            toast.error(describeConnectorError(error, 'Failed to disconnect account'));
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

    // A rejected query leaves `isPending` false and the data undefined, and
    // every consumer here coalesces undefined to an empty array — so without
    // this a 403 or a network failure rendered as "you have no connections",
    // beside a full catalog. Worse when only the accounts query failed: every
    // row reverted from "Add another" to "Connect", inviting a duplicate
    // connection against an account the person has and cannot see.
    const loadError = connectorsError ?? accountsError ?? authConfigsError;
    if (loadError) {
        return (
            <div className={embedded ? 'min-h-[30vh] bg-transparent' : 'context-shell min-h-full bg-transparent pb-8'}>
                <ResourceFeedbackBanner
                    tone="error"
                    title="Could not load your connectors"
                    description={describeConnectorError(
                        loadError,
                        'Something went wrong reaching the connectors service.',
                    )}
                    actions={[{ label: 'Try again', onClick: () => { void refetchAccounts(); } }]}
                />
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
                                needsSignIn={installIdsNeedingSignIn.has(install.id)}
                                onSignIn={(target) => void startOAuth(target.connector_id, target.id)}
                                onReplaceCredentials={
                                    installIdsNeedingSignIn.has(install.id) ||
                                    !accountIdByInstallId.has(install.id)
                                        ? undefined
                                        : (target) =>
                                              openCredentialDialog(
                                                  connectorsById.get(target.connector_id) as Connector,
                                                  getKindSpec(
                                                      connectorsById.get(target.connector_id),
                                                      target.kind,
                                                  ),
                                                  target.id,
                                                  'reconnect',
                                                  accountIdByInstallId.get(target.id),
                                              )
                                }
                                onEdit={(target) =>
                                    openConnectionDialog(
                                        connectorsById.get(target.connector_id) as Connector,
                                        target,
                                    )
                                }
                                onRefresh={(target) => void handleRefreshInstall(target)}
                                onMakeDefault={(target) => void handleMakeDefault(target)}
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
                                    || installingAccountId === account.id
                                }
                                onReconnect={handleReconnect}
                                onInstall={handleInstall}
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
                existingNames={existingInstallNames}
                isEnabling={isEnabling}
                initialMode={advancedMode}
                onOpenChange={(open) => {
                    if (!open) {
                        setAdvancedApp(null);
                        setAdvancedMode(undefined);
                    }
                }}
                onEnable={handleAdvancedEnable}
            />

            <InstallChoiceDialog
                choices={installChoices?.choices ?? null}
                accountLabel={
                    installChoices?.account.display_name
                    || installChoices?.account.email
                    || 'This account'
                }
                isSubmitting={bindInstallation.isPending}
                onOpenChange={(open) => {
                    if (!open) setInstallChoices(null);
                }}
                onChoose={async (installationId) => {
                    if (!installChoices) return;
                    try {
                        await bindInstallation.mutateAsync({
                            accountId: installChoices.account.id,
                            installationId,
                        });
                        setInstallChoices(null);
                        toast.success('Connected');
                    } catch (error) {
                        toast.error(describeConnectorError(error, 'Could not use that installation.'));
                    }
                }}
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
