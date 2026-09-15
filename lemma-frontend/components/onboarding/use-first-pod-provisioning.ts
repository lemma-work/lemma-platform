"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getLemmaClient } from "@/lib/sdk/lemma-client";
import { useUpdateProfile } from "@/lib/hooks/use-user";
import { trackPodReady } from "@/lib/analytics/onboarding";
import { buildNewPodWelcomeHref } from "@/lib/pods/new-pod-conversation";
import { type Organization } from "@/lib/types";
import { normalizeEmailDomain, workDomainFromEmail } from "@/lib/utils/organization-slugs";

import {
  firstPodName,
  hasUsableProfileName,
  inferFullName,
  splitName,
} from "./account-onboarding-helpers";
import { useEnsureOrganization } from "./use-ensure-organization";

/**
 * `running` until the pod exists, `ready` once it does and nobody has asked to
 * go in yet, `navigated` once somebody has. `failed` is the caller's cue to
 * stop waiting and show the old flow instead.
 */
export type ProvisioningState = "running" | "ready" | "navigated" | "failed";

export interface FirstPodProvisioning {
  state: ProvisioningState;
  /**
   * Go into the pod. Immediately if it exists; the moment it does otherwise.
   *
   * The screen in front of provisioning decides when, not this hook. It used
   * to navigate the instant the pod was created, which was right when the
   * screen in front was a spinner and wrong once it became something a person
   * is reading: a screen that yanks itself away mid-sentence because a request
   * finished is worse than a spinner.
   */
  open: () => void;
}

/**
 * Give a new account a workspace without asking it anything.
 *
 * Every question the old flow asked here had an answer already: the provider
 * sent the name, the email says which company, and the pod is theirs by
 * definition. So this runs while something else is on screen rather than in
 * front of it, and the naming choices it makes are all renameable from inside,
 * which is what makes it safe to make them silently.
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
}): FirstPodProvisioning {
  const router = useRouter();
  const queryClient = useQueryClient();
  const updateProfile = useUpdateProfile();
  const ensureOrganization = useEnsureOrganization();
  const [state, setState] = useState<ProvisioningState>("running");
  // Where the pod opens, once it exists. A ref rather than state because
  // `open` has to read it from inside a click handler without re-subscribing.
  const hrefRef = useRef<string | null>(null);
  // Set when `open` is called before the pod exists, so the navigation happens
  // the moment it does rather than waiting for a second click.
  const wantsOpenRef = useRef(false);
  const navigatedRef = useRef(false);
  // Provisioning must happen once per mount even though its inputs change
  // underneath it — creating the organization is itself one of those changes.
  const startedRef = useRef(false);

  const navigate = useCallback(
    (href: string) => {
      if (navigatedRef.current) return;
      navigatedRef.current = true;
      // Set before the navigation, not after. Creating the pod invalidated the
      // pods query, which re-rendered the caller with `needsFirstPod` already
      // false — and the child it would then render is the root redirect, which
      // navigates to the bare pod URL and takes the composer launch with it.
      // `navigated` is what keeps the caller holding this screen instead.
      setState("navigated");
      router.replace(href);
    },
    [router],
  );

  const open = useCallback(() => {
    wantsOpenRef.current = true;
    if (hrefRef.current) navigate(hrefRef.current);
  }, [navigate]);

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

        // Still needed below: the welcome door greets a work address by name.
        const workDomain = normalizeEmailDomain(workDomainFromEmail(email));

        const ensured = await ensureOrganization({
          email,
          organizationIds: organizations.map((org) => org.id),
          suggestedOrganizationId: suggestedOrganization?.id ?? null,
        });

        if (!ensured) {
          setState("failed");
          return;
        }

        const { organizationId, entryKind } = ensured;

        // Joining an existing organization still earns a pod of your own:
        // otherwise you land in a workspace where everything belongs to someone
        // else, which is a worse first screen than an empty one. `create_pod`
        // asks only for organization membership, so a domain-joined member may
        // do this — there is no extra permission to clear.
        const pod = await getLemmaClient().pods.create({
          name: firstPodName(profile),
          description:
            "A private workspace for apps, surface agents, knowledge, and operating loops.",
          organization_id: organizationId,
        });

        trackPodReady(entryKind, profile?.created_at ?? null);
        // Into the conversation, not onto pod home: nobody answered a question
        // to get here, so the launcher there has nothing to offer them yet.
        // The conversation opens behind the welcome door rather than opening
        // itself with a greeting — nobody has said anything to answer yet.
        hrefRef.current = buildNewPodWelcomeHref({
          podId: pod.id,
          workDomain,
          isFirstPod: true,
        });
        if (wantsOpenRef.current) {
          navigate(hrefRef.current);
        } else {
          setState("ready");
        }
        // After the state above is queued, never before: the refreshed listing
        // is what would otherwise let the caller fall through to its redirect.
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
        setState("failed");
      }
    })();
  }, [
    enabled,
    ensureOrganization,
    navigate,
    organizations,
    profile,
    queryClient,
    suggestedOrganization,
    updateProfile,
  ]);

  return { state, open };
}
