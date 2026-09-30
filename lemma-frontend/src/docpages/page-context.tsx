"use client";

import { createContext, useContext } from "react";
import type { HighlightAnchor } from "./editor/comment-highlight";

/** What a page needs from around it: where to go, whom to ask, and its
 *  comments. Provided by the page's own frame (`DocSpace`), read by the
 *  editor and the blocks inside it. Null wherever a markdown file is shown
 *  as a plain document — a preview in a conversation, a skill — so none of
 *  the page tools appear there. */
export interface PageTools {
    podId: string;
    path: string;
    /** The bot the page's conversation is with, by name. */
    botName: string;
    openFile: (path: string) => void;
    /** Open a pod table on its own. */
    openTable?: (name: string) => void;
    /** Where the page's save status goes: its top bar, clear of the floating
     *  chat that takes the bottom-right corner. */
    statusSlot?: HTMLElement | null;
    /** Say something to the page's bot, in the page's conversation, and send
     *  it. Null when there is no conversation to send it to. */
    sendToBot: ((text: string) => void) | null;
    comments: {
        anchors: HighlightAnchor[];
        active: string | null;
        /** Start a thread on these words (or on the page, with null). */
        start: (anchor: { quote: string; quotePrefix: string; quoteSuffix: string } | null) => void;
        focus: (id: string | null) => void;
    } | null;
}

export const PageToolsContext = createContext<PageTools | null>(null);

export function usePageTools(): PageTools | null {
    return useContext(PageToolsContext);
}

/** Where a page keeps what belongs to it: `/pages/Plan.md` keeps its images
 *  in `/pages/Plan-files/` and its sub-pages in `/pages/Plan/`. */
export function pageDirs(path: string): { assets: string; children: string } {
    const cut = path.lastIndexOf("/");
    const dir = cut > 0 ? path.slice(0, cut) : "";
    const stem = path.slice(cut + 1).replace(/\.(md|markdown)$/i, "");
    return { assets: dir + "/" + stem + "-files", children: dir + "/" + stem };
}

/** A file name safe to put in a markdown link without angle brackets. */
export function safeName(name: string): string {
    return name.trim().replace(/\s+/g, "-").replace(/[^\w.-]/g, "") || "file";
}
