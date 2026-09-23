'use client';

import { useMemo, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

// Hook to fetch all resources for a pod's AI assistant context
import { usePod } from './use-pods';
import { useAgents } from './use-agents';
import { useFunctions } from './use-functions';
import { useFlows } from './use-flows';
import { useDatastores } from './use-datastores';
import { useAppPages } from './use-app';
import { useAccounts } from './use-connectors';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { Datastore } from '@/lib/types';
import type { PodContext, EnrichedDatastore, TableInfo } from '@/lib/types/ai';

// Hook to fetch tables for multiple datastores
function useMultipleDatastoreTables(podId: string | undefined, datastores: { id: string; name: string }[]) {
    const [tablesMap, setTablesMap] = useState<Record<string, TableInfo[]>>({});
    const [isLoading, setIsLoading] = useState(false);
    const queryClient = useQueryClient();

    useEffect(() => {
        if (!podId || datastores.length === 0) {
            return;
        }

        const fetchAllTables = async () => {
            setIsLoading(true);
            const results: Record<string, TableInfo[]> = {};

            try {
                const tablesQueryKey = ['tables', podId] as const;
                const cached = queryClient.getQueryData<{ items: TableInfo[] }>(tablesQueryKey);
                const normalizedTables = cached?.items
                    ? cached.items.map((table) => ({
                        name: table.name,
                        columns: table.columns || [],
                        record_count: table.record_count,
                    }))
                    : (() => {
                        const fetchAndNormalize = async () => {
                            const data = await getLemmaClient(podId).tables.list();
                            const normalized = (data?.items || []).map((table) => {
                                const raw = table as unknown as Record<string, unknown>;
                                const rawColumns = Array.isArray(raw.columns)
                                    ? (raw.columns as Array<Record<string, unknown>>)
                                    : [];

                                return {
                                    name: String(raw.name || ''),
                                    columns: rawColumns.map((column) => ({
                                        name: String(column.name || ''),
                                        type: String(column.type || 'TEXT'),
                                        description: (column.description as string | null | undefined) ?? undefined,
                                        required: (column.required as boolean | undefined) ?? undefined,
                                        unique: (column.unique as boolean | undefined) ?? undefined,
                                    })),
                                    record_count: (raw.record_count as number | undefined) ?? undefined,
                                };
                            });
                            queryClient.setQueryData(tablesQueryKey, data);
                            return normalized;
                        };

                        return fetchAndNormalize();
                    })();

                const resolvedTables = normalizedTables instanceof Promise ? await normalizedTables : normalizedTables;
                datastores.forEach((ds) => {
                    results[ds.id] = resolvedTables;
                });
            } catch (err) {
                datastores.forEach((ds) => {
                    console.warn(`[PodContext] Failed to fetch tables for datastore ${ds.name} (${ds.id}):`, err);
                    results[ds.id] = [];
                });
            }

            setTablesMap(results);
            setIsLoading(false);
        };

        fetchAllTables();
    }, [podId, datastores, queryClient]);

    return { tablesMap, isLoading };
}

interface UsePodContextOptions {
    enabled?: boolean;
}

export function usePodContext(
    podId: string | undefined,
    options: UsePodContextOptions = {}
): {
    context: PodContext | null;
    isLoading: boolean;
} {
    const isEnabled = options.enabled ?? true;
    const scopedPodId = isEnabled ? podId : undefined;

    const { data: pod, isLoading: podLoading } = usePod(scopedPodId);
    const { data: agentsData, isLoading: agentsLoading } = useAgents(scopedPodId);
    const agents = useMemo(() => agentsData?.items || [], [agentsData?.items]);
    const { data: functionsData, isLoading: functionsLoading } = useFunctions(scopedPodId);
    const functions = useMemo(() => functionsData?.items || [], [functionsData?.items]);
    const { data: flowsData, isLoading: flowsLoading } = useFlows(scopedPodId);
    const { data: datastoresData, isLoading: datastoresLoading } = useDatastores(scopedPodId);
    const { pages: appPages, isLoading: appLoading } = useAppPages(scopedPodId || '');
    const { data: accountsData, isLoading: accountsLoading } = useAccounts({
        organizationId: pod?.organization_id,
        enabled: isEnabled && !!scopedPodId && !!pod?.organization_id,
    });

    const allDatastores: Datastore[] = useMemo(() => datastoresData?.items || [], [datastoresData?.items]);
    const datastoreTableRefs = useMemo(
        () => allDatastores.map((d: Datastore) => ({ id: d.id, name: d.name })),
        [allDatastores],
    );

    // Fetch tables for ALL datastores
    const { tablesMap, isLoading: tablesLoading } = useMultipleDatastoreTables(
        scopedPodId,
        datastoreTableRefs
    );



    const isLoading = isEnabled && (podLoading || agentsLoading || functionsLoading || flowsLoading || datastoresLoading || appLoading || tablesLoading || accountsLoading);

    // Check if podId is valid (and not "undefined" string which sometimes happens)
    const isValidPodId = scopedPodId && scopedPodId !== 'undefined';

    // Build enriched datastores with table schemas
    const enrichedDatastores: EnrichedDatastore[] = useMemo(() => {
        if (!isValidPodId) return [];
        return allDatastores.map((ds: Datastore) => ({
            ...ds,
            tables: tablesMap[ds.id] || [],
            isLinked: true, // Legacy flag, kept for compatibility
        }));
    }, [allDatastores, tablesMap, isValidPodId]);

    const context: PodContext | null = useMemo(() => {
        if (!isEnabled || !pod || isLoading) return null;

        return {
            pod,
            agents: agents || [],
            functions: functions || [],
            flows: flowsData || [],
            datastores: enrichedDatastores,
            appPages: appPages || [],
            connectedAccounts: accountsData || [],
        };
    }, [pod, agents, functions, flowsData, enrichedDatastores, appPages, accountsData, isEnabled, isLoading]);

    return { context, isLoading: isEnabled ? isLoading : false };
}
