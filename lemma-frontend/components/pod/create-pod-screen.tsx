"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { useOrganization } from "@/components/dashboard/org-context";
import { SetupShell, SetupStandalonePage } from "@/components/onboarding/account-onboarding-chrome";
import {
  startPathComposerLaunch,
  type ComposerStartPath,
} from "@/components/onboarding/account-onboarding-helpers";
import { StepLoader } from "@/components/brand/loader";
import { WaitingScreen } from "@/components/shared/loading";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ArrowRight, Boxes } from "@/components/ui/icons";
import { buildNewPodConversationHref } from "@/lib/pods/new-pod-conversation";
import { normalizeRemixSource, remixSourceLabel } from "@/lib/remix/app-remix";
import { getLemmaClient } from "@/lib/sdk/lemma-client";

/**
 * Making a pod, for someone who has already made one.
 *
 * This route used to render the first-run screen verbatim — four illustrated
 * themes and a required brief — so a returning user replayed onboarding to get
 * their fifth pod, and could not get an empty one at all. The primitive is a
 * name and a button. Everything else is optional, and lives one route later:
 * a fresh pod's home already asks what it should become, with the starter
 * themes and the real composer under the question.
 *
 * The chips are the same start paths as first run, minus the form. They create
 * the pod under the name in the field, then seed that composer with the start
 * of a sentence — see `startPathComposerLaunch`.
 */

const DEFAULT_POD_NAME = "Untitled pod";

const START_CHIPS: Array<{ id: ComposerStartPath; label: string }> = [
  { id: "telegram", label: "Telegram agent" },
  { id: "internal-app", label: "Internal app" },
  { id: "chatgpt", label: "ChatGPT + MCP" },
  { id: "agent-skin", label: "Coding agent skin" },
];

/** What each button is waiting on, so only the pressed one shows a spinner. */
type PendingAction = ComposerStartPath | "create" | "templates";

export function CreatePodScreen({ remixSource: rawRemixSource }: { remixSource: string | null }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { currentOrg, isLoading } = useOrganization();
  const remixSource = normalizeRemixSource(rawRemixSource);
  const [name, setName] = useState(
    remixSource ? `Remix of ${remixSourceLabel(remixSource)}` : "",
  );
  const [pending, setPending] = useState<PendingAction | null>(null);

  /**
   * Every button here is "create the pod, then go somewhere in it". The pod is
   * the point; the destination is the difference. `pending` stays set on
   * success so the screen does not flash back to idle behind the navigation.
   *
   * `fallbackName` is what a start path would call this pod. It only applies to
   * an empty field — a name the user typed always wins.
   */
  const createAndGo = async (
    action: PendingAction,
    destination: (podId: string, podName: string) => string,
    fallbackName = DEFAULT_POD_NAME,
  ) => {
    if (pending) return;
    if (!currentOrg) {
      toast.error("Create or join an organization before creating a pod");
      return;
    }

    setPending(action);
    try {
      const pod = await getLemmaClient().pods.create({
        name: name.trim() || fallbackName,
        organization_id: currentOrg.id,
      });
      queryClient.invalidateQueries({ queryKey: ["pods"] });
      router.push(destination(pod.id, pod.name));
    } catch (error) {
      toast.error(
        error instanceof Error && error.message
          ? error.message
          : "Failed to create pod",
      );
      setPending(null);
    }
  };

  const startFromPath = (path: ComposerStartPath) => {
    const launch = startPathComposerLaunch(path);
    void createAndGo(
      path,
      // Same landing as naming a pod and pressing create. Picking a starting
      // point is a *stronger* statement of intent, so it must not end up
      // somewhere less started than saying nothing did.
      (podId, podName) =>
        buildNewPodConversationHref({
          podId,
          podName,
          isFirstPod: false,
          openingMessage: launch.stem,
          extraInstructions: remixSource
            ? [
                launch.instructions,
                `Use ${remixSource} as the reference experience. Inspect it before building, preserve the interaction mechanics that matter, and make the result Lemma-native rather than a visual copy.`,
              ].join("\n\n")
            : launch.instructions,
          metadata: {
            source: "create_screen",
            intent: launch.intent,
            start_path: path,
            remix_source: remixSource,
          },
        }),
      launch.podName,
    );
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
    <SetupShell fullBleed>
      <SetupStandalonePage onBack={() => router.push("/home")}>
        <div className="m-auto w-full max-w-xl pb-12">
          <h1 className="font-display text-2xl font-medium leading-tight tracking-tight text-[var(--text-primary)] sm:text-3xl">
            New pod
          </h1>
          <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
            {remixSource
              ? `Starting from ${remixSourceLabel(remixSource)}. Name the pod, then say what it should become inside it.`
              : "Name it and go. You can say what it should become once you are inside."}
          </p>

          <form
            className="mt-7"
            onSubmit={(event) => {
              event.preventDefault();
              void createAndGo("create", (podId, podName) =>
                buildNewPodConversationHref({ podId, podName, isFirstPod: false }),
              );
            }}
          >
            <Label
              htmlFor="create-pod-name"
              className="text-sm text-[var(--text-secondary)]"
            >
              Name
            </Label>
            <Input
              id="create-pod-name"
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={DEFAULT_POD_NAME}
              disabled={Boolean(pending)}
              className="setup-detail-input mt-2 h-11 text-sm"
            />

            <Button
              variant="quiet"
              type="submit"
              loading={pending === "create"}
              loadingLabel="Creating pod"
              disabled={Boolean(pending)}
              className="setup-primary-action !flex mt-5 h-11 w-full gap-2 text-sm font-medium"
            >
              Create pod
              <ArrowRight className="h-4 w-4" />
            </Button>
          </form>

          <div className="my-7 flex items-center gap-3 text-xs text-[var(--text-tertiary)]">
            <span className="h-px flex-1 bg-[var(--border-subtle)]" />
            or start from
            <span className="h-px flex-1 bg-[var(--border-subtle)]" />
          </div>

          <div className="flex flex-wrap gap-2">
            {START_CHIPS.map((chip) => (
              <Button
                key={chip.id}
                type="button"
                variant="quiet"
                onClick={() => startFromPath(chip.id)}
                disabled={Boolean(pending)}
                className="setup-detail-choice h-9 w-auto gap-2 px-3 text-sm font-normal"
              >
                {pending === chip.id ? <StepLoader size="sm" /> : null}
                {chip.label}
              </Button>
            ))}
            {/* Creates the pod first. It used to leave for the global template
                gallery, so backing out of that left you with nothing. */}
            <Button
              type="button"
              variant="quiet"
              onClick={() =>
                void createAndGo("templates", (podId) => `/pod/${podId}/recipes`)
              }
              disabled={Boolean(pending)}
              className="setup-detail-choice h-9 w-auto gap-2 px-3 text-sm font-normal"
            >
              {pending === "templates" ? (
                <StepLoader size="sm" />
              ) : (
                <Boxes className="h-4 w-4" />
              )}
              Explore templates
            </Button>
          </div>

          <p className="mt-4 text-xs leading-5 text-[var(--text-tertiary)]">
            These create the pod and open it with the first sentence started for
            you. Nothing is sent until you send it. Leave the name blank and the
            one you pick names the pod.
          </p>
        </div>
      </SetupStandalonePage>
    </SetupShell>
  );
}
