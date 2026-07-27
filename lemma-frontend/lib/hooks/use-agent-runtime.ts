import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { AgentRuntimeConfig } from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const agentRuntimeQueryKey = (organizationId?: string | null) =>
    ['agent-runtime', 'runtimes', organizationId ?? null] as const;

export const availableAgentRuntimeHarnessesQueryKey = () =>
    ['agent-runtime', 'available-harnesses'] as const;

export const agentHostsQueryKey = () => ['agent-hosts'] as const;

export const agentHostIntegrationsQueryKey = (hostId?: string | null) =>
    ['agent-hosts', hostId ?? null, 'integrations'] as const;

export const useAvailableAgentRuntimeHarnesses = () => {
    return useQuery({
        queryKey: availableAgentRuntimeHarnessesQueryKey(),
        queryFn: () => getLemmaClient().agentRuntime.listAvailableHarnesses(),
        staleTime: 30000,
        refetchOnWindowFocus: true,
    });
};

export const useAgentRuntimes = (organizationId?: string | null) => {
    return useQuery({
        queryKey: agentRuntimeQueryKey(organizationId),
        queryFn: () => getLemmaClient().agentRuntime.listRuntimes(organizationId!),
        enabled: Boolean(organizationId),
        staleTime: 30000,
        refetchOnWindowFocus: true,
    });
};

export const useAgentRuntimeCatalog = useAgentRuntimes;

export const useAgentHosts = () => {
    return useQuery({
        queryKey: agentHostsQueryKey(),
        queryFn: () => getLemmaClient().agentHost.list(),
        staleTime: 15000,
        refetchInterval: 30000,
        refetchOnWindowFocus: true,
    });
};

export const useAgentHostIntegrations = (hostId?: string | null) => {
    return useQuery({
        queryKey: agentHostIntegrationsQueryKey(hostId),
        queryFn: () => getLemmaClient().agentHost.listIntegrations(hostId!),
        enabled: Boolean(hostId),
        staleTime: 15000,
        refetchInterval: 30000,
        refetchOnWindowFocus: true,
    });
};

export const useCreateAgentHostPairing = () => {
    return useMutation({
        mutationFn: ({
            organizationId,
            displayName,
        }: {
            organizationId: string;
            displayName: string;
        }) => getLemmaClient().agentHost.createPairing({
            organization_id: organizationId,
            display_name: displayName,
        }),
    });
};

export const useRevokeAgentHost = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (hostId: string) => getLemmaClient().agentHost.revoke(hostId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: agentHostsQueryKey() });
            queryClient.invalidateQueries({ queryKey: ['agent-runtime', 'runtimes'] });
        },
    });
};

export const useCreateAgentRuntime = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({
            organizationId,
            request,
        }: {
            organizationId: string;
            request: Parameters<ReturnType<typeof getLemmaClient>['agentRuntime']['createRuntime']>[1];
        }) => getLemmaClient().agentRuntime.createRuntime(organizationId, request),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: agentRuntimeQueryKey(variables.organizationId) });
        },
    });
};

export const useUpdatePodDefaultAgentRuntime = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, runtime }: { podId: string; runtime: AgentRuntimeConfig | null }) =>
            getLemmaClient().pods.update(podId, {
                config: {
                    // Persist the full runtime (profile + optional model). The
                    // backend mirrors profile_id into the legacy default_profile_id.
                    default_runtime: runtime,
                },
            }),
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['pods'] });
            queryClient.invalidateQueries({ queryKey: ['pods', variables.podId] });
        },
    });
};

export const useUpdatePodDefaultRuntimeProfile = useUpdatePodDefaultAgentRuntime;
