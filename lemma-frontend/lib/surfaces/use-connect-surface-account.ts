'use client';

import { useCallback } from 'react';

import {
    useAuthConfigs,
    useCreateConnectorAccount,
    useEnableConnector,
} from '@/lib/hooks/use-connectors';

/**
 * Create a connector account for a surface without leaving the setup modal.
 *
 * Bring-your-own surfaces (a Telegram bot token, a WhatsApp number's Meta
 * credentials) are modeled as connector accounts, but sending someone to the
 * Connectors page mid-journey loses them. This packages the two steps the
 * connectors view does — reuse or create the org's auth config, then create the
 * account against it — behind one call that returns the new `account_id` ready
 * to bind to the surface.
 */
export function useConnectSurfaceAccount(organizationId: string | undefined) {
    const enableConnector = useEnableConnector(organizationId);
    const createAccount = useCreateConnectorAccount(organizationId);
    const { data: authConfigs = [] } = useAuthConfigs({
        organizationId,
        limit: 200,
        enabled: Boolean(organizationId),
    });

    const connect = useCallback(
        async ({
            connectorId,
            provider,
            credentials,
        }: {
            connectorId: string;
            provider?: string;
            credentials: Record<string, unknown>;
        }): Promise<string> => {
            if (!organizationId) {
                throw new Error('An organization is required to connect an account.');
            }

            // Surface connectors are LEMMA-native and self-managed, so the org's
            // auth config is just a container — reuse whichever one already
            // exists rather than accumulating one per surface.
            let authConfigId = authConfigs.find(
                (config) => config.connector_id === connectorId,
            )?.id;

            if (!authConfigId) {
                const created = await enableConnector.mutateAsync({
                    connectorId,
                    provider,
                    configSource: 'SYSTEM_DEFAULT',
                });
                authConfigId = created.id;
            }

            const account = await createAccount.mutateAsync({ authConfigId, credentials });
            return account.id;
        },
        [authConfigs, createAccount, enableConnector, organizationId],
    );

    return {
        connect,
        isConnecting: enableConnector.isPending || createAccount.isPending,
    };
}
