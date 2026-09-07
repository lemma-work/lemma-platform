"use client";

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { buildApiUrl } from "@/components/auth/portal/auth/config";
import type { OnboardingEntryKind } from "@/lib/analytics/onboarding";

export interface EnsuredOrganization {
  organizationId: string;
  entryKind: OnboardingEntryKind;
}

/**
 * Give an account a workspace without asking it anything.
 *
 * Extracted from first-pod provisioning because signing up is no longer the only
 * way in. Someone who arrives on `/import/github/...` and signs up from there is
 * returned to that page, never passes through the root route, and so never meets
 * `AccountOnboarding` — the only thing that used to create a workspace. Every
 * such account reached the installer with no organization to install into, a
 * disabled button, and the words "No workspace available".
 *
 * The organization half only. A first pod is the root route's answer to landing
 * in an empty account; the importer is about to create a pod of its own, and a
 * spare one beside it is clutter rather than a welcome.
 *
 * The decision itself now lives in the backend. It used to live here — find an
 * organization, prefer one that already claimed the email domain, otherwise
 * invent a name and create one — but chat surfaces onboard people who never load
 * this app at all, so the same reasoning had to exist there. Two copies of
 * "which organization does this person belong to" drift from each other inside a
 * release, and the half that drifts is the half nobody is looking at.
 *
 * Raw `fetch`, like the other onboarding mechanics under `/auth/...`: the
 * endpoint is deliberately outside the published spec, so it is not in the SDK.
 */
export function useEnsureOrganization() {
  const queryClient = useQueryClient();

  return useCallback(
    async ({
      organizationIds,
    }: {
      organizationIds: string[];
    }): Promise<EnsuredOrganization | null> => {
      // Already known to belong somewhere: the backend would answer `existing`,
      // and this saves the round trip on the common path.
      const existing = organizationIds[0];
      if (existing) return { organizationId: existing, entryKind: "new_org" };

      const response = await fetch(buildApiUrl("/users/me/first-workspace"), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ with_pod: false }),
      });
      if (!response.ok) return null;

      const workspace = (await response.json()) as {
        organization_id?: string;
        entry?: string;
      };
      if (!workspace.organization_id) return null;

      await queryClient.invalidateQueries({ queryKey: ["organizations"] });
      return {
        organizationId: workspace.organization_id,
        // `existing` and `surface_join` cannot reach here — the first is
        // short-circuited above and the second only happens on a chat surface —
        // so the analytics vocabulary needs no widening.
        entryKind: workspace.entry === "domain_join" ? "domain_join" : "new_org",
      };
    },
    [queryClient],
  );
}
