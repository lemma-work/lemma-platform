import { channelHref, goTo } from "@/shell/place-link";

/** Opening a channel at one line -- a search result, a mention in the bell.
 *  The address names the channel; which line is a request held here for the
 *  channel's page to take when it is in front, whether it was open already or
 *  is opened by this. */

export interface LineFocus {
    lineId: string;
    /** The thread it is in, when it is a reply. */
    threadRootId: string | null;
}

const pending = new Map<string, LineFocus>();
const listeners = new Set<(channelId: string) => void>();

export function openAtLine(podId: string, channelId: string, focus: LineFocus): void {
    pending.set(channelId, focus);
    for (const listener of [...listeners]) listener(channelId);
    goTo(channelHref(podId, channelId));
}

/** The line a channel was asked to open at, once: taken, it is gone. */
export function takeFocus(channelId: string): LineFocus | null {
    const held = pending.get(channelId) ?? null;
    pending.delete(channelId);
    return held;
}

export function onFocusAsked(listener: (channelId: string) => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
}
