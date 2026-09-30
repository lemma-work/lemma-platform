"use client";

import type { AnchorHTMLAttributes, MouseEvent } from "react";
import { NOWHERE, writeAddress } from "./address";

/** The address of one group's page. */
export function groupHref(podId: string, groupId: string): string {
    return writeAddress({ ...NOWHERE, podId, tabId: "group:" + groupId });
}

/** Somewhere in the workspace, from a component the shell hands no callback
 *  to. The address is written and the shell — which reads the address bar —
 *  follows it, the same road Back and a pasted link take. */
export function goTo(href: string): void {
    window.history.pushState(null, "", href);
}

/** A link to a place in the workspace that stays in the app on a plain click
 *  and is still a link: a modified click opens it in a new tab, and it reads
 *  as a link to a screen reader. `onGo` runs first, for the dialog it is in
 *  to close. */
export function PlaceLink({ href, onGo, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; onGo?: () => void }) {
    const follow = (event: MouseEvent<HTMLAnchorElement>) => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onGo?.();
        goTo(href);
    };
    return <a {...rest} href={href} onClick={follow}>{children}</a>;
}
