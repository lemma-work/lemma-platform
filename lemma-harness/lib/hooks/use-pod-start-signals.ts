'use client';

import { useMemo } from 'react';

import { getAccountStatusMeta } from '@/components/connectors/connector-utils';
import { useAgents } from '@/lib/hooks/use-agents';
import { useAppPages } from '@/lib/hooks/use-app';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { useAccounts } from '@/lib/hooks/use-connectors';
import { useTables } from '@/lib/hooks/use-datastores';
import { useFlows } from '@/lib/hooks/use-flows';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { useSchedules } from '@/lib/hooks/use-schedules';
import { EMPTY_POD_START_SIGNALS, type PodStartSignals } from '@/lib/pods/pod-start-signals';
import type { Conversation } from '@/lib/types';

const RECENT_CONVERSATION_LIMIT = 4;
const ACTIVE_SCHEDULE_LIMIT = 12;

/**
 * Everything the new-conversation screen needs to describe the pod you are
 * about to give work to.
 *
 * Deliberately built from the same queries Home already runs, so arriving from
 * Home costs nothing new — every list here shares its cache key with the page
 * you came from, and the pod table list is already in flight for the composer's
 * `@`-mentions. Each list is gated on its own permission: a member who cannot
 * read workflows should see a screen without workflows, not a failed request.
 */
export function usePodStartSignals(podId: string): {
    signals: PodStartSignals;
    recentConversations: Conversation[];
    isLoading: boolean;
} {
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);

    const canReadAgents = podAccess.can('agent.read');
    const canReadWorkflows = podAccess.can('workflow.read');
    const canReadSchedules = podAccess.can('schedule.read');
    const canReadConversations = podAccess.can('conversation.read');
    const canReadTables = podAccess.canAccessRoute('data');
    const canReadSurfaces = podAccess.canAccessRoute('surfaces');
    const canReadConnectors = podAccess.canAccessRoute('connectors');

    const { data: agentsData, isLoading: agentsLoading } = useAgents(canReadAgents ? podId : undefined);
    const { data: workflows = [], isLoading: workflowsLoading } = useFlows(canReadWorkflows ? podId : undefined);
    const { data: tablesData, isLoading: tablesLoading } = useTables(podId, undefined, { enabled: canReadTables });
    const { pages: appPages, isLoading: appsLoading } = useAppPages(podId);
    const { data: surfaces = [], isLoading: surfacesLoading } = usePodSurfaces(canReadSurfaces ? podId : undefined);
    const { data: schedulesData, isLoading: schedulesLoading } = useSchedules(
        canReadSchedules ? podId : undefined,
        { isActive: true, limit: ACTIVE_SCHEDULE_LIMIT },
    );
    const { data: conversationsData, isLoading: conversationsLoading } = useScopedConversations(
        { podId },
        { limit: RECENT_CONVERSATION_LIMIT, enabled: canReadConversations },
    );
    const { data: accounts = [], isLoading: accountsLoading } = useAccounts({
        organizationId: pod?.organization_id,
        enabled: canReadConnectors && !!pod?.organization_id,
    });

    const agents = useMemo(() => agentsData?.items ?? [], [agentsData?.items]);
    const tables = useMemo(() => tablesData?.items ?? [], [tablesData?.items]);
    const schedules = useMemo(() => schedulesData?.items ?? [], [schedulesData?.items]);
    const recentConversations = useMemo(() => conversationsData?.items ?? [], [conversationsData?.items]);

    const signals = useMemo<PodStartSignals>(() => ({
        ...EMPTY_POD_START_SIGNALS,
        tables: tables.map((table) => ({ name: table.name })),
        agents: agents.map((agent) => ({
            name: agent.name,
            iconUrl: agent.icon_url ?? null,
        })),
        workflows: workflows.map((workflow) => ({ name: workflow.name })),
        // One mark per app, not per account: two Gmail accounts are still one
        // capability as far as "what can I ask for" is concerned.
        connectors: Array.from(
            new Map(
                accounts
                    .filter((account) => !getAccountStatusMeta(account.status).needsAttention)
                    .map((account) => [
                        account.connector_id,
                        {
                            connectorId: account.connector_id,
                            label: account.connector?.title || account.connector?.name || account.connector_id,
                            icon: account.connector?.icon ?? null,
                        },
                    ]),
            ).values(),
        ),
        appCount: appPages.length,
        surfaceCount: surfaces.length,
        activeSurfaceCount: surfaces.filter((surface) => String(surface.status || '').toUpperCase() === 'ACTIVE').length,
        scheduleCount: schedules.length,
        conversationCount: recentConversations.length,
        hasUsedWorkflow: false,
    }), [accounts, agents, appPages.length, recentConversations.length, schedules.length, surfaces, tables, workflows]);

    return {
        signals,
        recentConversations,
        isLoading:
            agentsLoading
            || workflowsLoading
            || tablesLoading
            || appsLoading
            || surfacesLoading
            || schedulesLoading
            || conversationsLoading
            || accountsLoading,
    };
}
