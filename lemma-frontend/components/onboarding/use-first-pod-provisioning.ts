"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
  useCreateOrganization,
  useJoinSuggestedOrganization,
} from "@/lib/hooks/use-organizations";
import { useUpdateProfile } from "@/lib/hooks/use-user";
import { trackPodReady, type OnboardingEntryKind } from "@/lib/analytics/onboarding";
import { buildNewPodWelcomeHref } from "@/lib/pods/new-pod-conversation";
import { OrganizationJoinPolicy, type Organization } from "@/lib/types";
import { normalizeEmailDomain, workDomainFromEmail } from "@/lib/utils/organization-slugs";

import {
  firstPodName,
  hasUsableProfileName,
  inferFullName,
  organizationNameCandidate,
  splitName,
} from "./account-onboarding-helpers";

/**
 * Only failure is state.
 *
 * "Working" is not: `enabled` already says provisioning should be running, so a
 * second flag tracking the same fact would be a synchronous setState in an
 * effect body and one more thing to keep in step. The caller needs exactly one
 * answer from here — whether to stop waiting and show the old flow instead.
 */
export type ProvisioningState = "running" | "navigated" | "failed";

/**
 * Give a new account a workspace without asking it anything.
 *
 * Every question the old flow asked here had an answer already: the provider
 * sent the name, the email says which company, and the pod is theirs by
 * definition. So this runs while the pod shell is already on screen rather than
 * in front of it, and the naming choices it makes are all renameable from
 * inside — which is what makes it safe to make them silently.
 *
 * Not transactional, because the client cannot be. The failure it actually has
 * to survive is an organization created and a pod not: on the next load there
 * are organizations and no pods, and this runs again and creates just the pod.
 */
export function useFirstPodProvisioning({
  enabled,
  profile,
  organizations,
  suggestedOrganization,
}: {
  enabled: boolean;
  profile?: {
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    full_name?: string | null;
    created_at?: string | null;
  } | null;
  organizations: Organization[];
  suggestedOrganization: Organization | null;
}): ProvisioningState {
  const router = useRouter();
  const queryClient = useQueryClient();
  const updateProfile = useUpdateProfile();
  const createOrganization = useCreateOrganization();
  const joinSuggestedOrganization = useJoinSuggestedOrganization();
  const [failed, setFailed] = useState(false);
  // Set before the navigation, not after. Creating the pod invalidates the pods
  // query, which re-renders the caller with `needsFirstPod` already false — and
  // the child it then renders is the root redirect, which navigates to the bare
  // pod URL and takes the composer launch with it.
  const [navigated, setNavigated] = useState(false);
  // Provisioning must happen once per mount even though its inputs change
  // underneath it — creating the organization is itself one of those changes.
  const startedRef = useRef(false);

  useEffect(() => {
    if (!enabled || startedRef.current) return;
    startedRef.current = true;

    void (async () => {
      try {
        const email = profile?.email || "";

        // The name is derived, never asked. A provider that sent one has
        // already populated the profile; this covers the rest from the address,
        // and the profile page is where anyone who dislikes it fixes it.
        if (!hasUsableProfileName(profile)) {
          const parsed = splitName(inferFullName(profile));
          if (parsed.firstName) {
            await updateProfile.mutateAsync({
              first_name: parsed.firstName,
              last_name: parsed.lastName || null,
            });
          }
        }

        const workDomain = normalizeEmailDomain(workDomainFromEmail(email));
        let entryKind: OnboardingEntryKind = "new_org";
        let organization = organizations[0] || null;

        if (!organization && suggestedOrganization) {
          // A colleague already claimed this domain. Joining them beats
          // fragmenting the company across two workspaces.
          organization = await joinSuggestedOrganization.mutateAsync(
            suggestedOrganization.id,
          );
          entryKind = "domain_join";
        } else if (!organization) {
          organization = await createOrganization.mutateAsync({
            name: organizationNameCandidate({ email, workDomain }),
            join_policy: workDomain
              ? OrganizationJoinPolicy.EMAIL_DOMAIN
              : OrganizationJoinPolicy.INVITE_ONLY,
            email_domain: workDomain || null,
            resolve_name_conflicts: true,
          });
        }

        if (!organization) {
          setFailed(true);
          return;
        }

        // Joining an existing organization still earns a pod of your own:
        // otherwise you land in a workspace where everything belongs to someone
        // else, which is a worse first screen than an empty one. `create_pod`
        // asks only for organization membership, so a domain-joined member may
        // do this — there is no extra permission to clear.
        const pod = await getLemmaClient().pods.create({
          name: firstPodName(profile),
          description:
            "A private workspace for apps, surface agents, knowledge, and operating loops.",
          organization_id: organization.id,
        });

        setNavigated(true);
        trackPodReady(entryKind, profile?.created_at ?? null);
        // Into the conversation, not onto pod home: nobody answered a question
        // to get here, so the launcher there has nothing to offer them yet.
        // The conversation opens behind the welcome door rather than opening
        // itself with a greeting — nobody has said anything to answer yet.
        router.replace(
          buildNewPodWelcomeHref({
            podId: pod.id,
            workDomain,
            isFirstPod: true,
          }),
        );
        // After the navigation is queued: refreshing the listing first is what
        // hands the race to the redirect this is trying to beat.
        queryClient.invalidateQueries({ queryKey: ["pods"] });
      } catch (error) {
        // Say what went wrong. An earlier version swallowed this and quietly
        // redirected, which turned a rejected pod name into "the pod just did
        // not appear" — invisible from the UI and from the logs alike. The
        // wizard behind this is still the way out; it is not a reason to be
        // silent about why we are falling back to it.
        toast.error(
          error instanceof Error && error.message
            ? `Could not finish setting up your workspace: ${error.message}`
            : "Could not finish setting up your workspace.",
        );
        setFailed(true);
      }
    })();
  }, [
    createOrganization,
    enabled,
    joinSuggestedOrganization,
    organizations,
    profile,
    queryClient,
    router,
    suggestedOrganization,
    updateProfile,
  ]);

  if (failed) return "failed";
  return navigated ? "navigated" : "running";
}
