"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { captureEvent } from "@/site/analytics/client";
import { joinLemma, useJoinable, type JoinableLemma } from "@/outcomes/data";
import { ArrivalRow, InMark, LemmaMark } from "./shell";

/** "Lemmas to join", right after somebody comes into an organization that
 *  already has people in it: invited, or let in by their email domain.
 *
 *  The Lemmas they are in come first, the one they were invited to at the
 *  top, then the ones open to everyone there, each a Join away -- the list
 *  the Lemmas page keeps under "You can join", shown at the moment it is
 *  most use. Making a new one stays one click away beneath. */
export function LemmasStep({ orgId, orgName, invitedPodId, onContinue, onStartNew }: {
    orgId: string;
    orgName: string;
    /** The Lemma their invitation named, which leads the list. */
    invitedPodId: string | null;
    onContinue: () => void;
    onStartNew: () => void;
}) {
    const queryClient = useQueryClient();
    const mine = useQuery({ queryKey: ["pods", orgId], queryFn: () => source.listPods(orgId) });
    const joinable = useJoinable(orgId);
    const [joining, setJoining] = useState<string | null>(null);
    const [joined, setJoined] = useState<ReadonlySet<string>>(new Set());
    const [problem, setProblem] = useState<string | null>(null);

    const rank = (pod: { id: string; everyone?: boolean }) => (pod.id === invitedPodId ? 0 : pod.everyone ? 1 : 2);
    const yours = [...(mine.data ?? [])].sort((a, b) => rank(a) - rank(b));
    const inAlready = new Set(yours.map((one) => one.id));
    const open = (joinable.data ?? []).filter((one) => !inAlready.has(one.id));

    const join = async (lemma: JoinableLemma) => {
        setJoining(lemma.id);
        setProblem(null);
        try {
            await joinLemma(lemma.id);
            setJoined((was) => new Set(was).add(lemma.id));
            captureEvent("onboarding.lemma_joined");
            void queryClient.invalidateQueries({ queryKey: ["pods", orgId] });
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : `Couldn’t join ${lemma.name}.`);
        } finally {
            setJoining(null);
        }
    };

    const reading = mine.isPending || joinable.isPending;
    return (
        <>
            <h1 className="ar__t">You’re in {orgName}</h1>
            <p className="ar__model">Each Lemma is a shared space for part of the work here. Join the ones you’ll work in; you can join more later.</p>
            {reading ? (
                <p className="ar__note" role="status">Reading {orgName}’s Lemmas…</p>
            ) : (
                <>
                    {yours.length > 0 && (
                        <ul className="ar__rows" aria-label="Lemmas you are in">
                            {yours.map((pod) => (
                                <ArrivalRow
                                    key={pod.id}
                                    mark={<LemmaMark id={pod.id} name={pod.name} iconUrl={pod.iconUrl} />}
                                    name={pod.name}
                                    tag={pod.everyone ? "Everyone" : undefined}
                                    line={pod.id === invitedPodId ? "You were invited to this one" : pod.everyone ? `Everyone at ${orgName}` : pod.description ?? "You’re in this one"}
                                >
                                    <InMark />
                                </ArrivalRow>
                            ))}
                        </ul>
                    )}
                    {open.length > 0 && (
                        <>
                            <p className="ar__sub">Open to everyone at {orgName}</p>
                            <ul className="ar__rows" aria-label="Lemmas you can join">
                                {open.map((lemma) => (
                                    <ArrivalRow
                                        key={lemma.id}
                                        mark={<LemmaMark id={lemma.id} name={lemma.name} iconUrl={lemma.iconUrl} />}
                                        name={lemma.name}
                                        line={[lemma.description, peopleSaid(lemma.people)].filter(Boolean).join(" · ")}
                                    >
                                        {joined.has(lemma.id) ? (
                                            <InMark />
                                        ) : (
                                            <button type="button" className="ar__btn ar__btn--small" disabled={joining !== null} onClick={() => void join(lemma)}>
                                                {joining === lemma.id ? "Joining…" : "Join"}
                                            </button>
                                        )}
                                    </ArrivalRow>
                                ))}
                            </ul>
                        </>
                    )}
                    {joinable.isError && <p className="ar__note">Couldn’t read which Lemmas are open to you. They’re on the Lemmas page too.</p>}
                </>
            )}
            {problem && <p className="ar__problem" role="alert">{problem}</p>}
            <div className="ar__acts">
                <button type="button" className="ar__btn ar__btn--primary" disabled={joining !== null} onClick={onContinue}>Continue</button>
                <button type="button" className="ar__quiet" onClick={onStartNew}>Start a new one</button>
            </div>
        </>
    );
}

function peopleSaid(count: number): string {
    if (count <= 0) return "";
    return count === 1 ? "1 person" : `${count} people`;
}
