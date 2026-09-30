"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircleIcon, ExternalIcon } from "@/ui/icons";
import { lemma } from "@/session/client";
import { isCompleteMobileNumber, normalizeMobileNumber, storedMobileNumber } from "@/session/mobile-number";
import { openExternal } from "./open-external";
import { thisMac } from "./this-mac";
import { telegramBotCard } from "./telegram-bot";

/** Under a saved Telegram token: the bot by name, your number, and the chat.
 *  See `telegram-bot.ts` for why that is all it takes. */
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
    useEffect(() => setNumber(stored), [stored]);
    const typed = normalizeMobileNumber(number);
    const save = useMutation({
        mutationFn: () => lemma().users.upsertProfile({ mobile_number: typed }),
        onSuccess: (next) => queryClient.setQueryData(["current-user"], next),
    });

    const card = telegramBotCard({
        saved, unsaved, detail: probe.data?.detail, pending: probe.isPending, failed: probe.isError,
    });
    if (card.kind === "hidden") return null;
    if (card.kind === "loading") return <p className="thismac-said telegram-chat" role="status">Asking Telegram for the bot’s name…</p>;
    if (card.kind === "problem") return <p className="thismac-said thismac-said--bad telegram-chat" role="alert">{card.text}</p>;
    const changed = typed !== stored;
    return (
        <div className="telegram-chat">
            <p className="telegram-chat__title"><CheckCircleIcon size={13} /> {card.title}</p>
            <form className="field" onSubmit={(event) => { event.preventDefault(); if (changed && isCompleteMobileNumber(typed)) save.mutate(); }}>
                <label htmlFor="telegram-chat-mobile">Your mobile number</label>
                <div className="telegram-chat__number">
                    <input id="telegram-chat-mobile" value={number} inputMode="tel" autoComplete="tel" placeholder="+1 555 010 0000"
                        onChange={(event) => { save.reset(); setNumber(event.target.value); }} />
                    <button type="submit" className="btn" disabled={!changed || !isCompleteMobileNumber(typed) || save.isPending}>
                        {save.isPending ? "Saving…" : "Save"}
                    </button>
                </div>
                <span className="thismac-said">It’s how the bot recognises you when you share your contact.</span>
                {save.isError && <span className="thismac-said thismac-said--bad" role="alert">That did not save. {(save.error as Error)?.message}</span>}
                {save.isSuccess && !changed && <span className="thismac-said" role="status">Saved.</span>}
            </form>
            <div className="setup-form__acts">
                <button type="button" className="btn btn--primary" onClick={() => openExternal(card.url)}>
                    Open @{card.bot} in Telegram <ExternalIcon size={13} />
                </button>
            </div>
            <p className="thismac-said">Send it any message, then tap <strong>Share my contact</strong>.</p>
        </div>
    );
}
