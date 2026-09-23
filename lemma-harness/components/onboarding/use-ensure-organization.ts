"use client";

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import type { OnboardingEntryKind } from "@/lib/analytics/onboarding";

export interface EnsuredOrganization {
  organizationId: string;
  podId: string | null;
  assistantId: string | null;
  entryKind: OnboardingEntryKind;
}

/** Importers request only an organization; first-chat callers also ensure a pod. */
export function useEnsureOrganization() {
  const queryClient = useQueryClient();
  return useCallback(async (options: {
    organizationIds: string[];
    withPod?: boolean;
  }): Promise<EnsuredOrganization | null> => {
    const workspace = await getLemmaClient().users.ensureFirstWorkspace({ with_pod: options.withPod ?? false });
    await queryClient.invalidateQueries({ queryKey: ["organizations"] });
    return {
      organizationId: workspace.organization_id,
      podId: workspace.pod_id ?? null,
      assistantId: workspace.assistant_id ?? null,
      entryKind: workspace.entry === "domain_join" ? "domain_join" : "new_org",
    };
  }, [queryClient]);
}
