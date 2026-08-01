import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
    AgentHostHarnessResponse,
    AgentHostPairingCreated,
    AgentHostResponse,
    AgentRuntimeConfig,
} from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const agentRuntimeQueryKey = (organizationId?: string | null) =>
    ['agent-runtime', 'runtimes', organizationId ?? null] as const;

// Deliberately a different key from the catalog above. runtimeCatalogToModelOptions
// flattens every catalog item into a pickable chat model with no status filter, so
// writing an archived-inclusive listing into agentRuntimeQueryKey would make archived
// profiles reappear in the composer's model picker.
export const managedAgentRuntimeQueryKey = (
    organizationId?: string | null,
    includeArchived = false,
) => ['agent-runtime', 'managed', organizationId ?? null, includeArchived] as const;

export const agentRuntimeProfileQueryKey = (
    organizationId?: string | null,
    profileId?: string | null,
) => ['agent-runtime', 'profile', organizationId ?? null, profileId ?? null] as const;

export const agentHostsQueryKey = () => ['agent-hosts'] as const;

export const agentHostHarnessesQueryKey = (hostId?: string | null) =>
    ['agent-hosts', hostId ?? null, 'harnesses'] as const;

// Agent Host wire shapes come straight from the SDK's generated models; these
// aliases keep the shorter names the components already read.
export type AgentHost = AgentHostResponse;
export type AgentHostHarness = AgentHostHarnessResponse;
export type AgentHostPairing = AgentHostPairingCreated;

export const useAgentHosts = () => {
    return useQuery({
        queryKey: agentHostsQueryKey(),
        queryFn: () => getLemmaClient().agentHost.list(),
        staleTime: 15000,
        refetchOnWindowFocus: true,
    });
};

export const useAgentHostHarnesses = (hostId?: string | null) => {
    return useQuery({
        queryKey: agentHostHarnessesQueryKey(hostId),
        queryFn: () => getLemmaClient().agentHost.listHarnesses(hostId!),
        enabled: Boolean(hostId),
        staleTime: 15000,
        refetchOnWindowFocus: true,
    });
};

export const useCreateAgentHostPairing = () => {
    return useMutation({
        mutationFn: ({ organizationId, displayName }: { organizationId?: string | null; displayName: string }) =>
            getLemmaClient().agentHost.createPairing({
                display_name: displayName,
                organization_id: organizationId ?? null,
            }),
    });
};

export const useRevokeAgentHost = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (hostId: string) => getLemmaClient().agentHost.revoke(hostId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: agentHostsQueryKey() });
            // Harness-backed profiles live on the revoked host, so their
            // availability changes with it. Without this they keep reporting
            // Active until the 30s-stale catalog happens to refetch.
            queryClient.invalidateQueries({ queryKey: ['agent-runtime'] });
        },
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

/**
 * The management listing behind the org Models page. Unlike the catalog it can
 * include archived profiles, which is why it is keyed separately.
 */
export const useManagedAgentRuntimes = (
    organizationId?: string | null,
    options: { includeArchived?: boolean } = {},
) => {
    const includeArchived = options.includeArchived ?? false;

    return useQuery({
        queryKey: managedAgentRuntimeQueryKey(organizationId, includeArchived),
        queryFn: () =>
            getLemmaClient().agentRuntime.listProfiles(organizationId!, {
                includeDisabled: includeArchived,
            }),
        enabled: Boolean(organizationId),
        staleTime: 30000,
        refetchOnWindowFocus: true,
    });
};

/** One profile with the live harness and host behind it, for the edit dialog. */
export const useAgentRuntimeProfile = (
    organizationId?: string | null,
    profileId?: string | null,
) => {
    return useQuery({
        queryKey: agentRuntimeProfileQueryKey(organizationId, profileId),
        queryFn: () => getLemmaClient().agentRuntime.getProfile(organizationId!, profileId!),
        enabled: Boolean(organizationId) && Boolean(profileId),
        staleTime: 15000,
    });
};

// Every profile mutation invalidates the whole 'agent-runtime' tree: the catalog
// (a rename or archive changes which models the composer offers), the management
// listing, and the single-profile query the open dialog is reading.
const invalidateAgentRuntime = (queryClient: ReturnType<typeof useQueryClient>) => {
    queryClient.invalidateQueries({ queryKey: ['agent-runtime'] });
};

export const useUpdateAgentRuntime = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({
            organizationId,
            profileId,
            request,
        }: {
            organizationId: string;
            profileId: string;
            request: Parameters<ReturnType<typeof getLemmaClient>['agentRuntime']['updateProfile']>[2];
        }) => getLemmaClient().agentRuntime.updateProfile(organizationId, profileId, request),
        onSuccess: () => invalidateAgentRuntime(queryClient),
    });
};

export const useArchiveAgentRuntime = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ organizationId, profileId }: { organizationId: string; profileId: string }) =>
            getLemmaClient().agentRuntime.archiveProfile(organizationId, profileId),
        onSuccess: () => invalidateAgentRuntime(queryClient),
    });
};

export const useRestoreAgentRuntime = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ organizationId, profileId }: { organizationId: string; profileId: string }) =>
            getLemmaClient().agentRuntime.restoreProfile(organizationId, profileId),
        onSuccess: () => invalidateAgentRuntime(queryClient),
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
