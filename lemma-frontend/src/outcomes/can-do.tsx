"use client";

import { useQuery } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { useMe } from "@/session/use-me";
import type { FrameActions } from "./actions";
import { useLemmaPages, useLemmaTables } from "./lemma-head";
import { canDo, isChat, looksAfter, outcomeView, type Go, type Plan } from "./model";
import { Icon, to } from "./parts";

/** What a Lemma can do so far, as four things a person can see for
 *  themselves -- it holds something, it has been asked something, it runs
 *  something by itself, it owns a result -- and the one step that would add
 *  the next. This is what stands where a set-up flow would: a Lemma is never
 *  "being set up", it is as far along as this says, and the next step is
 *  always one thing.
 *
 *  Shown to whoever looks after the Lemma (`looksAfter`): the steps are
 *  theirs to take. On its home it shows until all four are true, then steps
 *  aside; About keeps it (`always`). It draws nothing until what it reads has
 *  answered, so a step is never shown undone and then ticked a moment later. */
export function CanDo({ pod, plan, activity, go, actions, always = false }: {
    pod: Pod;
    plan: Plan | null | undefined;
    /** The reader's chats with it, for whether it has been asked anything. */
    activity: readonly ConversationRef[];
    go: Go;
    actions: FrameActions;
    always?: boolean;
}) {
    const tables = useLemmaTables(pod.id);
    const pages = useLemmaPages(pod.id);
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const mine = looksAfter(pod, useMe());
    if (!mine) return null;
    if (tables.isPending || pages.isPending || schedules.isPending || plan === undefined) return null;
    const steps = canDo({
        stuff: (tables.data?.length ?? 0) > 0 || (pages.data?.length ?? 0) > 0,
        chats: activity.some(isChat),
        runs: (schedules.data?.length ?? 0) > 0,
        owns: Boolean(plan?.outcome),
    });
    const next = steps.find((step) => !step.done) ?? null;
    if (!next && !always) return null;

    return (
        <section className="ox-cando" aria-label="What it can do so far">
            {!always && <div className="ox-sec"><h2>What it can do so far</h2></div>}
            <ul className="ox-cando__steps">
                {steps.map((step) => (
                    <li key={step.key} className={step.done ? "ox-cando__step ox-cando__step--done" : "ox-cando__step"}>
                        <span className="ox-cando__mark" aria-hidden="true">{step.done && <Icon name="check" size={12} />}</span>
                        {step.label}
                        <span className="ox-sr">{step.done ? ", yes" : ", not yet"}</span>
                    </li>
                ))}
            </ul>
            {next && (
                <p className="ox-cando__next">
                    <span>Next: {next.next}</span>
                    {next.key === "knows" && <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.prompt(pod.id, "Bring in ")}>Bring something in</button>}
                    {next.key === "works" && <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.prompt(pod.id, "Every weekday at 9, ")}>Set one up</button>}
                    {next.key === "owns" && <a className="ox-btn ox-btn--small" {...to(outcomeView(pod.id, "plan"), go)}>Give it one</a>}
                </p>
            )}
        </section>
    );
}
