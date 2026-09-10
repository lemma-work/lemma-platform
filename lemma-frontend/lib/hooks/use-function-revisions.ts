'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '../sdk/lemma-client';

export interface FunctionRevision {
    id: string;
    revision_number: number;
    revision_hash: string;
    label?: string | null;
    created_at?: string | null;
    is_live: boolean;
    /** Set once retention removed this revision's artifact. */
    pruned_at?: string | null;
    code?: string | null;
    input_schema?: Record<string, unknown> | null;
    output_schema?: Record<string, unknown> | null;
}

export const functionRevisionsQueryKey = (podId: string, functionName: string) =>
    ['function-revisions', podId, functionName] as const;

export function useFunctionRevisions(podId: string, functionName: string, enabled = true) {
    return useQuery({
        queryKey: functionRevisionsQueryKey(podId, functionName),
        enabled: Boolean(podId && functionName) && enabled,
        queryFn: async (): Promise<FunctionRevision[]> => {
            const response = await getLemmaClient(podId).functions.revisions.list(
                functionName,
            ) as { items?: FunctionRevision[] };
            return Array.isArray(response?.items) ? response.items : [];
        },
    });
}

export function useFunctionRevision(
    podId: string,
    functionName: string,
    revisionRef: string | null,
) {
    return useQuery({
        queryKey: ['function-revision', podId, functionName, revisionRef],
        enabled: Boolean(podId && functionName && revisionRef),
        queryFn: async (): Promise<FunctionRevision> => {
            return getLemmaClient(podId).functions.revisions.get(
                functionName,
                revisionRef as string,
            ) as Promise<FunctionRevision>;
        },
    });
}

export interface PromoteRevisionResult {
    revision: FunctionRevision;
    /**
     * True when this revision's schemas differ from the ones that were live.
     * The schemas move with the revision, so agents and workflows built against
     * the old contract may need updating.
     */
    schema_changed: boolean;
}

export function usePromoteFunctionRevision(podId: string, functionName: string) {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (revisionRef: string) => {
            return getLemmaClient(podId).functions.revisions.promote(
                functionName,
                revisionRef,
            ) as Promise<PromoteRevisionResult>;
        },
        onSuccess: () => {
            // The live pointer moved and the function's code and schemas moved
            // with it, so the function itself is stale, not just this list.
            void queryClient.invalidateQueries({
                queryKey: functionRevisionsQueryKey(podId, functionName),
            });
            void queryClient.invalidateQueries({
                queryKey: ['functions', podId, functionName],
            });
        },
    });
}
