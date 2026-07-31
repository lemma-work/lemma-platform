import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { AgentRuntimeConfig } from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const agentRuntimeQueryKey = (organizationId?: string | null) =>
    ['agent-runtime', 'runtimes', organizationId ?? null] as const;

export const availableAgentRuntimeHarnessesQueryKey = () =>
    ['agent-runtime', 'available-harnesses'] as const;

export const agentHostsQueryKey = () => ['agent-hosts'] as const;

export const agentHostHarnessesQueryKey = (hostId?: string | null) =>
    ['agent-hosts', hostId ?? null, 'harnesses'] as const;

export type AgentHostStatus = 'ONLINE' | 'OFFLINE' | 'DRAINING' | 'UPGRADE_REQUIRED' | 'REVOKED';

export type AgentHostHarnessHealth =
    | 'READY'
    | 'AUTH_REQUIRED'
    | 'UNSUPPORTED_VERSION'
    | 'CONFIG_INVALID'
    | 'PROBE_FAILED'
    | 'INSTALLING'
    | 'DISABLED';

export interface AgentHostCapacity {
    active_runs?: number;
    available_runs?: number;
    max_runs?: number;
}

export interface AgentHost {
    capacity: AgentHostCapacity;
    created_at: string;
    display_name: string;
    host_release: string;
    id: string;
    installation_id: string;
    last_seen_at: string | null;
    organization_id: string | null;
    protocol_version: number | null;
    revoked_at: string | null;
    status: AgentHostStatus;
    updated_at: string;
    user_id: string;
}

export interface AgentHostConfigOption {
    category: string;
    current_value?: unknown;
    description?: string | null;
    id: string;
    metadata?: Record<string, unknown>;
    name: string;
    options?: Array<Record<string, unknown>>;
}

export interface AgentHostHarness {
    adapter_version: string;
    capabilities: Record<string, unknown>;
    config_options: AgentHostConfigOption[];
    config_revision: string;
    display_name: string;
    harness_key: string;
    // Open string on the wire, so a health value a newer server adds still
    // renders here. Anything outside AgentHostHarnessHealth reads as unusable.
    health: string;
    host_id: string;
    id: string;
    stale_after: string;
    stale_reason: string | null;
    upstream_version: string | null;
}

interface AgentHostListResponse {
    items: AgentHost[];
}

interface AgentHostHarnessListResponse {
    items: AgentHostHarness[];
}

export interface AgentHostPairing {
    expires_at: string;
    pairing_code: string;
    pairing_id: string;
}

// Agent Host has generated OpenAPI operations but no typed SDK namespace yet,
// so these go through the client's documented raw-request escape hatch.
export const useAgentHosts = () => {
    return useQuery({
        queryKey: agentHostsQueryKey(),
        queryFn: () => getLemmaClient().request<AgentHostListResponse>('GET', '/me/runtime/agent-hosts'),
        staleTime: 15000,
        refetchOnWindowFocus: true,
    });
};

export const useAgentHostHarnesses = (hostId?: string | null) => {
    return useQuery({
        queryKey: agentHostHarnessesQueryKey(hostId),
        queryFn: () =>
            getLemmaClient().request<AgentHostHarnessListResponse>(
                'GET',
                `/me/runtime/agent-hosts/${encodeURIComponent(hostId!)}/harnesses`
            ),
        enabled: Boolean(hostId),
        staleTime: 15000,
        refetchOnWindowFocus: true,
    });
};

export const useCreateAgentHostPairing = () => {
    return useMutation({
        mutationFn: ({ organizationId, displayName }: { organizationId?: string | null; displayName: string }) =>
            getLemmaClient().request<AgentHostPairing>('POST', '/me/runtime/agent-host-pairings', {
                body: { display_name: displayName, organization_id: organizationId ?? null },
            }),
    });
};

export const useRevokeAgentHost = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (hostId: string) =>
            getLemmaClient().request<AgentHost>(
                'DELETE',
                `/me/runtime/agent-hosts/${encodeURIComponent(hostId)}`
            ),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: agentHostsQueryKey() });
        },
    });
};

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
