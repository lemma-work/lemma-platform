"use client";

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { useMaySeeContacts } from "@/space/contact-queries";
import type { FrameActions } from "./actions";
import { CanDo } from "./can-do";
import { useLemmaOpenness, useMeasure } from "./data";
import { useMe } from "@/session/use-me";
import { PLAN_SECTIONS, formatMeasure, looksAfter, outcomeView, reachSaid, standingOf, type Go, type Plan } from "./model";
import { Avatar, to } from "./parts";

/** How many of anything a part of About lists before it points at the page
 *  that holds the rest. */
const SHOWN = 5;

function Part({ question, children }: { question: string; children: ReactNode }) {
    return (
        <section className="ox-about__part" aria-label={question}>
            <h2 className="ox-about__q">{question}</h2>
            <div className="ox-about__a">{children}</div>
        </section>
    );
}

/** What a Lemma is, as the questions a person would ask about it: what it is
 *  for, who is in it, who can talk to it, what it does by itself, what it
 *  has been taught, what it is connected to. Each answer is short and read
 *  from what the Lemma already keeps, and ends at the page where the whole of
 *  it is kept and changed -- its plan, its people, what it runs, its
 *  settings -- so nothing is said in two places. */
export function AboutTab({ pod, orgName, plan, activity, go, actions }: {
    pod: Pod;
    orgName: string;
    plan: Plan | null | undefined;
    /** The reader's chats with it, for whether it has been asked anything. */
    activity: readonly ConversationRef[];
    go: Go;
    actions: FrameActions;
}) {
    const contacts = useMaySeeContacts(pod.id);
    const mine = looksAfter(pod, useMe());
    const { openness } = useLemmaOpenness(pod.id);
    const measure = useMeasure(pod.id, plan);
    const value = measure.data ?? null;
    const standing = standingOf(plan, value);
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const jobs = schedules.data ?? [];
    const people = pod.members.filter((member) => member.kind === "person");
    const taught = plan ? PLAN_SECTIONS.flatMap((section) => plan.sections[section]) : [];
    const reach = reachSaid(openness?.reach ?? null, orgName);
    const at = (tab: Parameters<typeof outcomeView>[1], section: string | null = null) => to(outcomeView(pod.id, tab, section), go);

    return (
        <div className="ox-about">
            <Part question="What’s it for?">
                <p className="ox-about__lead">{pod.description || "Nobody has said yet."}</p>
                {plan?.outcome ? (
                    <p className="ox-about__owns">
                        <span className="ox-vs">It owns</span>
                        <span>{plan.outcome}</span>
                        {value !== null && <span className="ox-num">{formatMeasure(value, plan.unit)}{plan.target ? ` of ${plan.target}` : ""}</span>}
                        {standing && <span className={"ox-pill" + (standing === "behind" ? " ox-pill--behind" : "")}>{standing === "behind" ? "Behind" : "On track"}</span>}
                    </p>
                ) : <p className="ox-vs">It doesn’t own a result yet. It helps when asked, and nothing is counted.</p>}
                <p className="ox-about__more"><a className="ox-link" {...at("plan")}>{plan?.outcome ? "Its plan, and how that is counted" : "Give it something to own"}</a></p>
            </Part>

            {mine && (
                <Part question="What can it do so far?">
                    <CanDo pod={pod} plan={plan} activity={activity} go={go} actions={actions} always />
                </Part>
            )}

            <Part question="Who’s in it?">
                {people.length === 0 ? <p className="ox-vs">Nobody yet.</p> : (
                    <ul className="ox-about__people">
                        {people.slice(0, SHOWN).map((member) => (
                            <li key={member.id}><Avatar name={member.name} size={24} />{member.name}</li>
                        ))}
                        {people.length > SHOWN && <li className="ox-vs">and {people.length - SHOWN} more</li>}
                    </ul>
                )}
                {reach && <p className="ox-vs">{reach}.</p>}
                <p className="ox-about__more">
                    <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.addPeople(pod.id)}>Add people</button>
                    <a className="ox-link" {...at("people")}>Everyone in it, and who can join</a>
                </p>
            </Part>

            <Part question="Who can talk to it?">
                <p>The people in it, here and in any chat app it’s connected to.</p>
                <p className="ox-about__more">
                    <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.connect(pod.id)}>Choose where to reach it</button>
                    {contacts && <a className="ox-link" {...at("contacts")}>People outside who wrote in</a>}
                </p>
            </Part>

            <Part question="What does it do by itself?">
                {schedules.isPending ? <p className="ox-loading">Reading what it runs…</p>
                    : schedules.isError ? <p className="ox-vs">Couldn’t read what it runs. <button type="button" className="ox-link" onClick={() => void schedules.refetch()}>Try again</button></p>
                    : jobs.length === 0 ? <p className="ox-vs">Nothing. It waits to be asked.</p>
                    : (
                        <ul className="ox-about__rows">
                            {jobs.slice(0, SHOWN).map((job) => (
                                <li key={job.id}><span>{job.title}</span><span className="ox-vs">{job.trigger}</span></li>
                            ))}
                            {jobs.length > SHOWN && <li className="ox-vs">and {jobs.length - SHOWN} more</li>}
                        </ul>
                    )}
                <p className="ox-about__more">
                    <a className="ox-link" {...at("automations")}>Everything it runs</a>
                    <button type="button" className="ox-link" onClick={() => actions.prompt(pod.id, "Every weekday at 9, ")}>Ask it to do something regularly</button>
                </p>
            </Part>

            <Part question="What has it been taught?">
                {plan === undefined ? <p className="ox-loading">Reading its plan…</p>
                    : taught.length === 0 ? <p className="ox-vs">Nothing yet. Tell it how you do something and it writes that down, with your name beside it.</p>
                    : (
                        <ul className="ox-about__rows">
                            {taught.slice(0, SHOWN).map((said, index) => (
                                <li key={index}><span>{said.text}</span><span className="ox-vs">{[said.by, said.on].filter(Boolean).join(", ")}</span></li>
                            ))}
                            {taught.length > SHOWN && <li className="ox-vs">and {taught.length - SHOWN} more</li>}
                        </ul>
                    )}
                <p className="ox-about__more">
                    <a className="ox-link" {...at("plan")}>Its plan</a>
                    <a className="ox-link" {...at("settings", "skills")}>Its skills</a>
                    <a className="ox-link" {...at("settings", "memory")}>What it remembers</a>
                </p>
            </Part>

            <Part question="What’s it connected to?">
                <p>The tools it can read and work in, and whose account each one uses.</p>
                <p className="ox-about__more"><a className="ox-link" {...at("settings", "connections")}>Its connections</a></p>
            </Part>

            <Part question="Anything else?">
                <p className="ox-about__more">
                    <a className="ox-link" {...at("settings", "model")}>Which model it thinks with</a>
                    <a className="ox-link" {...to({ name: "computer", podId: pod.id, conversationId: null }, go)}>Its computer</a>
                    <button type="button" className="ox-link" onClick={() => actions.share(pod.id)}>Share or export it</button>
                    <a className="ox-link" {...at("settings")}>All its settings</a>
                </p>
            </Part>
        </div>
    );
}
