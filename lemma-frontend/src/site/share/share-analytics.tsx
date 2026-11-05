"use client";

import { useEffect, type ReactNode } from "react";

import { captureEvent } from "@/site/analytics/client";
import type { ShareKind } from "@/site/share/share-link";

/** The top of the share loop: a shared link opened. Recorded whether or not the
 *  reader is signed in, because the reader who is not is exactly the one the
 *  funnel is about. Renders nothing. */
export function ShareViewed({ kind }: { kind: ShareKind }) {
    useEffect(() => {
        captureEvent("share_link.viewed", { kind });
    }, [kind]);
    return null;
}

/** A contact card's channel: the moment the reader leaves for Telegram,
 *  WhatsApp or their mail client. `channel` is the channel's key, never the
 *  handle the link carries. */
export function ContactChannelLink({ channel, href, children }: { channel: string; href: string; children: ReactNode }) {
    return (
        <a className="btn" href={href} onClick={() => captureEvent("share_link.contact_opened", { channel })}>
            {children}
        </a>
    );
}

/** Saving the contact: the card's conversion, and the last thing we see. */
export function SaveContactLink({ href, children }: { href: string; children: ReactNode }) {
    return (
        <a className="btn btn--primary" href={href} onClick={() => captureEvent("share_link.contact_saved")}>
            {children}
        </a>
    );
}
