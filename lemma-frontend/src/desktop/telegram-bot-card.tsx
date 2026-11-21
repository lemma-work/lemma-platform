"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalIcon } from "@/ui/icons";
import { lemma } from "@/session/client";
import { isCompleteMobileNumber, normalizeMobileNumber, storedMobileNumber } from "@/session/mobile-number";
import { openExternal } from "./open-external";
import { thisMac } from "./this-mac";
import { telegramBotCard } from "./telegram-bot";

/** Under a saved Telegram token: the bot by name, your number, and the chat.
 *  See `telegram-bot.ts` for why that is all it takes. A block on the list's
 *  hairline, outside the token's form, so it shows without opening it. */
export function TelegramBotCard({ saved, unsaved }: { saved: boolean; unsaved: boolean }) {
    const queryClient = useQueryClient();
    const probe = useQuery({
        queryKey: ["telegram-bot-name"],
        queryFn: () => thisMac.testSetup({ service: "telegram" }),
        enabled: saved && !unsaved,
        staleTime: 5 * 60_000,
        retry: 1,
    });
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
        enabled: saved && !unsaved,
        staleTime: 5 * 60_000,
    });
    const stored = storedMobileNumber(me.data?.mobile_number ?? "");
    const [number, setNumber] = useState(stored);
    const [edited, setEdited] = useState(false);
    /* A late or refreshed profile fills the box only until the person types:
       overwriting a number mid-edit would lose it. */
    useEffect(() => { if (!edited) setNumber(stored); }, [stored, edited]);
    const typed = normalizeMobileNumber(number);
    const save = useMutation({
        mutationFn: () => lemma().users.upsertProfile({ mobile_number: typed }),
        onSuccess: (next) => { setEdited(false); queryClient.setQueryData(["current-user"], next); },
    });

    const card = telegramBotCard({
        saved, unsaved, detail: probe.data?.detail, pending: probe.isPending, failed: probe.isError,
    });
    if (card.kind === "hidden") return null;
    if (card.kind === "loading") return <p className="kset-note kset-block" role="status">Asking Telegram for its name…</p>;
    if (card.kind === "problem") return <p className="kset-said kset-said--bad kset-block" role="alert">{card.text}</p>;
    const changed = typed !== stored;
    return (
        <div className="kset-block">
            <p className="kstatus kset-state">{card.title}</p>
            <form className="kfield" onSubmit={(event) => { event.preventDefault(); if (changed && isCompleteMobileNumber(typed)) save.mutate(); }}>
                <label htmlFor="telegram-chat-mobile">Your mobile number</label>
                <div className="kset-acts">
                    <input id="telegram-chat-mobile" className="kinput kset-input kset-grow" value={number} inputMode="tel" autoComplete="tel" placeholder="+1 555 010 0000"
                        onChange={(event) => { save.reset(); setEdited(true); setNumber(event.target.value); }} />
                    <button type="submit" className="b b--m b--secondary" disabled={!changed || !isCompleteMobileNumber(typed) || save.isPending}>
                        {save.isPending ? "Saving…" : "Save"}
                    </button>
                </div>
                <small>It’s how Telegram tells Lemma it’s you when you share your contact.</small>
                {save.isError && <span className="kset-said kset-said--bad" role="alert">That did not save. {(save.error as Error)?.message}</span>}
                {save.isSuccess && !changed && <span className="kstatus" role="status">Saved</span>}
            </form>
            <div className="kset-acts">
                <button type="button" className="b b--s b--secondary" onClick={() => openExternal(card.url)}>
                    Open @{card.bot} in Telegram <ExternalIcon />
                </button>
            </div>
            <p className="kset-note">Send it any message, then tap <strong>Share my contact</strong>.</p>
        </div>
    );
}
