"use client";

/* A member proves which Telegram account is theirs, so the bot knows them in
   its groups: a one-time code opened in Telegram from that account, then
   confirmed here, in their own session. Nothing is linked until they say the
   account they see is theirs -- a link is easy to pass on, and whoever opened a
   forwarded one would otherwise get their access. */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { Pod } from "@/data";
import { ChannelIcon } from "@/shell/channels";
import { Modal } from "@/shell/modal";
import { channelApi, type TelegramAccount } from "./api";
import { problemOf } from "./problem";

type Link = { code: string; url: string };

export function LinkTelegramSheet({ pod, surfaceId, contactId, seenAs, onClose }: {
    pod: Pod;
    /** The bot to open it with: the group's own. */
    surfaceId: string;
    /** Somebody in a group the space took for a stranger, who the member says
     *  is them: the code then works only from that account. */
    contactId?: string;
    /** How that person shows in the group, to say what is being linked. */
    seenAs?: string | null;
    onClose: () => void;
}) {
    const api = channelApi(pod.id);
    const [link, setLink] = useState<Link | null>(null);
    const [linked, setLinked] = useState<TelegramAccount | null>(null);
    const make = useMutation({
        mutationFn: () => api.identityLink(surfaceId, contactId),
        onSuccess: (made) => setLink({ code: made.code, url: made.url }),
    });
    const took = useRef(false);
    useEffect(() => {
        if (took.current) return;
        took.current = true;
        make.mutate();
    });
    const status = useQuery({
        queryKey: ["identity-link", pod.id, link?.code],
        queryFn: () => api.identityLinkStatus(link!.code),
        enabled: Boolean(link) && !linked,
        refetchInterval: (query) => (query.state.data?.state ?? "WAITING") === "WAITING" ? 2_000 : false,
    });
    const confirm = useMutation({
        mutationFn: () => api.confirmIdentityLink(link!.code),
        onSuccess: setLinked,
    });
    const decline = useMutation({
        mutationFn: () => api.declineIdentityLink(link!.code),
        onSuccess: onClose,
    });

    const state = status.data?.state ?? "WAITING";
    const account = status.data?.state === "OPENED" ? status.data.account : null;
    const problem = make.error ?? confirm.error ?? decline.error;
    return (
        <Modal mark={<ChannelIcon platform="TELEGRAM" size={32} />} title="Link your Telegram" onClose={onClose}>
            <div className="gsheet">
                {linked ? (
                    <p className="gsheet__lead">
                        Linked. {pod.name} knows {said(linked)} as you from the next message.
                    </p>
                ) : account ? (
                    <>
                        <p className="gsheet__lead">Is {said(account)} your Telegram account?</p>
                        <p className="gsheet__fine">Only say yes if it is. Linked, it gets your access in {pod.name}.</p>
                        <div className="gsheet__foot gsheet__foot--split">
                            <button type="button" className="ghost-pill" disabled={decline.isPending} onClick={() => decline.mutate()}>Not me</button>
                            <button type="button" className="pill-button" disabled={confirm.isPending} onClick={() => confirm.mutate()}>
                                {confirm.isPending ? "Linking…" : "Yes, link it"}
                            </button>
                        </div>
                    </>
                ) : state === "OTHER_ACCOUNT" ? (
                    <p className="gsheet__lead">
                        That link was opened from a different Telegram account than {seenAs ?? "the one in the group"}, so nothing was linked.
                    </p>
                ) : state === "GONE" ? (
                    <p className="gsheet__lead">That link has expired. Close this and try again.</p>
                ) : (
                    <>
                        <p className="gsheet__lead">
                            {seenAs ? "To show " + pod.name + " that " + seenAs + " is you, open" : "Open"} this in Telegram from your account and tap Start.
                        </p>
                        <a
                            className="pill-button gsheet__big"
                            href={link?.url}
                            target="_blank"
                            rel="noreferrer"
                            aria-disabled={!link || undefined}
                        >
                            {link ? "Open Telegram" : "Getting a link…"}
                        </a>
                        <p className="gsheet__fine">Then come back here to say it is yours. The link works for ten minutes.</p>
                    </>
                )}
                {problem && <p className="gsheet__problem" role="alert">{problemOf(problem, "Couldn’t link it.")}</p>}
                {(linked || (state !== "WAITING" && !account)) && (
                    <div className="gsheet__foot">
                        <button type="button" className="ghost-pill" onClick={onClose}>Done</button>
                    </div>
                )}
            </div>
        </Modal>
    );
}

function said(account: TelegramAccount): string {
    const name = account.name?.trim();
    const handle = account.username ? "@" + account.username : null;
    if (name && handle) return name + " (" + handle + ")";
    return name || handle || "this account";
}
