"use client";

import type { Surface } from "@/data";
import { useBotGroups } from "@/channels/queries";
import { noGroupsYet } from "@/channels/sources";
import { isForbidden } from "@/session/auth-state";
import { ChannelIcon } from "./channels";
import { PlaceLink, channelHref } from "./place-link";

/** The groups a bot is in, under that bot.
 *
 *  A group is where people outside the space can meet the bot, and each has a
 *  channel in the space. Its name opens that channel, where everything about
 *  it is -- who answers its people from outside, and the switch for it. */
export function SurfaceGroups({ podId, surface, bot, space, onOpenGroup }: {
    podId: string;
    surface: Surface;
    /** The bot's name, as the space calls it. */
    bot: string;
    /** The space's name, for a channel with no title of its own. */
    space: string;
    /** Before a channel opens — for a dialog this list sits in to close. */
    onOpenGroup?: () => void;
}) {
    const groups = useBotGroups(podId, surface);
    /* An admin is also listed the bot's private groups they are not in;
       those are joined from the bot's settings, not opened from here. */
    const list = (groups.data ?? []).filter((group) => group.joined);
    return (
        <div className="sgroups">
            <h3 className="sgroups__head">Groups</h3>
            {groups.isPending && <p className="sgroups__quiet" role="status">Loading groups…</p>}
            {groups.isError && (isForbidden(groups.error) ? (
                <p className="sgroups__quiet" role="alert">You may not see {bot}’s groups.</p>
            ) : (
                <p className="sgroups__quiet" role="alert">
                    Couldn’t load groups.{" "}
                    <button className="linkish" onClick={() => void groups.refetch()}>Try again</button>
                </p>
            ))}
            {groups.isSuccess && list.length === 0 && <p className="sgroups__quiet">{noGroupsYet(surface.platform, bot)}</p>}
            {list.length > 0 && (
                <ul className="sgroups__list">
                    {list.map((place) => {
                        const title = place.title ?? "A group in " + space;
                        return (
                            <li key={place.id} className="sgroup">
                                <ChannelIcon platform={place.platform || surface.platform} size={16} />
                                <div className="sgroup__body">
                                    <b className="sgroup__title" title={title}>
                                        <PlaceLink href={channelHref(podId, place.channelId)} className="sgroup__open" onGo={onOpenGroup}>{title}</PlaceLink>
                                    </b>
                                    {place.pending && <span className="sgroup__who">Being created…</span>}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}
