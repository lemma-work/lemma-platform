"use client";

import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { Modal } from "@/shell/modal";
import { SurfaceManage } from "@/shell/surface-manage";
import { useSurfaces } from "@/shell/surfaces";
import { CopyButton } from "@/thread/copy-button";
import { channelApi } from "./api";
import { sourceKey, useSourceSettings } from "./queries";
import { SourcePublic } from "./source-public";
import { TableGrants } from "./table-grants";
import {
    answering,
    howToAsk,
    howToRemove,
    linkShown,
    offersTakeOn,
    onlyWho,
    refusedChange,
    sayAnswering,
    sayAnsweringFully,
    takeOn,
    toldWhenChanged,
    whomItAnswers,
    type SourceChange,
    type SourceSettings,
} from "./sources";

/** One group source of a channel, from the bot's side: who answers its people
 *  from outside the space, from what, and how its people join -- with the
 *  switch and "Take it on" for a reader the server lets change it. Anyone
 *  else is told who can. Where nobody outside is answered, the first reason
 *  is said: the bot's own switch, nobody to answer for them, or the group's
 *  switch, which is on screen already for whoever may flip it. */
export function SourceSettingsView({ pod, channelId, sourceId }: { pod: Pod; channelId: string; sourceId: string }) {
    const settings = useSourceSettings(pod.id, channelId, sourceId);
    if (settings.isPending) return <p className="gside__text" role="status">Reading its settings…</p>;
    if (!settings.data) {
        return (
            <p className="gside__text" role="alert">
                {isMissing(settings.error) ? "This group has no settings to show." : isForbidden(settings.error) ? "You may not see this group’s settings." : "Couldn’t read its settings."}
                {!isMissing(settings.error) && !isForbidden(settings.error) && <> <button type="button" className="gsheet__inline" onClick={() => void settings.refetch()}>Try again</button></>}
            </p>
        );
    }
    return <Settings pod={pod} source={settings.data} />;
}

function Settings({ pod, source }: { pod: Pod; source: SourceSettings }) {
    const me = useMe();
    const ids = useId();
    const cache = useQueryClient();
    const surfaces = useSurfaces(pod.id);
    const surface = (surfaces.data ?? []).find((candidate) => candidate.name === source.surfaceName) ?? null;
    /* Who the bot is here: the space itself, or an agent with a bot of its own. */
    const bot = surface && !surface.mine ? surface.agentName || pod.name : pod.name;
    const [opened, setOpened] = useState(false);
    const change = useMutation({
        mutationFn: (body: SourceChange) => channelApi(pod.id).updateSource(source.channelId, source.id, body),
        onSuccess: (saved) => cache.setQueryData(sourceKey(pod.id, source.id), saved),
        /* Refused: it changed hands since it was read. Read it again, so the
           panel stops offering what is no longer the reader's. */
        onError: (error) => { if (isForbidden(error)) void cache.invalidateQueries({ queryKey: sourceKey(pod.id, source.id) }); },
    });
    const asked = change.isPending ? change.variables : null;
    const on = asked?.answers_outsiders ?? source.answersOutsiders;
    const state = answering(source, me);
    const may = source.canManage;
    const taking = change.isPending && change.variables?.owner === "me";
    const told = may ? toldWhenChanged(state) : null;
    const who = may ? null : onlyWho(state, pod.name, bot);
    const remove = howToRemove(source.platform, source.platform === "SLACK" ? surface?.handle || bot : bot);
    const saved = () => {
        for (const key of [["surfaces", 2, pod.id], ["surface-detail", pod.id], ["surface-setup", pod.id], ["surface-channels", pod.id], ["my-surfaces"]]) {
            void cache.invalidateQueries({ queryKey: key });
        }
        void cache.invalidateQueries({ queryKey: sourceKey(pod.id, source.id) });
        setOpened(false);
    };

    return (
        <div className="csource">
            {source.pending && <p className="gside__text" role="status">WhatsApp is still making this group. Its link appears here in a few seconds.</p>}
            {source.inviteLink && (
                <div className="csource__link">
                    <span className="gside__label">People join by</span>
                    <span className="sgroup__link">
                        <span className="sgroup__link-text" title={source.inviteLink}>{linkShown(source.inviteLink)}</span>
                        <CopyButton text={source.inviteLink} label="Copy the invite link" />
                    </span>
                </div>
            )}
            <p className="gside__label" id={ids + "-head"}>People outside {pod.name}</p>
            {state.kind === "invited" ? (
                <p className="gside__text">People here who are not in {pod.name} get a private note inviting them in. Nobody is answered from what is Public in this channel.</p>
            ) : (
                <>
                    {may && (
                        <label className="gswitch">
                            <input
                                type="checkbox"
                                role="switch"
                                checked={on}
                                disabled={change.isPending || source.pending}
                                aria-describedby={ids + "-head"}
                                onChange={(event) => change.mutate({ answers_outsiders: event.target.checked })}
                            />
                            <span className="gswitch__text">
                                <span>Answer them</span>
                                <small>From what is Public in {pod.name}, listed below.</small>
                            </span>
                        </label>
                    )}
                    {state.kind === "bot-off" ? (
                        <p className="gside__text">
                            {sayAnsweringFully(state, pod.name)}.
                            {may && surface && <> <button type="button" className="gsheet__inline" onClick={() => setOpened(true)}>Open its settings</button></>}
                        </p>
                    ) : !(may && (state.kind === "off" || state.kind === "pending")) && (
                        <div className="gowner">
                            <span>{sayAnswering(state, pod.name)}</span>
                            {offersTakeOn(state, may) && (
                                <button type="button" className="gsheet__inline" disabled={change.isPending} onClick={() => change.mutate(takeOn(source))}>
                                    {taking ? "Taking it on…" : "Take it on"}
                                </button>
                            )}
                        </div>
                    )}
                    {(told ?? who) && <p className="gside__fine">{told ?? who}</p>}
                    {source.publicReach && <SourcePublic reach={source.publicReach} space={pod.name} />}
                    <TableGrants pod={pod} channelId={source.channelId} may={may} />
                    {change.isError && (
                        <p className="gside__problem" role="alert">{isForbidden(change.error) ? refusedChange(pod.name) : "Couldn’t change that. Try again."}</p>
                    )}
                </>
            )}
            <p className="gside__fine">{howToAsk(source.platform, bot)} {whomItAnswers(source, pod.name)}</p>
            {remove && <p className="gside__fine">{remove}</p>}
            {opened && surface && (
                <Modal title={"Channels for " + bot} onClose={() => setOpened(false)}>
                    <SurfaceManage pod={pod} surface={surface} onBack={() => setOpened(false)} onSaved={saved} onLeave={() => setOpened(false)} />
                </Modal>
            )}
        </div>
    );
}
