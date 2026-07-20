import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AgentRuntimeConfig } from "lemma-sdk";
import { getLemmaClient } from "@/lib/sdk/lemma-client";

export const agentRuntimeQueryKey = (organizationId?: string | null) =>
  ["agent-runtime", "runtimes", organizationId ?? null] as const;

export const availableAgentRuntimeHarnessesQueryKey = () =>
  ["agent-runtime", "available-harnesses"] as const;

export const useAvailableAgentRuntimeHarnesses = () => {
  // The daemon's "GG Coder / Not detected" state can flip several
  // times per minute as the connection comes and goes. Refetching
  // every 15s keeps the Models page honest without spamming the
  // server; window-focus already invalidates too.
  return useQuery({
    queryKey: availableAgentRuntimeHarnessesQueryKey(),
    queryFn: () => getLemmaClient().agentRuntime.listAvailableHarnesses(),
    staleTime: 15000,
    refetchOnWindowFocus: true,
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
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
      request: Parameters<
        ReturnType<typeof getLemmaClient>["agentRuntime"]["createRuntime"]
      >[1];
    }) => getLemmaClient().agentRuntime.createRuntime(organizationId, request),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: agentRuntimeQueryKey(variables.organizationId),
      });
    },
  });
};

export const useUpdatePodDefaultAgentRuntime = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      podId,
      runtime,
    }: {
      podId: string;
      runtime: AgentRuntimeConfig | null;
    }) =>
      getLemmaClient().pods.update(podId, {
        config: {
          // Persist the full runtime (profile + optional model). The
          // backend mirrors profile_id into the legacy default_profile_id.
          default_runtime: runtime,
        },
      }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ["pods"] });
      queryClient.invalidateQueries({ queryKey: ["pods", variables.podId] });
    },
  });
};

export const useUpdatePodDefaultRuntimeProfile =
  useUpdatePodDefaultAgentRuntime;
