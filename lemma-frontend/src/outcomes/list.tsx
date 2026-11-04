"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source, type ConversationRef, type Org, type Pod } from "@/data";
import { chatView, emptyPlan, outcomeView, type Go, type Plan, type QueueItem } from "./model";
import { askIn, planRequest, savePlan } from "./data";
import { to } from "./parts";
import { Board } from "./board";

interface Starter {
    name: string;
    outcome: string;
    team: string;
    kind: "Every week" | "By a date" | "Every month";
    counts: string;
    builds: string;
    works: string;
}

/** What a new Lemma could own, said plainly. Each card says what it would count
 *  and build; nothing here claims a result. */
const STARTERS: readonly Starter[] = [
    { name: "Reviews", outcome: "Every pull request is reviewed the same day", team: "Engineering", kind: "Every week", counts: "Pull requests reviewed within 8 working hours, from GitHub", builds: "A review queue app, an hourly nudge, a Friday report", works: "GitHub, Slack" },
    { name: "Renewals", outcome: "Every renewal is confirmed 30 days early", team: "Sales", kind: "Every week", counts: "Renewals confirmed before the 30-day mark, from your CRM", builds: "A renewals table, a call list, reminders to account owners", works: "HubSpot, Gmail" },
    { name: "Onboarding", outcome: "Every new customer is live within 14 days", team: "Customers", kind: "Every week", counts: "Customers live within 14 days of signing", builds: "An onboarding tracker, a checklist per customer, nudges", works: "Intercom, Slack" },
    { name: "Support", outcome: "Every customer hears back within a day", team: "Customers", kind: "Every week", counts: "Conversations answered within 24 hours", builds: "A reply queue, drafts for anything waiting, a Friday report", works: "Intercom, Gmail" },
    { name: "Close", outcome: "The books close by the 5th of each month", team: "Finance", kind: "Every month", counts: "Close checklist done by day 5", builds: "A close checklist, a reconciliation table, chases for receipts", works: "Xero, Gmail" },
    { name: "Hiring", outcome: "Two engineers signed by a date you choose", team: "People", kind: "By a date", counts: "Signed offers", builds: "A candidate pipeline, interview booking, scorecard chases", works: "Ashby, Google Calendar" },
];

/** The plan's kind for a starter: a plan holds a weekly bar or a date, and
 *  says neither for a monthly one rather than claim a cadence it is not. */
function kindOfStarter(starter: Starter): Plan["kind"] {
    return starter.kind === "Every week" ? "week" : starter.kind === "By a date" ? "date" : null;
}

function freeName(wanted: string, taken: ReadonlySet<string>): string {
    if (!taken.has(wanted.toLowerCase())) return wanted;
    for (let index = 2; index < 50; index += 1) {
        const candidate = `${wanted} ${index}`;
        if (!taken.has(candidate.toLowerCase())) return candidate;
    }
    return `${wanted} ${Date.now() % 1000}`;
}

/** Every Lemma at the organization as a lane, then what a new one could
 *  own, each said as what it would count and build. */
export function OutcomesList({ org, pods, activity, plans, queue, go }: {
    org: Org;
    pods: readonly Pod[];
    activity: Map<string, ConversationRef[]>;
    plans: Map<string, Plan | null | undefined>;
    queue: readonly QueueItem[];
    go: Go;
}) {
    const queryClient = useQueryClient();
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [own, setOwn] = useState("");

    /* Owning one makes a pod named for it, writes the outcome as its plan,
       and asks its agent to set the rest up: the build happens in that
       chat, with everything the agent can already do, and the reader lands
       in it to watch. */
    const start = async (name: string, outcome: string, kind: Plan["kind"]) => {
        setBusy(name);
        setError(null);
        try {
            const pod = await source.createPod(org.id, freeName(name, new Set(pods.map((one) => one.name.toLowerCase()))), outcome);
            const plan = { ...emptyPlan(outcome), kind, sections: { must: [{ text: outcome }], how: [], who: [], never: [] } };
            await savePlan(pod.id, plan, false).catch(() => undefined);
            const setup = await askIn(pod.id, `Set yourself up to own this: "${outcome}". Look at what is already connected, build the tables, app and schedules it needs, then ${planRequest(outcome).charAt(0).toLowerCase()}${planRequest(outcome).slice(1)}`).catch(() => null);
            await queryClient.invalidateQueries({ queryKey: ["pods", org.id] });
            go(setup ? chatView({ kind: "chat", podId: pod.id, id: setup }) : outcomeView(pod.id));
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : "Couldn’t create it");
        } finally {
            setBusy(null);
        }
    };

    const ownName = (text: string) => {
        const words = text.replace(/[^A-Za-z0-9 ]+/g, " ").trim().split(/\s+/).filter(Boolean);
        const pick = words.find((word) => word.length > 4 && !/^(every|within|before|after|their|there)$/i.test(word)) ?? words[0] ?? "Lemma";
        return pick.charAt(0).toUpperCase() + pick.slice(1).toLowerCase();
    };

    return (
        <div className="ox-page">
            <h1 className="ox-title">Your Lemmas</h1>
            <p className="ox-sub">Each one owns something: a bar it holds every week, or a finish line by a date. It builds what that takes, runs it, and asks a person when it needs one.</p>

            <div className="ox-sec">
                <h2>At {org.name}</h2>
                <span className="ox-sec__side"><span className="ox-vs">{pods.length} {pods.length === 1 ? "Lemma" : "Lemmas"}</span><a className="ox-link" {...to({ name: "people" }, go)}>Who is in them</a></span>
            </div>
            {pods.length === 0 ? <p className="ox-vs">None yet. Start one below.</p> : <Board pods={pods} activity={activity} plans={plans} queue={queue} go={go} />}

            <div className="ox-sec"><h2>What should it own?</h2><span className="ox-vs">Start a new Lemma from one of these, or say your own</span></div>
            <div className="ox-cards">
                {STARTERS.map((starter) => (
                    <article key={starter.name} className="ox-card" aria-label={starter.name}>
                        <p className="ox-card__top"><span className="ox-tag">{starter.team}</span><span className="ox-tag">{starter.kind}</span></p>
                        <p className="ox-card__name">{starter.name}</p>
                        <p className="ox-card__owns">Owns: {starter.outcome.charAt(0).toLowerCase() + starter.outcome.slice(1)}.</p>
                        <dl className="ox-facts">
                            <dt>Counts</dt><dd>{starter.counts}</dd>
                            <dt>Builds</dt><dd>{starter.builds}</dd>
                            <dt>Works in</dt><dd>{starter.works}</dd>
                        </dl>
                        <div className="ox-card__acts">
                            <button type="button" className="ox-btn ox-btn--primary" disabled={busy !== null} onClick={() => start(starter.name, starter.outcome, kindOfStarter(starter))} aria-label={busy === starter.name ? undefined : "Own " + starter.name}>{busy === starter.name ? "Starting…" : "Own this"}</button>
                        </div>
                    </article>
                ))}
            </div>
            <form className="ox-own" onSubmit={(event) => { event.preventDefault(); const text = own.trim(); if (text) void start(ownName(text), text, "week"); }}>
                <label htmlFor="own-outcome">Or say your own</label>
                <input id="own-outcome" value={own} onChange={(event) => setOwn(event.target.value)} placeholder="Every invoice over $10,000 gets a second approval" />
                <button type="submit" className="ox-btn ox-btn--primary" disabled={busy !== null || !own.trim()}>Own it</button>
            </form>
            {error && <p className="ox-note" role="alert">{error}</p>}
            <p className="ox-vs ox-list__foot">Owning one starts a new Lemma here, writes its plan, and opens the chat where it sets itself up, so you can watch.</p>
        </div>
    );
}
