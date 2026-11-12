"use client";

import type { ConversationRef, Org, Pod } from "@/data";
import type { Go, Plan, QueueItem } from "./model";
import { to } from "./parts";
import { Board } from "./board";

/** Every Lemma at the organization as a lane. A new one starts from the
 *  shelf, which reads what can be started from the server's templates. */
export function OutcomesList({ org, pods, activity, plans, queue, go }: {
    org: Org;
    pods: readonly Pod[];
    activity: Map<string, ConversationRef[]>;
    plans: Map<string, Plan | null | undefined>;
    queue: readonly QueueItem[];
    go: Go;
}) {
    return (
        <div className="ox-page">
            <header className="ox-list__head">
                <h1 className="ox-title">Your Lemmas</h1>
                <a className="ox-btn ox-btn--primary" {...to({ name: "hire" }, go)}>New Lemma</a>
            </header>
            <p className="ox-sub">Each one owns something: a bar it holds every week, or a finish line by a date. It builds what that takes, runs it, and asks a person when it needs one.</p>

            <div className="ox-sec">
                <h2>At {org.name}</h2>
                <span className="ox-sec__side"><span className="ox-vs">{pods.length} {pods.length === 1 ? "Lemma" : "Lemmas"}</span><a className="ox-link" {...to({ name: "people" }, go)}>Who is in them</a></span>
            </div>
            {pods.length === 0
                ? <p className="ox-vs">None yet. <a className="ox-link" {...to({ name: "hire" }, go)}>Start the first one</a>.</p>
                : <Board pods={pods} activity={activity} plans={plans} queue={queue} go={go} />}
        </div>
    );
}
