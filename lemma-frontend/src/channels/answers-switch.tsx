"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isForbidden } from "@/session/auth-state";
import { channelApi } from "./api";
import type { Channel } from "./model";
import { problemOf } from "./problem";
import { keepChannel } from "./queries";
import { ANSWERS_SHARED_LABEL, answersSharedNote } from "./settings-model";

/** Whether a group shows its agents' answers to everybody in it: on, an
 *  answer an agent gives someone here with their own access is read by
 *  everyone; off, it is kept to whoever asked until they share it. Offered
 *  to whoever may change the group; everybody else is told which it is.
 *  A change applies to answers from then on. */
export function AnswersSwitch({ podId, channel, manage }: { podId: string; channel: Channel; manage: boolean }) {
    const cache = useQueryClient();
    const flip = useMutation({
        mutationFn: (on: boolean) => channelApi(podId).update(channel.id, { answersShared: on }),
        onSuccess: (saved) => keepChannel(cache, podId, saved),
    });
    const on = flip.isPending && flip.variables !== undefined ? flip.variables : channel.answersShared;
    if (!manage) return <p className="gside__fine">{answersSharedNote(on)}</p>;
    return (
        <div className="cside__answers">
            <label className="gswitch">
                <input
                    type="checkbox"
                    role="switch"
                    checked={on}
                    disabled={flip.isPending}
                    onChange={(event) => flip.mutate(event.target.checked)}
                />
                <span className="gswitch__text">
                    <span>{ANSWERS_SHARED_LABEL}</span>
                    <small>{answersSharedNote(on)}</small>
                </span>
            </label>
            {flip.isError && (
                <p className="gside__problem" role="alert">
                    {isForbidden(flip.error) ? "Only who made this chat, or an editor, can change this." : problemOf(flip.error, "Couldn’t change that.")}
                </p>
            )}
        </div>
    );
}
