"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isForbidden } from "@/session/auth-state";
import { channelApi } from "./api";
import type { Channel } from "./model";
import { keepChannel, refreshLists } from "./queries";

/** Whether a contact's agent answers them: the operator's switch.
 *
 *  Off, only members answer -- however long since one last wrote. On again,
 *  the agent answers them at once, whoever had stepped in. */
export function AgentSwitch({ podId, channel }: { podId: string; channel: Channel }) {
    const cache = useQueryClient();
    const flip = useMutation({
        mutationFn: (on: boolean) => channelApi(podId).setAgentAnswers(channel.id, on),
        onSuccess: (saved) => { keepChannel(cache, podId, saved); refreshLists(cache, podId); },
    });
    const on = flip.isPending && flip.variables !== undefined ? flip.variables : channel.agentsAnswer;
    return (
        <span className="agent-switch">
            <label className="agent-switch__label" title={on ? "The agent answers them. Switch off to answer them yourself." : "Only members answer them. Switch on to let the agent answer again."}>
                <input
                    type="checkbox"
                    role="switch"
                    checked={on}
                    disabled={flip.isPending}
                    onChange={(event) => flip.mutate(event.target.checked)}
                />
                <span>Agent answers</span>
            </label>
            {flip.isError && (
                <span className="agent-switch__problem" role="alert">
                    {isForbidden(flip.error) ? "Only an editor can switch it." : "Couldn’t switch it."}
                </span>
            )}
        </span>
    );
}
