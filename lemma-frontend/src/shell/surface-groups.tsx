"use client";

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { GroupUpdateRequest } from "lemma-sdk";
import { source, type Member, type Surface, type SurfaceGroup } from "@/data";
import { groupLine, groupTitle, linkShown, noGroupsYet, offersTakeOver, whoMayChange, withGroup } from "@/data/surface-groups";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { CopyButton } from "@/thread/copy-button";
import { ChannelIcon } from "./channels";
import { PlaceLink, groupHref } from "./place-link";
import { useFeature } from "@/site/analytics/flags";

/** The groups a channel's bot is in, under that channel.
 *
 *  A group is where people outside the space can meet the bot. Each row says
 *  who answers them there — one line — and, to a reader the server lets
 *  change it, the two things to do about it: switch answering them on or
 *  off, and take the group over from whoever looks after it now. Anyone else
 *  is told who can. A group the bot opened (WhatsApp) also carries the link
 *  people join by, to copy and pass on. Its name opens the group's own page,
 *  where everything else about it is. */
export function SurfaceGroups(props: Parameters<typeof GroupsOfSurface>[0]) {
    return useFeature("groups") ? <GroupsOfSurface {...props} /> : null;
}

function GroupsOfSurface({ podId, surface, bot, space, members, canChange, onOpenGroup }: {
    podId: string;
    surface: Surface;
    /** The bot's name, as the space calls it. */
    bot: string;
    /** The space's name — "people outside {space}". */
    space: string;
    /** For an owner the server names by id alone. */
    members?: Member[];
    /** False when the reader may not configure this bot, so the controls are
     *  shut before a click rather than refused after one. Unknown is not no. */
    canChange?: boolean;
    /** Before a group's page opens — for a dialog this list sits in to close. */
    onOpenGroup?: () => void;
}) {
    const me = useMe();
    const ids = useId();
    const cache = useQueryClient();
    const key = ["surface-groups", podId, surface.name];
    const groups = useQuery({
        queryKey: key,
        queryFn: () => source.listSurfaceGroups(podId, surface.name),
        staleTime: 30_000,
    });
    const refused = "Only someone who can change " + bot + " can change this.";
    const [problem, setProblem] = useState<{ id: string; text: string } | null>(null);
    const change = useMutation({
        mutationFn: ({ group, patch }: { group: SurfaceGroup; patch: GroupUpdateRequest }) =>
            source.updateSurfaceGroup(podId, surface.name, group.id, patch),
        onMutate: () => setProblem(null),
        onSuccess: (saved) => {
            cache.setQueryData<SurfaceGroup[]>(key, (was) => (was ? withGroup(was, saved) : was));
            /* The same group, on the space's Groups page and its own. */
            void cache.invalidateQueries({ queryKey: ["groups", podId] });
            void cache.invalidateQueries({ queryKey: ["group", podId, saved.id] });
        },
        onError: (error, { group }) => {
            setProblem({
                id: group.id,
                text: isForbidden(error) ? whoMayChange(group, space, bot, me, members) : "Couldn’t change that. Try again.",
            });
            /* Refused: it changed hands since this list was read. */
            if (isForbidden(error)) void cache.invalidateQueries({ queryKey: key });
        },
    });
    const shut = canChange === false;
    const list = groups.data ?? [];

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
            {groups.isSuccess && list.length === 0 && (
                <p className="sgroups__quiet">{noGroupsYet(surface.platform, bot)}</p>
            )}
            {list.length > 0 && (
                <ul className="sgroups__list">
                    {list.map((group) => {
                        /* The switch shows what was asked while it is being
                           asked, so a slow save does not look like a click
                           that did nothing. */
                        const asked = change.isPending && change.variables?.group.id === group.id ? change.variables.patch : null;
                        const on = asked?.answers_outsiders ?? group.answersOutsiders;
                        const title = groupTitle(group);
                        /* Every row's controls say the same words, so each
                           is described by the group it belongs to. */
                        const named = ids + "-" + group.id;
                        /* Not the reader's to change: no control, and who
                           can — unless the whole bot is shut to them, which
                           is said once, below. */
                        const theirs = group.canManage;
                        return (
                            <li key={group.id} className="sgroup">
                                <ChannelIcon platform={group.platform || surface.platform} size={16} />
                                <div className="sgroup__body">
                                    <b className="sgroup__title" id={named} title={title}>
                                        <PlaceLink href={groupHref(podId, group.id)} className="sgroup__open" onGo={onOpenGroup}>{title}</PlaceLink>
                                    </b>
                                    <span className="sgroup__who">{groupLine(group, space, me, members)}</span>
                                    {group.inviteLink && (
                                        <span className="sgroup__link">
                                            <span className="sgroup__link-text" title={group.inviteLink}>{linkShown(group.inviteLink)}</span>
                                            <CopyButton text={group.inviteLink} label={"Copy the invite link to " + title} />
                                        </span>
                                    )}
                                    {theirs ? (
                                        <div className="sgroup__acts">
                                            <label className="sgroup__switch">
                                                <input
                                                    type="checkbox"
                                                    role="switch"
                                                    aria-describedby={named}
                                                    checked={on}
                                                    disabled={shut || change.isPending}
                                                    onChange={(event) => change.mutate({ group, patch: { answers_outsiders: event.target.checked } })}
                                                />
                                                <span>Answer people outside {space}</span>
                                            </label>
                                            {offersTakeOver(group, me) && (
                                                <button
                                                    type="button"
                                                    className="sgroup__take"
                                                    aria-describedby={named}
                                                    disabled={shut || change.isPending}
                                                    onClick={() => change.mutate({ group, patch: { take_over: true } })}
                                                >
                                                    Take this over
                                                </button>
                                            )}
                                        </div>
                                    ) : !shut && !group.pending && (
                                        <span className="sgroup__who">{whoMayChange(group, space, bot, me, members)}</span>
                                    )}
                                    {problem?.id === group.id && <span className="sgroup__problem" role="alert">{problem.text}</span>}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
            {shut && list.length > 0 && <p className="sgroups__quiet">{refused}</p>}
        </div>
    );
}
