"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircleIcon, ExternalIcon } from "@/ui/icons";
import { openExternalWhenReady } from "./open-external";
import { linkProblem, mintTelegramLink, telegramChatCard, telegramLinkOptions } from "./telegram-link";

/** Under the saved Telegram token: the bot by name, who answers, and the one
 *  button that opens a chat already linked to the person at this Mac. See
 *  `telegram-link.ts` for why a link rather than signup. */
export function TelegramChatLink({ saved, unsaved }: { saved: boolean; unsaved: boolean }) {
    const [podId, setPodId] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const options = useQuery({
        queryKey: ["telegram-link"],
        queryFn: () => telegramLinkOptions(),
        enabled: saved && !unsaved,
        staleTime: 60_000,
        retry: 2,
    });
    const card = telegramChatCard({ saved, unsaved, options: options.data, problem: options.error, podId });
    const open = useMutation({
        /* The link is minted inside the click, so the browser still counts the
           tab it opens as the person's own doing. */
        mutationFn: () => openExternalWhenReady(mintTelegramLink(podId ?? options.data?.podId ?? null)),
        onMutate: () => setProblem(null),
        onError: (cause) => setProblem(linkProblem(cause)),
    });

    if (card.kind === "hidden") return null;
    if (card.kind === "loading") return <p className="thismac-said telegram-chat" role="status">Asking Telegram for the bot’s name…</p>;
    if (card.kind === "problem") return <p className="thismac-said thismac-said--bad telegram-chat" role="alert">{card.text}</p>;
    const chosen = podId ?? options.data?.podId ?? "";
    return (
        <div className="telegram-chat">
            <p className="telegram-chat__title"><CheckCircleIcon size={13} /> {card.title}</p>
            {card.pods.length > 1 ? (
                <div className="field telegram-chat__pod">
                    <label htmlFor="telegram-chat-pod">Answers from</label>
                    <select id="telegram-chat-pod" value={chosen} onChange={(event) => setPodId(event.target.value)}>
                        {card.pods.map((pod) => <option key={pod.id} value={pod.id}>{pod.name}</option>)}
                    </select>
                </div>
            ) : (
                <p className="thismac-said">{card.answers}</p>
            )}
            <div className="setup-form__acts">
                <button type="button" className="btn btn--primary" disabled={open.isPending} onClick={() => open.mutate()}>
                    {open.isPending ? "Opening Telegram…" : "Chat with your agents on Telegram"} <ExternalIcon size={13} />
                </button>
            </div>
            <p className="thismac-said">Opens Telegram. Press Start there and this chat is yours — no email needed.</p>
            {problem && <p className="thismac-said thismac-said--bad" role="alert">{problem}</p>}
        </div>
    );
}
