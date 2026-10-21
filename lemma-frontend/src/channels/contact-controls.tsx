"use client";

import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Member as PodMember, Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { channelApi, type ChannelChange } from "./api";
import type { ChannelDetail, ContactStatus } from "./model";
import { problemOf } from "./problem";
import { keepChannel } from "./queries";
import { STATUS_LABELS } from "./views";

/** A contact's channel as a support-inbox item: who answers them by hand,
 *  where it stands, and how long the assignee holds them before the agent
 *  answers again. Each change is one PATCH; the socket tells everybody else. */
export function ContactControls({ pod, channel, compact = false }: { pod: Pod; channel: ChannelDetail; compact?: boolean }) {
    const me = useMe();
    const ids = useId();
    const cache = useQueryClient();
    const change = useMutation({
        mutationFn: (body: ChannelChange) => channelApi(pod.id).update(channel.id, body),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    const [hours, setHours] = useState(String(channel.handBackAfterHours));
    const people = pod.members.filter((member): member is PodMember & { userId: string } => member.kind !== "teammate" && Boolean(member.userId));
    const assignee = change.isPending && change.variables?.assignee !== undefined
        ? (change.variables.assignee === "me" ? me : change.variables.assignee)
        : channel.assignee?.id ?? null;
    const status = change.isPending && change.variables?.status ? change.variables.status : channel.status ?? "open";

    return (
        <div className={"ccontact" + (compact ? " ccontact--compact" : "")}>
            <label className="ccontact__field">
                <span className={compact ? "sr-only" : "gside__label"}>Assigned to</span>
                <select
                    value={assignee ?? ""}
                    disabled={change.isPending}
                    onChange={(event) => {
                        const picked = event.target.value;
                        change.mutate({ assignee: picked === "" ? null : picked === me ? "me" : picked });
                    }}
                >
                    <option value="">{compact ? "Unassigned" : "Nobody"}</option>
                    {me && <option value={me}>You</option>}
                    {people.filter((member) => member.userId !== me).map((member) => <option key={member.userId} value={member.userId}>{member.name}</option>)}
                    {channel.assignee?.id && !people.some((member) => member.userId === channel.assignee?.id) && channel.assignee.id !== me && (
                        <option value={channel.assignee.id}>{channel.assignee.name ?? "A member"}</option>
                    )}
                </select>
            </label>
            <label className="ccontact__field">
                <span className={compact ? "sr-only" : "gside__label"}>Status</span>
                <select value={status} disabled={change.isPending} onChange={(event) => change.mutate({ status: event.target.value as ContactStatus })}>
                    {(Object.keys(STATUS_LABELS) as ContactStatus[]).map((one) => <option key={one} value={one}>{STATUS_LABELS[one]}</option>)}
                </select>
            </label>
            {!compact && (
                <form
                    className="ccontact__field ccontact__hours"
                    onSubmit={(event) => {
                        event.preventDefault();
                        const value = Number(hours);
                        if (Number.isInteger(value) && value >= 1 && value <= 720 && value !== channel.handBackAfterHours) change.mutate({ handBackAfterHours: value });
                    }}
                >
                    <label htmlFor={ids + "-hours"} className="gside__label">Hand back to the agent after</label>
                    <span className="ccontact__hours-row">
                        <input id={ids + "-hours"} type="number" min={1} max={720} step={1} inputMode="numeric" value={hours} onChange={(event) => setHours(event.target.value)} />
                        <span>hours quiet</span>
                        <button type="submit" className="ghost-pill" disabled={change.isPending || Number(hours) === channel.handBackAfterHours}>Save</button>
                    </span>
                </form>
            )}
            {change.isError && (
                <p className="gside__problem" role="alert">
                    {isForbidden(change.error) ? "Only an editor of " + pod.name + " can change that." : problemOf(change.error, "Couldn’t change that.")}
                </p>
            )}
        </div>
    );
}
