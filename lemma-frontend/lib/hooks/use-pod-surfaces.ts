'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { CatalogSurface } from '@/lib/surfaces/catalog';
import type { AssistantSurface } from '@/lib/types';
import type {
    AvailableSurfaceChannelsResponse,
    SetDefaultSurfaceRequest,
    SurfaceCreateRequest,
    SurfacePlatform,
    SurfacePlatformSetupGuide,
    SurfaceSetupResponse,
    SurfaceUpdateRequest,
    TelegramManagedBotSetupRequest,
    TelegramManagedBotSetupResponse,
    UserSurfacesResponse,
} from 'lemma-sdk';

export type SurfacePlatformValue = `${SurfacePlatform}`;

export interface CreatePodSurfaceInput {
    podId: string;
    data: SurfaceCreateRequest;
}

export interface UpdatePodSurfaceInput {
    podId: string;
    surfaceName: string;
    data: SurfaceUpdateRequest;
}

export interface StartTelegramManagedBotSetupInput {
    podId: string;
    data: TelegramManagedBotSetupRequest;
}

const surfacesKey = (podId: string) => ['pod-surfaces', podId];
const setupPrefix = (podId: string) => ['pod-surface-setup', podId];
const channelsPrefix = (podId: string) => ['pod-surface-channels', podId];
const userSurfacesKey = () => ['user-surfaces'];

/** Invalidate every read that a surface write can affect for one pod. */
function invalidatePodSurfaces(
    queryClient: ReturnType<typeof useQueryClient>,
    podId: string,
) {
    queryClient.invalidateQueries({ queryKey: surfacesKey(podId) });
    // Setup/channels reads are keyed by surface name (or platform for the
    // pre-creation guide); a prefix match invalidates them all for the pod.
    queryClient.invalidateQueries({ queryKey: setupPrefix(podId) });
    queryClient.invalidateQueries({ queryKey: channelsPrefix(podId) });
    queryClient.invalidateQueries({ queryKey: userSurfacesKey() });
}

/**
 * The connectable-surface catalog for a pod: which platforms this deployment can
 * actually run, the credential schema to connect an account, and whether the org
 * has already claimed each platform's Lemma-managed bot/number. Drives the setup
 * modal so a platform the deployment can't support is never offered.
 */
export const useAvailableSurfaces = (podId: string | undefined, enabled = true) => {
    return useQuery({
        queryKey: ['pod-available-surfaces', podId],
        queryFn: async () => {
            const response = await getLemmaClient().podSurfaces.available(podId!);
            return (response.surfaces ?? []) as CatalogSurface[];
        },
        enabled: Boolean(podId) && enabled,
        // The claim half changes whenever any pod in the org connects a surface,
        // so keep this fresher than the per-pod list.
        staleTime: 15_000,
    });
};

export const usePodSurfaces = (podId: string | undefined) => {
    return useQuery({
        queryKey: ['pod-surfaces', podId],
        queryFn: async () => {
            const response = await getLemmaClient().podSurfaces.list(podId!);
            return (response.items || []) as AssistantSurface[];
        },
        enabled: !!podId,
        // Shared across the surfaces page, pod home, and agent detail; a short
        // stale window avoids refetching the same pod-wide list on every mount.
        staleTime: 60_000,
    });
};

/**
 * Live setup read for an *existing* surface (addressed by its pod-unique name):
 * live status + webhook info + admin-consent + the platform checklist, in one
 * call. For the pre-creation checklist (before any surface exists) use
 * {@link useSurfaceSetupGuide}.
 */
export const useSurfaceSetup = (
    podId: string,
    surfaceName: string | null | undefined,
    enabled = true
) => {
    return useQuery({
        queryKey: [...setupPrefix(podId), surfaceName],
        queryFn: () =>
            getLemmaClient().podSurfaces.setup(podId, surfaceName as string) as Promise<SurfaceSetupResponse>,
        enabled: Boolean(podId && surfaceName && enabled),
    });
};

/**
 * Pre-creation platform checklist (env/OAuth prerequisites), keyed by platform.
 * Works before any surface of the platform exists.
 */
export const useSurfaceSetupGuide = (
    podId: string,
    platform: SurfacePlatformValue | null | undefined,
    enabled = true
) => {
    return useQuery({
        queryKey: [...setupPrefix(podId), `guide:${platform}`],
        queryFn: () =>
            getLemmaClient().podSurfaces.setupGuide(podId, platform as string) as Promise<SurfacePlatformSetupGuide>,
        enabled: Boolean(podId && platform && enabled),
    });
};

/** Live channels/groups an existing surface can be routed to (Slack/Teams). */
export const useSurfaceChannels = (
    podId: string,
    surfaceName: string | null | undefined,
    enabled = true
) => {
    return useQuery({
        queryKey: [...channelsPrefix(podId), surfaceName],
        queryFn: () =>
            getLemmaClient().podSurfaces.channels(podId, surfaceName as string) as Promise<AvailableSurfaceChannelsResponse>,
        enabled: Boolean(podId && surfaceName && enabled),
        staleTime: 60 * 1000,
    });
};

/** Provision a new surface. `name` defaults to the lowercased platform. */
export const useCreatePodSurface = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ podId, data }: CreatePodSurfaceInput) =>
            getLemmaClient().podSurfaces.create(podId, data),
        onSuccess: (_data, vars) => invalidatePodSurfaces(queryClient, vars.podId),
    });
};

export const useStartTelegramManagedBotSetup = () => {
    return useMutation({
        mutationFn: ({ podId, data }: StartTelegramManagedBotSetupInput) =>
            getLemmaClient().podSurfaces.startTelegramBotSetup(
                podId,
                data,
            ) as Promise<TelegramManagedBotSetupResponse>,
    });
};

export function telegramManagedBotSetupPollInterval(
    status: TelegramManagedBotSetupResponse['status'] | undefined,
    queryStatus: 'pending' | 'error' | 'success',
): number | false {
    if (
        queryStatus === 'error' ||
        status === 'COMPLETE' ||
        status === 'FAILED'
    ) {
        return false;
    }
    return 1500;
}

export const useTelegramManagedBotSetup = (
    podId: string,
    setupId: string | null | undefined,
) => {
    return useQuery({
        queryKey: ['telegram-managed-bot-setup', podId, setupId],
        queryFn: () =>
            getLemmaClient().podSurfaces.getTelegramBotSetup(
                podId,
                setupId as string,
            ) as Promise<TelegramManagedBotSetupResponse>,
        enabled: Boolean(podId && setupId),
        refetchInterval: (query) =>
            telegramManagedBotSetupPollInterval(
                query.state.data?.status,
                query.state.status,
            ),
        refetchIntervalInBackground: true,
    });
};

/**
 * Partial update of an existing surface (addressed by name): config, agent,
 * account, credential mode, channel routes, send policy, and enable/disable.
 */
export const useUpdatePodSurface = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ podId, surfaceName, data }: UpdatePodSurfaceInput) =>
            getLemmaClient().podSurfaces.update(podId, surfaceName, data),
        onSuccess: (_data, vars) => invalidatePodSurfaces(queryClient, vars.podId),
    });
};

/** Enable/disable convenience: a thin update that only flips is_enabled. */
export const useTogglePodSurface = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ podId, surfaceName, isActive }: { podId: string; surfaceName: string; isActive: boolean }) =>
            getLemmaClient().podSurfaces.update(podId, surfaceName, { is_enabled: isActive }),
        onSuccess: (_data, vars) => invalidatePodSurfaces(queryClient, vars.podId),
    });
};

/** Delete removes the surface entirely, freeing its account for another pod. */
export const useDeletePodSurface = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: ({ podId, surfaceName }: { podId: string; surfaceName: string }) =>
            getLemmaClient().podSurfaces.delete(podId, surfaceName),
        onSuccess: (_data, vars) => invalidatePodSurfaces(queryClient, vars.podId),
    });
};

/**
 * Proactively message a pod member over an existing thread on this surface.
 * Fails (404) when the member has no reachable conversation on the surface —
 * there is no cold-DM path.
 */
export const useSendSurfaceMessage = () => {
    return useMutation({
        mutationFn: ({ podId, surfaceName, userId, message }: { podId: string; surfaceName: string; userId: string; message: string }) =>
            getLemmaClient().podSurfaces.send(podId, surfaceName, { user_id: userId, message }),
    });
};

/** My surfaces across every pod I belong to, grouped by platform. */
export const useUserSurfaces = (enabled = true) => {
    return useQuery({
        queryKey: userSurfacesKey(),
        queryFn: () => getLemmaClient().userSurfaces.list() as Promise<UserSurfacesResponse>,
        enabled,
    });
};

/** Pick which surface answers me on a platform when several could. */
export const useSetDefaultSurface = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (payload: SetDefaultSurfaceRequest) =>
            getLemmaClient().userSurfaces.setDefault(payload),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: userSurfacesKey() }),
    });
};
