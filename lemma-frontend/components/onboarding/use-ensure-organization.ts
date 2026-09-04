"use client";

import { useCallback } from "react";

import {
  useCreateOrganization,
  useJoinSuggestedOrganization,
} from "@/lib/hooks/use-organizations";
import { OrganizationJoinPolicy } from "@/lib/types";
import { normalizeEmailDomain, workDomainFromEmail } from "@/lib/utils/organization-slugs";
import type { OnboardingEntryKind } from "@/lib/analytics/onboarding";

import { organizationNameCandidate } from "./account-onboarding-helpers";

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
 * Ids in and an id out, deliberately: the caller here holds a full
 * `Organization`, the importer holds the slimmer navigation shape, and an id is
 * the only thing either of them actually needs from this.
 */
export function useEnsureOrganization() {
  const createOrganization = useCreateOrganization();
  const joinSuggestedOrganization = useJoinSuggestedOrganization();

  return useCallback(
    async ({
      email,
      organizationIds,
      suggestedOrganizationId,
    }: {
      email?: string | null;
      organizationIds: string[];
      suggestedOrganizationId?: string | null;
    }): Promise<EnsuredOrganization | null> => {
      const existing = organizationIds[0];
      if (existing) return { organizationId: existing, entryKind: "new_org" };

      if (suggestedOrganizationId) {
        // A colleague already claimed this domain. Joining them beats
        // fragmenting the company across two workspaces.
        const joined = await joinSuggestedOrganization.mutateAsync(
          suggestedOrganizationId,
        );
        return joined?.id
          ? { organizationId: joined.id, entryKind: "domain_join" }
          : null;
      }

      const workDomain = normalizeEmailDomain(workDomainFromEmail(email || ""));
      const created = await createOrganization.mutateAsync({
        name: organizationNameCandidate({ email: email || "", workDomain }),
        join_policy: workDomain
          ? OrganizationJoinPolicy.EMAIL_DOMAIN
          : OrganizationJoinPolicy.INVITE_ONLY,
        email_domain: workDomain || null,
        resolve_name_conflicts: true,
      });
      return created?.id
        ? { organizationId: created.id, entryKind: "new_org" }
        : null;
    },
    [createOrganization, joinSuggestedOrganization],
  );
}
