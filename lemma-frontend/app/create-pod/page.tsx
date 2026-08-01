"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { ProtectedRoute } from "@/components/auth/protected-route";
import { useOrganization } from "@/components/dashboard/org-context";
import { StartStep } from "@/components/onboarding/account-onboarding-steps";
import {
  derivePodNameFromIntent,
  startPathLaunchConfig,
  type OnboardingStartDetails,
  type OnboardingStartPath,
} from "@/components/onboarding/account-onboarding-helpers";
import { WaitingScreen } from "@/components/shared/loading";
import { FIRST_RUN_DELIGHT } from "@/lib/recipes/recipes";
import {
  normalizeRemixSource,
  remixSourceLabel,
} from "@/lib/remix/app-remix";
import { getLemmaClient } from "@/lib/sdk/lemma-client";

export default function CreatePodPage() {
  return (
    <ProtectedRoute>
      <CreatePodScreen />
    </ProtectedRoute>
  );
}

function CreatePodScreen() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { currentOrg, isLoading } = useOrganization();
  const [isCreating, setIsCreating] = useState(false);
  const remixSource = normalizeRemixSource(searchParams.get("remixSource"));

  const handleChoosePath = async (
    path: OnboardingStartPath,
    details: OnboardingStartDetails,
  ) => {
    if (path === "templates") {
      router.push("/templates");
      return true;
    }
    if (path === "coding-agents") return false;

    if (!currentOrg) {
      toast.error("Create or join an organization before creating a pod");
      return false;
    }

    setIsCreating(true);
    try {
      const config = startPathLaunchConfig(path, details);
      const assistantMessage = remixSource
        ? [
            config.message,
            `Use ${remixSource} as the reference experience. Inspect it before building, preserve the interaction mechanics that matter, and make the result Lemma-native rather than a visual copy.`,
          ].join("\n\n")
        : config.message;
      const podName = derivePodNameFromIntent(details.brief);
      const pod = await getLemmaClient().pods.create({
        name: podName,
        description: details.brief.trim(),
        organization_id: currentOrg.id,
      });
      const params = new URLSearchParams({
        assistantMessage,
        conversationInstructions: [
          FIRST_RUN_DELIGHT,
          `The pod already exists: ${pod.name}. Do not create another pod. Build only inside this pod. Treat the user's structured brief as authoritative, inspect existing resources first, and create the smallest complete working version for the selected surface.`,
        ].join("\n\n"),
        conversationMetadata: JSON.stringify({
          source: "create_screen",
          intent: config.intent,
          first_run: true,
          pod_id: pod.id,
          start_path: path,
          remix_source: remixSource,
        }),
      });

      router.push(`/pod/${pod.id}/conversations/new?${params.toString()}`);
      return true;
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Failed to create pod";
      toast.error(message);
      setIsCreating(false);
      return false;
    }
  };

  if (isLoading) {
    return (
      <main className="setup-shell flex min-h-screen items-center justify-center px-4">
        <WaitingScreen
          title="Opening create"
          description="Finding your current organization."
          className="w-full max-w-lg"
        />
      </main>
    );
  }

  return (
    <StartStep
      isCreating={isCreating}
      onChoosePath={handleChoosePath}
      onBack={() => router.push("/home")}
      initialPath={remixSource ? "internal-app" : undefined}
      initialBrief={
        remixSource
          ? `Recreate the useful app experience from ${remixSourceLabel(remixSource)} inside Lemma`
          : ""
      }
    />
  );
}
