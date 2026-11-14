"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { ArrivalView } from "@/org/arrival-view";
import { defaultOrgKind, personalNameFor, teamNameFor } from "@/org/arrival";
import { isLocalDeployment } from "@/site/config";
import { captureEvent } from "@/site/analytics/client";
import { WorkspaceLoading } from "@/shell/workspace-loading";
import { createLemma, freshFace } from "./data";

/** What the one Lemma of a workspace for one person is called. A team's is
 *  called what the team is: it is theirs, the one everybody is in. */
const PERSONAL_LEMMA = "Personal";

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
 *  make well, so it is asked about only when there is a real choice. An
 *  invitation is accepted -- somebody asked for this person, and accepting is
 *  what they came to do -- and they land on what they were invited to. With
 *  no invitation and no organization of their company's to join, one is made
 *  for them, by invitation only, with its own Lemma, and they land in that,
 *  on its welcome. A company's organization to join is a real choice (joining
 *  shows them to colleagues), so that one is the workspace's own door, as is
 *  anything that fails on the way. */
export function FrameArrival({ email, name, onEntered }: {
    email: string | null;
    name: string | null;
    /** Where they landed: the Lemma an invitation named or the one made for
     *  them, or null for the list of Lemmas when neither is there. */
    onEntered: (podId: string | null) => void;
}) {
    const queryClient = useQueryClient();
    const invitations = useQuery({ queryKey: ["my-invitations"], queryFn: () => source.myInvitations() });
    const suggested = useQuery({ queryKey: ["suggested-orgs"], queryFn: () => source.suggestedOrgs() });
    const [door, setDoor] = useState(false);
    const begun = useRef(false);

    const ready = invitations.isSuccess && suggested.isSuccess;
    const choice = ready && invitations.data.length === 0 && suggested.data.length > 0;
    useEffect(() => {
        if (invitations.isError || suggested.isError) setDoor(true);
    }, [invitations.isError, suggested.isError]);
    useEffect(() => {
        if (!ready || choice || begun.current) return;
        begun.current = true;
        const invites = invitations.data;
        void (async () => {
            try {
                if (invites.length > 0) {
                    for (const invite of invites) await source.acceptInvitation(invite.id);
                    captureEvent("onboarding.arrived", { route: "invite" });
                    onEntered(invites.find((invite) => invite.podId)?.podId ?? null);
                    return;
                }
                const team = defaultOrgKind(email, isLocalDeployment()) === "team" ? teamNameFor(email) : "";
                const org = await source.createOrg({ name: team || personalNameFor(name, email), derived: true });
                captureEvent("onboarding.arrived", { route: "new_org" });
                onEntered(await firstLemma(org.id, team ? org.name : PERSONAL_LEMMA));
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
    }, [ready, choice, invitations.data, email, name, onEntered, queryClient]);

    if (door || choice) return <ArrivalView email={email} name={name} onEntered={onEntered} />;
    return <WorkspaceLoading />;
}
