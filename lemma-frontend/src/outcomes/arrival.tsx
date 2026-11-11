"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { ArrivalView } from "@/org/arrival-view";
import { defaultOrgKind, personalNameFor, teamNameFor } from "@/org/arrival";
import { isLocalDeployment } from "@/site/config";
import { captureEvent } from "@/site/analytics/client";
import { WorkspaceLoading } from "@/shell/workspace-loading";
import { NotYours } from "@/shell/not-yours";

/** What the arrival screen does next, decided from what it has read.
 *
 *  - `wait`: the reads are not back yet.
 *  - `knock`: they opened a Lemma's link and nobody invited them, so that
 *    Lemma's door comes first.
 *  - `choose`: their company's organization could take them, which is a
 *    choice for them to make.
 *  - `begin`: accept their invitations, or make them an organization. */
export type ArrivalStep = "wait" | "knock" | "choose" | "begin";

export function arrivalStep({ invitations, suggested, linkedPodId, ownInstead }: {
    invitations: number | null;
    suggested: number | null;
    linkedPodId: string | null;
    ownInstead: boolean;
}): ArrivalStep {
    if (invitations === null || suggested === null) return "wait";
    if (invitations > 0) return "begin";
    if (linkedPodId && !ownInstead) return "knock";
    return suggested > 0 ? "choose" : "begin";
}

/** The frame's way in for somebody who belongs nowhere yet.
 *
 *  The organization is a container, not a decision a first-time person can
 *  make well, so it is asked about only when there is a real choice. An
 *  invitation is accepted -- somebody asked for this person, and accepting is
 *  what they came to do -- and they land on what they were invited to. With
 *  no invitation and no organization of their company's to join, one is made
 *  for them, by invitation only, and they land on the shelf to start their
 *  first Lemma. A company's organization to join is a real choice (joining
 *  shows them to colleagues), so that one is the workspace's own door, as is
 *  anything that fails on the way.
 *
 *  **A link wins over the shelf.** Somebody who opens a Lemma's address
 *  before belonging anywhere came for that Lemma. They used to get an
 *  organization made for them and the shelf, and the link was gone — even for
 *  a Lemma open to anyone. So with a link and no invitation, the first thing
 *  they see is that Lemma's door: an open one lets them in (and into its
 *  organization), a shut one takes their request. Starting their own stays one
 *  click away beneath it. With invitations, they are accepted first and the
 *  link is still where they land. */
export function FrameArrival({ email, name, linkedPodId, onEntered }: {
    email: string | null;
    name: string | null;
    /** The Lemma the address they opened names, if it names one. */
    linkedPodId: string | null;
    /** Where they landed: the Lemma they came for or were invited to, or
     *  null for the shelf. */
    onEntered: (podId: string | null) => void;
}) {
    const queryClient = useQueryClient();
    const invitations = useQuery({ queryKey: ["my-invitations"], queryFn: () => source.myInvitations() });
    const suggested = useQuery({ queryKey: ["suggested-orgs"], queryFn: () => source.suggestedOrgs() });
    const [door, setDoor] = useState(false);
    /* Turned down the link's door for a place of their own. */
    const [ownInstead, setOwnInstead] = useState(false);
    const begun = useRef(false);

    const step = arrivalStep({
        invitations: invitations.isSuccess ? invitations.data.length : null,
        suggested: suggested.isSuccess ? suggested.data.length : null,
        linkedPodId,
        ownInstead,
    });
    const knocking = step === "knock";
    const choice = step === "choose";
    useEffect(() => {
        if (invitations.isError || suggested.isError) setDoor(true);
    }, [invitations.isError, suggested.isError]);
    useEffect(() => {
        if (step !== "begin" || begun.current) return;
        begun.current = true;
        const invites = invitations.data ?? [];
        void (async () => {
            try {
                if (invites.length > 0) {
                    for (const invite of invites) await source.acceptInvitation(invite.id);
                    captureEvent("onboarding.arrived", { route: "invite" });
                    onEntered(linkedPodId ?? invites.find((invite) => invite.podId)?.podId ?? null);
                    return;
                }
                const team = defaultOrgKind(email, isLocalDeployment()) === "team" ? teamNameFor(email) : "";
                await source.createOrg({ name: team || personalNameFor(name, email), derived: true });
                captureEvent("onboarding.arrived", { route: "new_org" });
                onEntered(null);
            } catch {
                /* Some invitations may already be accepted: the door has to
                   show what is still waiting, not what was waiting at first. */
                await Promise.all([
                    queryClient.invalidateQueries({ queryKey: ["my-invitations"] }),
                    queryClient.invalidateQueries({ queryKey: ["orgs"] }),
                ]).catch(() => undefined);
                setDoor(true);
            }
        })();
    }, [step, linkedPodId, invitations.data, email, name, onEntered, queryClient]);

    if (knocking && linkedPodId) {
        return (
            <div className="ox-alone">
                <NotYours
                    podId={linkedPodId}
                    onArrived={() => {
                        captureEvent("onboarding.arrived", { route: "link" });
                        onEntered(linkedPodId);
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
    if (door || choice) return <ArrivalView email={email} name={name} onEntered={onEntered} />;
    return <WorkspaceLoading />;
}
