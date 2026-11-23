"use client";

/* Linking a Telegram account from the profile, where no group says which bot
   to open the code with. The member picks one of their spaces' bots and the
   rest is the same proof a group's "This is me" starts: a code made in their
   own session, opened in Telegram from their account, then confirmed back
   here. Nothing on this screen names anybody else's account, so nothing on it
   can link one. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { isOwnBot } from "@/data/surface-settings";
import { LinkTelegramSteps } from "@/channels/identity-link";
import { problemOf } from "@/channels/problem";
import { ChannelIcon } from "@/shell/channels";
import { Modal } from "@/shell/modal";
import { lemma } from "./client";
import { linkedTelegramAccounts, unlinkTelegramAccount } from "./telegram-accounts";
import {
    botChoices, linkedWhen, readMine, spacesSaid,
    type BotChoice, type MyTelegramSurfaces, type TelegramBot,
} from "./telegram-bots";

/** The sample's member is in two spaces on Lemma's bot, and one of them has a
 *  bot of its own too. */
const SAMPLE_BOTS: TelegramBot[] = [
    { surfaceId: "s-telegram-shared", podId: "marketing", podName: "Marketing", handle: "@lemma_sample_bot", shared: true },
    { surfaceId: "s-telegram-personal", podId: "personal", podName: "Personal", handle: "@lemma_sample_bot", shared: true },
    { surfaceId: "s-telegram", podId: "marketing", podName: "Marketing", handle: "@marketing_acme_bot", shared: false },
];

/** The member's Telegram surfaces, across every space they are in: one read,
 *  enough to say whether there is anything to link through. */
async function myTelegramSurfaces(): Promise<MyTelegramSurfaces> {
    if (source.label === "sample") {
        return { surfaceIds: SAMPLE_BOTS.map((bot) => bot.surfaceId), podIds: [...new Set(SAMPLE_BOTS.map((bot) => bot.podId))], preferred: null };
    }
    return readMine(await lemma().userSurfaces.list());
}

/** Which bot each of those is: each such space's own list says, and names it. */
async function myTelegramBots(mine: MyTelegramSurfaces): Promise<BotChoice[]> {
    if (source.label === "sample") return botChoices(SAMPLE_BOTS);
    const wanted = new Set(mine.surfaceIds);
    /* A space that will not say is left out rather than failing the rest. */
    const read = await Promise.allSettled(mine.podIds.map(async (podId) => {
        const [pod, listed] = await Promise.all([
            source.getPod(podId),
            lemma(podId).podSurfaces.list(podId, { platform: "TELEGRAM", limit: 30 }),
        ]);
        return listed.items
            .filter((surface) => wanted.has(surface.id) && (surface.status ?? "ACTIVE") === "ACTIVE")
            .map((surface): TelegramBot => ({
                surfaceId: surface.id,
                podId,
                podName: pod?.name ?? "A space",
                handle: surface.reach?.handle ?? surface.surface_identity_username ?? "",
                shared: !isOwnBot(surface),
            }));
    }));
    const failed = read.find((one) => one.status === "rejected");
    if (failed && read.every((one) => one.status === "rejected")) throw failed.reason;
    return botChoices(read.flatMap((one) => (one.status === "fulfilled" ? one.value : [])), mine.preferred);
}

function title(choice: BotChoice): string {
    return choice.shared ? "Lemma on Telegram" : choice.handle || "Its own Telegram account";
}

function detail(choice: BotChoice): string {
    const spaces = spacesSaid(choice.spaces);
    if (choice.shared) return (choice.handle ? choice.handle + " · " : "") + spaces;
    return choice.spaces.length > 1 ? "Its own, in " + spaces : spaces + "’s own";
}

/** Account's Telegram rows: each account already linked, to unlink, and the
 *  button that links one. The button only for somebody one of whose Lemmas
 *  is on Telegram -- without one there is nothing to open the code with --
 *  and the rows only when there is something to show. Drawn as rows in the
 *  Account list: what it is, what it says, what you can do. */
export function LinkTelegramEntry() {
    const [open, setOpen] = useState(false);
    const cache = useQueryClient();
    const mine = useQuery({ queryKey: ["telegram-surfaces", "mine"], queryFn: myTelegramSurfaces, staleTime: 60_000 });
    const accounts = useQuery({ queryKey: ["telegram-accounts", "mine"], queryFn: linkedTelegramAccounts });
    const reread = () => cache.invalidateQueries({ queryKey: ["telegram-accounts"] });
    /* Re-read on a failure too: one already unlinked in another tab is a 404,
       and its row should go rather than stay to be tried again. */
    const unlink = useMutation({ mutationFn: unlinkTelegramAccount, onSettled: reread });
    const linked = accounts.data ?? [];
    const through = mine.data?.surfaceIds.length ? mine.data : null;
    /* Not yet read is not none: the row waits rather than offering a first
       link to somebody who may have several. */
    if (accounts.isPending) return null;
    const unread = accounts.isError && !accounts.data;
    if (!through && !linked.length && !unread) return null;
    const linkButton = through && (
        <button type="button" className="b b--s b--secondary" onClick={() => setOpen(true)}>
            {linked.length ? "Link another" : "Link Telegram"}
        </button>
    );
    return (
        <>
            {unread ? (
                <div className="krow krow--kv">
                    <span className="krow__k">Telegram</span>
                    <span className="krow__v kset-wrap">
                        <b>Not read</b>
                        <span className="kset-said--bad" role="alert">{problemOf(accounts.error, "Couldn’t read your linked accounts.")}</span>
                    </span>
                    <span className="krow__end">
                        <button type="button" className="b b--s b--secondary" onClick={() => void accounts.refetch()}>Try again</button>
                    </span>
                </div>
            ) : linked.length ? linked.map((account, index) => {
                const name = account.name ?? "A Telegram account";
                const when = linkedWhen(account.provedAt);
                const failed = unlink.isError && unlink.variables === account.id;
                return (
                    <div className="krow krow--kv" key={account.id}>
                        <span className="krow__k">{index === 0 ? "Telegram" : ""}</span>
                        <span className="krow__v kset-wrap">
                            <b>{name}</b>
                            {failed
                                ? <span className="kset-said--bad" role="alert">{problemOf(unlink.error, "Couldn’t unlink it.")}</span>
                                : when && <span>Linked {when}</span>}
                        </span>
                        <span className="krow__end">
                            <button
                                type="button"
                                className="b b--s b--quiet"
                                disabled={unlink.isPending}
                                aria-label={"Unlink " + name}
                                onClick={() => unlink.mutate(account.id)}
                            >Unlink</button>
                            {index === linked.length - 1 && linkButton}
                        </span>
                    </div>
                );
            }) : (
                <div className="krow krow--kv">
                    <span className="krow__k">Telegram</span>
                    <span className="krow__v kset-wrap">
                        <b>Not linked</b>
                        <span>Link it so your Lemmas know it’s you there, in groups as well as in private chats.</span>
                    </span>
                    <span className="krow__end">{linkButton}</span>
                </div>
            )}
            {open && through && <LinkTelegramFromProfile mine={through} onClose={() => { setOpen(false); void reread(); }} />}
        </>
    );
}

function LinkTelegramFromProfile({ mine, onClose }: { mine: MyTelegramSurfaces; onClose: () => void }) {
    const bots = useQuery({ queryKey: ["telegram-bots", mine.surfaceIds], queryFn: () => myTelegramBots(mine), staleTime: 60_000 });
    const choices = bots.data ?? [];
    const [picked, setPicked] = useState<string | null>(null);
    const [going, setGoing] = useState<BotChoice | null>(null);
    /* With one bot there is nothing to choose: straight to the link. */
    const through = going ?? (choices.length === 1 ? choices[0] : null);
    const selected = choices.find((choice) => choice.surfaceId === picked) ?? choices[0] ?? null;

    return (
        <Modal mark={<ChannelIcon platform="TELEGRAM" size={32} />} title="Link your Telegram" onClose={onClose}>
            {through ? (
                <LinkTelegramSteps
                    key={through.surfaceId}
                    pod={{ id: through.podId, name: through.podName }}
                    surfaceId={through.surfaceId}
                    everywhere
                    onClose={onClose}
                />
            ) : (
                <div className="gsheet">
                    {bots.isPending ? (
                        <p className="gsheet__lead" role="status">Finding your Lemmas on Telegram…</p>
                    ) : bots.isError ? (
                        <>
                            <p className="gsheet__problem" role="alert">{problemOf(bots.error, "Couldn’t find your Lemmas on Telegram.")}</p>
                            <div className="gsheet__foot">
                                <button type="button" className="ghost-pill" onClick={() => void bots.refetch()}>Try again</button>
                            </div>
                        </>
                    ) : !selected ? (
                        <>
                            <p className="gsheet__lead">None of your Lemmas is on Telegram yet.</p>
                            <p className="gsheet__fine">Once one is, link your account through it here.</p>
                            <div className="gsheet__foot">
                                <button type="button" className="ghost-pill" onClick={onClose}>Done</button>
                            </div>
                        </>
                    ) : (
                        <>
                            <p className="gsheet__lead">Which should Telegram open?</p>
                            <p className="gsheet__fine">Whichever you pick, every Lemma you’re in knows the account as you.</p>
                            <div className="gsheet__block" role="radiogroup" aria-label="Telegram account">
                                {choices.map((choice) => (
                                    <label key={choice.surfaceId} className="gsheet__check">
                                        <input
                                            type="radio"
                                            name="telegram-bot"
                                            value={choice.surfaceId}
                                            checked={choice === selected}
                                            onChange={() => setPicked(choice.surfaceId)}
                                        />
                                        <span>
                                            <span className="gsheet__check-title">{title(choice)}</span>
                                            <span className="gsheet__fine">{detail(choice)}</span>
                                        </span>
                                    </label>
                                ))}
                            </div>
                            <div className="gsheet__foot">
                                <button type="button" className="pill-button" onClick={() => setGoing(selected)}>Continue</button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </Modal>
    );
}
