"use client";

import { createContext, useContext } from "react";

/** Who a selection in a doc can be handed to, and how.
 *
 *  Provided by the doc space around an editor, read by the editor. A context
 *  rather than a prop because the editor sits three components down inside
 *  `FileView`, which renders files everywhere — in a transcript card, in the
 *  library — and only a doc with a conversation beside it has anyone to ask. */
export type DocAsk = {
    /** The name on the button: the teammate the conversation is with. */
    label: string;
    /** Hand the selected passage to the doc's conversation. */
    ask: (quote: string) => void;
};

export const DocAskContext = createContext<DocAsk | null>(null);

export function useDocAsk(): DocAsk | null {
    return useContext(DocAskContext);
}

/** A passage as a quote the person then writes their request under. */
export function quoted(passage: string): string {
    const lines = passage.trim().split(/\r?\n/).map(line => "> " + line);
    return lines.join("\n") + "\n\n";
}
