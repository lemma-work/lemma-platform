import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { Connector, Account, AuthConfig } from '@/lib/types';

export interface ConnectorsListResponse {
    items: Connector[];
    next_page_token?: string | null;
}

export interface AccountsListResponse {
    items: Account[];
    next_page_token?: string | null;
}

export interface AuthConfigsListResponse {
    items: AuthConfig[];
    next_page_token?: string | null;
}

export interface UseConnectorsOptions {
    organizationId?: string;
    limit?: number;
    pageToken?: string;
    connectorId?: string;
    search?: string;
    enabled?: boolean;
}

export const useConnectors = (options?: UseConnectorsOptions) => {
    return useQuery({
        queryKey: ['connectors', options?.limit, options?.pageToken],
        queryFn: () =>
            getLemmaClient().connectors.list({
                limit: options?.limit,
                pageToken: options?.pageToken,
            }) as Promise<ConnectorsListResponse>,
        select: (data: ConnectorsListResponse) => data.items || [],
        enabled: options?.enabled ?? true,
    });
};

export const useAccounts = (options?: UseConnectorsOptions) => {
    return useQuery({
        queryKey: ['accounts', options?.organizationId, options?.connectorId, options?.limit, options?.pageToken],
        queryFn: () =>
            getLemmaClient().connectors.accounts.list(options?.organizationId as string, {
                connectorId: options?.connectorId,
                limit: options?.limit,
                pageToken: options?.pageToken,
            }) as Promise<AccountsListResponse>,
        select: (data: AccountsListResponse) => data.items || [],
        enabled: Boolean(options?.organizationId) && (options?.enabled ?? true),
    });
};

export const useAuthConfigs = (options?: UseConnectorsOptions) => {
    return useQuery({
        queryKey: ['auth-configs', options?.organizationId, options?.limit, options?.pageToken],
        queryFn: () =>
            getLemmaClient().connectors.authConfigs.list(options?.organizationId as string, {
                limit: options?.limit,
                pageToken: options?.pageToken,
            }) as Promise<AuthConfigsListResponse>,
        select: (data: AuthConfigsListResponse) => data.items || [],
        enabled: Boolean(options?.organizationId) && (options?.enabled ?? true),
    });
};

export const useTriggers = (options?: UseConnectorsOptions) => {
    const organizationId = options?.organizationId;
    const connectorId = options?.connectorId;
    const triggersEnabled = options?.enabled ?? true;

    // Triggers are scoped to an auth config (org + app + kind). An org holds
    // at most one auth config per app, so resolve the app's auth config name here
    // and list triggers for it — only the auth config's kind is returned.
    const { data: authConfigs = [] } = useAuthConfigs({
        organizationId,
        limit: 100,
        enabled: Boolean(organizationId && connectorId) && triggersEnabled,
    });
    const authConfigName = connectorId
        ? authConfigs.find(
              (config) => config.connector_id === connectorId && config.status === 'ACTIVE'
          )?.name
        : undefined;

    return useQuery({
        queryKey: ['triggers', organizationId, authConfigName, options?.search, options?.limit],
        queryFn: () =>
            getLemmaClient().connectors.triggers.list(
                {
                    organizationId: organizationId as string,
                    authConfigName: authConfigName as string,
                },
                {
                    search: options?.search,
                    limit: options?.limit,
                }
            ),
        select: (data) => data.items || [],
        enabled: Boolean(organizationId && authConfigName) && triggersEnabled,
    });
};

export const useTrigger = (
    options?: UseConnectorsOptions & { triggerName?: string },
) => {
    const organizationId = options?.organizationId;
    const connectorId = options?.connectorId;
    const triggerName = options?.triggerName;
    const enabled = options?.enabled ?? true;

    // Trigger list responses are lean (no payload_schema); fetch the single
    // selected trigger's detail to derive payload field paths.
    const { data: authConfigs = [] } = useAuthConfigs({
        organizationId,
        limit: 100,
        enabled: Boolean(organizationId && connectorId && triggerName) && enabled,
    });
    const authConfigName = connectorId
        ? authConfigs.find(
              (config) => config.connector_id === connectorId && config.status === 'ACTIVE'
          )?.name
        : undefined;

    return useQuery({
        queryKey: ['trigger', organizationId, authConfigName, triggerName],
        queryFn: () =>
            getLemmaClient().connectors.triggers.get(
                {
                    organizationId: organizationId as string,
                    authConfigName: authConfigName as string,
                },
                triggerName as string,
            ),
        enabled: Boolean(organizationId && authConfigName && triggerName) && enabled,
    });
};

export const useDeleteAccount = (organizationId?: string) => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (accountId: string) => {
            if (!organizationId) throw new Error('organizationId is required to delete an account');
            await getLemmaClient().connectors.accounts.delete(organizationId, accountId);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['accounts', organizationId] });
        },
    });
};

export const useEnableConnector = (organizationId?: string) => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (data: {
            connectorId: string;
            kind?: string;
            configSource?: string;
            config?: Record<string, unknown> | null;
            name?: string | null;
        }) => {
            if (!organizationId) throw new Error('organizationId is required to enable an app');
            return getLemmaClient().connectors.enableApp(organizationId, data.connectorId, {
                kind: data.kind,
                config_source: data.configSource,
                config: data.config,
                name: data.name,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['auth-configs', organizationId] });
        },
    });
};

export const useCreateConnectRequest = (organizationId?: string) => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (data: { connectorId: string; authConfigId?: string }) => {
            if (!organizationId) throw new Error('organizationId is required to connect an app');
            const response = await getLemmaClient().connectors.createConnectRequest(
                organizationId,
                data.authConfigId ? { auth_config_id: data.authConfigId } : data.connectorId,
            );
            return response;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['accounts', organizationId] });
        },
    });
};

export const useCreateConnectorAccount = (organizationId?: string) => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (data: {
            authConfigId?: string | null;
            authConfigName?: string | null;
            credentials: Record<string, unknown>;
            email?: string | null;
            providerAccountId?: string | null;
        }) => {
            if (!organizationId) throw new Error('organizationId is required to connect an app account');
            return getLemmaClient().connectors.accounts.create(organizationId, {
                auth_config_id: data.authConfigId,
                auth_config_name: data.authConfigName,
                credentials: data.credentials,
                email: data.email,
                provider_account_id: data.providerAccountId,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['accounts', organizationId] });
        },
    });
};

export const useConnectorOperations = (
    scope: { organizationId?: string | null; authConfigName?: string | null } | null | undefined,
    enabled = true
) => {
    return useQuery({
        queryKey: ['connector-operations', scope?.organizationId, scope?.authConfigName],
        queryFn: async () => {
            return getLemmaClient().connectors.operations.list({
                organizationId: scope?.organizationId as string,
                authConfigName: scope?.authConfigName as string,
            });
        },
        enabled: Boolean(scope?.organizationId && scope?.authConfigName) && enabled,
    });
};
