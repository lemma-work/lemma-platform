"use client";

import { useState } from "react";
import { ArrivalFlow, useArrivalReads, type Landing } from "@/arrival/flow";
import { isLocalDeployment } from "@/site/config";
import { captureEvent } from "@/site/analytics/client";
import { WorkspaceLoading } from "@/shell/workspace-loading";
import { NotYours } from "@/shell/not-yours";
import { createLemma, freshFace } from "./data";
import { arrivalStep } from "./arrival-step";

/** The organization's own Lemma, made with it: open to everyone in it, and
 *  joined by everyone who comes later, as a chat app's first channel is. So
 *  nobody lands in an empty container -- they land in a Lemma, on its
 *  welcome. Answers its id, or null when it could not be made: the
 *  organization exists either way, and the Lemmas page offers to make one. */
export async function firstLemma(orgId: string, name: string): Promise<string | null> {
    try {
        const made = await createLemma({ orgId, name, purpose: "", face: freshFace([]), reach: "org", people: [], everyone: true });
        return made.podId;
    } catch {
        return null;
    }
}

/** The frame's way in for somebody who belongs nowhere yet.
 *
 *  The organization is a container, not a decision a first-time person can
 *  make well, so it is asked about only when there is a real choice. The
 *  server makes it by one rule for every way in (`ensureFirstWorkspace`):
 *  invitations accepted, else their company's organization if it is open to
 *  their email domain, else a new one with a Lemma everyone in it shares.
 *  Their company's organization is a real choice (joining shows them to
 *  colleagues), so that one waits for them; everything else happens as soon as
 *  the screen is up, under one sentence about what a Lemma is.
 *
 *  **A link wins over a place of their own.** Somebody who opens a Lemma's
 *  address before belonging anywhere came for that Lemma. With a link and no
 *  invitation, the first thing they see is that Lemma's door: an open one
 *  lets them in (and into its organization), a shut one takes their request.
 *  Starting their own stays one click away beneath it. With invitations, they
 *  are accepted first and the link is still where they land. */
export function FrameArrival({ linkedPodId, asksWork, asksName, onEntered }: {
    /** The Lemma the address they opened names, if it names one. */
    linkedPodId: string | null;
    /** Whether "What do you do?" is still ahead of them. */
    asksWork: boolean;
    /** The account has no name yet. */
    asksName: boolean;
    /** Where they landed: the Lemma they came for, were invited to or that
     *  everyone in their organization shares, or none for the list of
     *  Lemmas. */
    onEntered: (landing: Landing) => void | Promise<void>;
}) {
    const { invitations, suggested } = useArrivalReads();
    /* Turned down the link's door for a place of their own. */
    const [ownInstead, setOwnInstead] = useState(false);
    const failed = invitations.isError || suggested.isError;

    const step = arrivalStep({
        invitations: invitations.isSuccess ? invitations.data.length : failed ? 0 : null,
        suggested: suggested.isSuccess ? suggested.data.length : failed ? 0 : null,
        linkedPodId,
        ownInstead,
    });

    if (step === "knock" && linkedPodId) {
        return (
            <div className="ox-alone">
                <NotYours
                    podId={linkedPodId}
                    onArrived={() => {
                        captureEvent("onboarding.arrived", { route: "link" });
                        onEntered({ orgId: null, podId: linkedPodId });
                    }}
                    aside={
                        <button type="button" className="ox-link" onClick={() => setOwnInstead(true)}>
                            Start your own Lemma instead
                        </button>
                    }
                />
            </div>
        );
    }
    if (step === "wait") return <WorkspaceLoading />;
    /* A list that could not be read is not an empty one, but it is no reason
       to stop: the server reads invitations and the company's organization
       again for itself when it makes the workspace. */
    return (
        <ArrivalFlow
            start={{
                kind: "first",
                invitations: invitations.data?.length ?? 0,
                suggested: suggested.data ?? [],
                local: isLocalDeployment(),
            }}
            asksWork={asksWork}
            asksName={asksName}
            onEntered={(landing) => onEntered(linkedPodId && !landing.startNew ? { ...landing, podId: linkedPodId } : landing)}
        />
    );
}
