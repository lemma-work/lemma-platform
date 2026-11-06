"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { InteractionCard, type Resolve } from "@/thread/interaction-card";
import { buildTurns, openInteraction } from "@/thread/turns";
import { CONVERSATION_ASKS } from "@/thread/waiting-on-you";
import { WaitForm } from "@/workflow/form";
import { ageOf, answersHere, verbOf, viewOfItem, type Go, type QueueItem } from "./model";
import { to } from "./parts";

const DAY_MS = 86_400_000;

/** One thing waiting on you, answered where it is listed.
 *
 *  It used to be a link and nothing else: every question, approval and form
 *  opened its chat or its run, and was answered there. That is a page and a
 *  wait for a yes that is one click. Now the row opens in place onto the
 *  same card the chat would dock, or the same form the run would show, and
 *  the title still opens where it came from, for when the answer needs the
 *  whole conversation. A contact waiting on a person wants a written reply,
 *  so that one still opens their chat. */
export function NeedRow({ item, pods, now, go, showLemma = true }: {
    item: QueueItem;
    pods: readonly Pod[];
    now: number;
    go: Go;
    /** Off on a Lemma's own page, where every row is that Lemma's. */
    showLemma?: boolean;
}) {
    const [open, setOpen] = useState(false);
    const here = answersHere(item);
    const pod = pods.find((one) => one.id === item.podId) ?? null;
    const opens = to(viewOfItem(item), go);
    const said = [showLemma ? item.podName : null, item.detail].filter(Boolean).join(" · ");
    return (
        <div className="ox-need" data-open={open || undefined}>
            <div className="ox-item">
                <a className="ox-item__t" {...opens}>
                    <span className="ox-item__w">{item.title}</span>
                    {said && <span className="ox-item__o">{said}</span>}
                </a>
                <span className={"ox-item__age" + (now - item.sinceMs > DAY_MS ? " ox-item__age--old" : "")}>{ageOf(item.sinceMs, now)}</span>
                {here && pod ? (
                    <button type="button" className="ox-btn ox-btn--small" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
                        {open ? "Close" : verbOf(item)}
                    </button>
                ) : (
                    <a className="ox-btn ox-btn--small" {...opens}>{verbOf(item)}</a>
                )}
            </div>
            {open && pod && (
                <div className="ox-need__panel">
                    {here === "form" && item.wait && item.runId ? (
                        <FormHere pod={pod} runId={item.runId} wait={item.wait} />
                    ) : (
                        <AskHere pod={pod} conversationId={item.conversationId as string} />
                    )}
                </div>
            )}
        </div>
    );
}

/** The card a chat would dock for its open question or approval, read from
 *  the chat's own messages: the pause is the tool call, so the transcript
 *  already holds everything the card draws. Answered with the same POST the
 *  chat makes. A queued answer belongs to a worker from then on, so nothing
 *  here has to keep the run going. */
function AskHere({ pod, conversationId }: { pod: Pod; conversationId: string }) {
    const cache = useQueryClient();
    const chat = useQuery({
        queryKey: ["outcome-ask", pod.id, conversationId],
        queryFn: () => source.getConversation(pod.id, pod.teammate, conversationId),
        staleTime: 0,
    });
    const interaction = useMemo(() => openInteraction(buildTurns(chat.data?.messages ?? [])), [chat.data]);
    const resolve: Resolve = async (id, decision, response) => {
        if (source.label === "live") {
            await lemma(pod.id).conversations.approvals.resolve(conversationId, id, { decision, response: response ?? {} }, { pod_id: pod.id });
        }
        void cache.invalidateQueries({ queryKey: [CONVERSATION_ASKS] });
    };
    if (chat.isPending) return <p className="ox-vs">Reading what {pod.name} asked…</p>;
    if (chat.isError) return <p className="ox-vs">What {pod.name} asked couldn’t be read just now. Its chat has it.</p>;
    if (!interaction) return <p className="ox-vs">Already answered. It leaves this list at the next check.</p>;
    return <InteractionCard interaction={interaction} teammate={pod.name} onResolve={resolve} />;
}

/** The form a workflow run is stopped on, filled in here. */
function FormHere({ pod, runId, wait }: { pod: Pod; runId: string; wait: NonNullable<QueueItem["wait"]> }) {
    const cache = useQueryClient();
    return (
        <div className="wf-wait__form">
            <WaitForm podId={pod.id} runId={runId} wait={wait} onDone={() => void cache.invalidateQueries({ queryKey: ["workflow-waiting"] })} />
        </div>
    );
}
