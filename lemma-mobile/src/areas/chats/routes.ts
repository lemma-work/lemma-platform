import { lemmaTab, part, underLemma } from "../paths";

/** Chats across Lemmas, groups, people and contacts. `(in)/chats` is every
 *  chat; a group, someone's own chat with a Lemma or a contact's chat is
 *  `(in)/shared/[podId]/[id]`; what hangs off one -- a thread, its details --
 *  and the contacts are under the Lemma (`(in)/lemma/[podId]/...`). */

function query(pairs: Record<string, string | null | undefined>): string {
    const said = Object.entries(pairs).filter((pair): pair is [string, string] => Boolean(pair[1]));
    return said.length ? "?" + said.map(([name, value]) => name + "=" + part(value)).join("&") : "";
}

export type DetailsTab = "about" | "people" | "answers" | "connected" | "settings";
export type ContactsTab = "inbox" | "people" | "setup";

export const chatsRoutes = {
    all: "/chats" as const,
    chats: (podId: string) => lemmaTab(podId, "chats"),
    people: (podId: string) => lemmaTab(podId, "people"),
    /** A chat that lives on a channel; `line` opens it at one message, and
     *  `thread` at that message's thread. */
    shared: (podId: string, id: string, at: { line?: string | null; thread?: string | null } = {}) =>
        `/shared/${part(podId)}/${part(id)}` + query({ line: at.line, thread: at.thread }),
    /** One thread of a chat, on its own screen; `line` is a reply to go to. */
    thread: (podId: string, channelId: string, rootId: string, line?: string | null) =>
        underLemma(podId, "thread", channelId, rootId) + query({ line }),
    details: (podId: string, channelId: string, tab?: DetailsTab) => underLemma(podId, "group", channelId) + query({ tab }),
    newGroup: (podId: string) => underLemma(podId, "new-group"),
    contacts: (podId: string, tab?: ContactsTab) => underLemma(podId, "contacts") + query({ tab }),
    contact: (podId: string, contactId: string) => underLemma(podId, "contact", contactId),
};
