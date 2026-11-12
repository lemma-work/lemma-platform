"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ConversationRef, Org, Pod } from "@/data";
import { TeammateFace } from "@/space/teammate-face";
import type { FrameActions } from "./actions";
import { joinLemma, useJoinable, type JoinableLemma } from "./data";
import { outcomeView, type Go, type Plan, type QueueItem, type DayCount } from "./model";
import { to } from "./parts";
import { Board } from "./board";

/** Every Lemma at the organization: the ones the reader is in, as lanes, and
 *  under them the ones open to everyone here that they could join -- the way
 *  a chat app lists the channels you are in and lets you browse the rest. */
export function OutcomesList({ org, pods, activity, days, plans, queue, go, actions }: {
    org: Org;
    pods: readonly Pod[];
    activity: Map<string, ConversationRef[]>;
    /** Each Lemma's work a day, where the organization counted it. */
    days: Map<string, DayCount[]> | null;
    plans: Map<string, Plan | null | undefined>;
    queue: readonly QueueItem[];
    go: Go;
    actions: FrameActions;
}) {
    return (
        <div className="ox-page">
            <header className="ox-list__head">
                <h1 className="ox-title">Lemmas at {org.name}</h1>
                <button type="button" className="ox-btn ox-btn--primary" onClick={actions.newLemma}>New Lemma</button>
            </header>
            <p className="ox-sub">Each one holds a team’s chats, pages, tables, files and apps, with an assistant that works in all of them.</p>

            <div className="ox-sec">
                <h2>Yours</h2>
                <span className="ox-sec__side"><span className="ox-vs">{pods.length} {pods.length === 1 ? "Lemma" : "Lemmas"}</span><a className="ox-link" {...to({ name: "people" }, go)}>Who is in them</a></span>
            </div>
            {pods.length === 0
                ? <p className="ox-vs">None yet. <button type="button" className="ox-textbtn" onClick={actions.newLemma}>Make the first one</button>.</p>
                : <Board pods={pods} activity={activity} days={days} plans={plans} queue={queue} go={go} />}
            <Joinable org={org} go={go} />
        </div>
    );
}

/** The Lemmas open to everyone at the organization that the reader is not
 *  in: each one a Join away. Lemmas only their own people can find are not
 *  listed, to anyone else. */
function Joinable({ org, go }: { org: Org; go: Go }) {
    const joinable = useJoinable(org.id);
    const queryClient = useQueryClient();
    const [joining, setJoining] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const list = joinable.data ?? [];
    if (joinable.isPending || (joinable.isSuccess && list.length === 0)) return null;

    const join = async (lemma: JoinableLemma) => {
        setJoining(lemma.id);
        setProblem(null);
        try {
            await joinLemma(lemma.id);
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["pods", org.id] }),
                queryClient.invalidateQueries({ queryKey: ["lemmas-joinable", org.id] }),
            ]).catch(() => undefined);
            go(outcomeView(lemma.id));
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : `Couldn’t join ${lemma.name}.`);
        } finally {
            setJoining(null);
        }
    };

    return (
        <section aria-label="Lemmas you can join">
            <div className="ox-sec"><h2>You can join</h2></div>
            {joinable.isError ? (
                <p className="ox-vs">Couldn’t read which Lemmas are open to you. <button type="button" className="ox-textbtn" onClick={() => void joinable.refetch()}>Try again</button></p>
            ) : (
                <ul className="ox-joinable">
                    {list.map((lemma) => (
                        <li key={lemma.id}>
                            <TeammateFace pod={{ id: lemma.id, name: lemma.name, iconUrl: lemma.iconUrl }} size={32} />
                            <span className="ox-joinable__t">
                                <span>{lemma.name}</span>
                                {lemma.description && <span className="ox-vs">{lemma.description}</span>}
                            </span>
                            <span className="ox-vs ox-num">{lemma.people} {lemma.people === 1 ? "person" : "people"}</span>
                            <button type="button" className="ox-btn ox-btn--small ox-btn--primary" disabled={joining !== null} onClick={() => void join(lemma)}>{joining === lemma.id ? "Joining…" : "Join"}</button>
                        </li>
                    ))}
                </ul>
            )}
            {problem && <p className="ox-note" role="alert">{problem}</p>}
        </section>
    );
}
