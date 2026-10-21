"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { PlaceLink, channelHref, goTo } from "@/shell/place-link";
import { channelApi, type BotGroup } from "./api";
import { problemOf } from "./problem";
import { keepChannel } from "./queries";
import { SourceSettingsView } from "./source-settings";

/** One group a bot is in, on the bot's settings page: its name opens its
 *  channel, and its settings -- who answers its people from outside -- open
 *  in place. A private group the reader is not in is listed to an admin
 *  only, with Join: they see nothing of it until they do, and the server
 *  records that they joined. */
export function BotGroupRow({ pod, group, onLeave }: { pod: Pod; group: BotGroup; onLeave?: () => void }) {
    const cache = useQueryClient();
    const [open, setOpen] = useState(false);
    const title = group.title ?? "A group in " + pod.name;
    const join = useMutation({
        mutationFn: () => channelApi(pod.id).join(group.channelId),
        onSuccess: (saved) => {
            keepChannel(cache, pod.id, saved);
            void cache.invalidateQueries({ queryKey: ["channel", pod.id, "bot-groups"] });
            onLeave?.();
            goTo(channelHref(pod.id, group.channelId));
        },
    });
    if (!group.joined) {
        return (
            <li className="sgroup-row">
                <span className="sgroup-row__title">{title}</span>
                <small>Private · you are not in it</small>
                <button type="button" className="linkish" disabled={join.isPending} onClick={() => join.mutate()}>{join.isPending ? "Joining…" : "Join"}</button>
                {join.isError && <small role="alert">{isForbidden(join.error) ? "Only an editor of " + pod.name + " can join it." : problemOf(join.error, "Couldn’t join it.")}</small>}
            </li>
        );
    }
    return (
        <li className="sgroup-row">
            <PlaceLink href={channelHref(pod.id, group.channelId)} onGo={onLeave}>{title}</PlaceLink>
            {group.pending && <small>Being created</small>}
            <button type="button" className="linkish" aria-expanded={open} onClick={() => setOpen((was) => !was)}>{open ? "Hide settings" : "Settings"}</button>
            {open && <SourceSettingsView pod={pod} channelId={group.channelId} sourceId={group.id} />}
        </li>
    );
}
