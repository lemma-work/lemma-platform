"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SurfaceContactsConfig } from "lemma-sdk";
import { source, type Pod, type Surface } from "@/data";
import { answerChoices } from "@/data/contacts";
import { contactsSupported, isOwnBot } from "@/data/surface-settings";
import { platformName } from "@/channels/sources";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";

type Answer = "off" | "known" | "anyone";

/** Where people outside the space can write to it: each of its own WhatsApp
 *  numbers, Telegram bots and email addresses, and whom each one answers --
 *  changed right here, the same setting a bot's own page holds. Only members
 *  are answered until somebody chooses otherwise. Lemma's shared number and
 *  bot are every space's at once, so they never answer contacts. */
export function ContactBots({ pod, onConnect }: { pod: Pod; onConnect?: () => void }) {
    const surfaces = useSurfaces(pod.id);
    const bots = (surfaces.data ?? []).filter((surface) => contactsSupported(surface.platform.toUpperCase()));
    return (
        <section className="ccard ccard--wide" aria-labelledby="contacts-bots">
            <div className="ccard__head">
                <h2 id="contacts-bots">Channels</h2>
                {onConnect && <button type="button" className="ghost-pill" onClick={onConnect}>Connect</button>}
            </div>
            <p className="ccard__sub">Whom each number, bot and address answers.</p>
            {surfaces.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : bots.length === 0 ? (
                <p className="ccard__sub">No WhatsApp number, Telegram bot or email address yet.</p>
            ) : (
                <>
                    <ul className="crows cbots">
                        {bots.map((surface) => <BotRow key={surface.id} pod={pod} surface={surface} />)}
                    </ul>
                    <p className="ccard__note">Lemma’s shared WhatsApp number and Telegram bot answer members only.</p>
                </>
            )}
        </section>
    );
}

function BotRow({ pod, surface }: { pod: Pod; surface: Surface }) {
    const cache = useQueryClient();
    const detail = useQuery({ queryKey: ["surface-detail", pod.id, surface.name], queryFn: () => source.getSurface(pod.id, surface.name) });
    const save = useMutation({
        mutationFn: (answer: Answer) => source.updateSurface(pod.id, surface.name, { config: { contacts: { answer: answer as SurfaceContactsConfig["answer"] } } }),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["surface-detail", pod.id, surface.name] });
            void cache.invalidateQueries({ queryKey: ["surfaces", 2, pod.id] });
        },
    });
    const saved = detail.data?.config.contacts?.answer;
    const current: Answer = saved === "known" || saved === "anyone" ? saved : "off";
    const value = save.isPending && save.variables ? save.variables : current;
    const own = detail.data ? isOwnBot(detail.data) : true;
    const choices = answerChoices(pod.name);
    const platform = surface.platform.toUpperCase();
    /* Lemma's shared number and bot are every space's at once: they never
       answer contacts, so they are not a choice here. Which one a bot is
       shows only in its details, so a row waits for them rather than
       showing and then going. */
    if (detail.isPending || (detail.data && !own)) return null;
    return (
        <li className="crow cbot">
            <span className="cbot__mark" aria-hidden="true"><ChannelIcon platform={platform} size={22} /></span>
            <span className="cbot__text">
                <span className="crow__name">{surface.handle || platformName(platform)}</span>
                <span className="crow__sub" title={choices.find((choice) => choice.value === value)?.note}>{platformName(platform)}</span>
            </span>
            {own && (
                <label className="cbot__choose">
                    <span className="sr-only">Whom {surface.handle || platformName(platform)} answers</span>
                    <select value={value} disabled={detail.isPending || save.isPending} onChange={(event) => save.mutate(event.target.value as Answer)}>
                        {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
                    </select>
                </label>
            )}
            {save.isError && <span className="cbot__problem" role="alert">Couldn’t change it. Only an editor of {pod.name} can.</span>}
        </li>
    );
}
