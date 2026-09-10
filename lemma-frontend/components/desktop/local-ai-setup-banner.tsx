"use client";

import { Button } from "@/components/ui/button";
import { useOrganization } from "@/components/dashboard/org-context";
import { isLocalDeployment } from "@/lib/config";
import { useAutoConnectThisComputer } from "@/lib/desktop/auto-connect";
import { openLocalSettings, useLocalAiStatus } from "@/lib/desktop/local-capabilities";
import { useThisComputer } from "@/lib/desktop/this-computer";
import { useManagedAgentRuntimes } from "@/lib/hooks/use-agent-runtime";
import { RuntimeProfileKind, RuntimeProfileStatus } from "lemma-sdk";
import { isReadyLocalAgent } from "@/lib/desktop/local-agent-default";
import Link from "next/link";

/**
 * "Configure an AI provider", but only when that is actually true.
 *
 * The capability probe behind this answers one narrow question — is the
 * installation's operator config pointing at a provider — and that is not the
 * same question as "can this person use an agent". Someone who connected Claude
 * Code has a working agent and no operator provider, and used to be told
 * forever that they had to configure one.
 *
 * This is also where the Agent Host gets connected. Every authenticated page
 * mounts it through `protected-route`, so by the time anyone looks at Models or
 * onboarding, this computer has already paired itself and scanned.
 */
export function LocalAiSetupBanner() {
    const local = isLocalDeployment();
    // A hook, not `thisComputer()`: this banner is `sticky top-0` on every
    // page, so a server/client disagreement here is a hydration mismatch on
    // every page.
    const computerNoun = useThisComputer();
    // Runs before the early return, and deliberately outside the `local` gate:
    // connecting this computer is not conditional on whether the banner has
    // anything to say, nor on the workspace being the one on this machine. A
    // hosted workspace opened in the app has the same laptop to offer it.
    useAutoConnectThisComputer();

    const { currentOrg } = useOrganization();
    const { status } = useLocalAiStatus(local);
    const managed = useManagedAgentRuntimes(local ? currentOrg?.id : null);

    if (!local || status !== "needs_setup" || managed.isPending) return null;

    const profiles = managed.data?.items ?? [];
    if (profiles.some(isReadyLocalAgent)) return null;
    const hasCodingAgent = profiles.some(
        (profile) => profile.kind === RuntimeProfileKind.HARNESS && profile.status !== RuntimeProfileStatus.DISABLED,
    );

    return (
        <aside className="state-surface-warning sticky top-0 z-[70] flex min-h-12 items-center justify-between gap-4 px-5 py-2.5 text-sm">
            <span>
                {hasCodingAgent ? (
                    <><strong>Your coding agents aren&apos;t ready.</strong>{" "}
                        Open Models to check their computer, sign-in, and setup status.</>
                ) : (
                    <><strong>No model is set up yet.</strong>{" "}
                        Connect a coding agent on {computerNoun} or an API provider to start chatting.</>
                )}
            </span>
            {hasCodingAgent && currentOrg ? <Button asChild variant="secondary" size="sm" className="shrink-0">
                <Link href={`/organizations/${currentOrg.id}/settings/agent-runtimes`}>Open Models</Link>
            </Button> : <Button
                variant="secondary"
                type="button"
                size="sm"
                className="shrink-0"
                onClick={() => void openLocalSettings("ai")}
            >
                Set up
            </Button>}
        </aside>
    );
}
