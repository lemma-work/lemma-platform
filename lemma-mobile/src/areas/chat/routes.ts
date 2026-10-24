import { NEW_CHAT, routes } from "../../data/routes";
import { part } from "../paths";

/** The chat thread's places. Its screens are `(in)/chat/[podId]/[id]` and
 *  `(in)/new-chat/[podId]`, linked through `routes.chat` and
 *  `routes.newChat` in `data/routes.ts`; these add what only a chat takes. */
export const chatRoutes = {
    /** A new chat that sends `ask` as soon as it opens, with the files
     *  handed over under `files` (`thread/hand-over.ts`) going with it. */
    start: (podId: string, ask: string, files?: string | null) => routes.chat(podId, NEW_CHAT, ask) + (files ? `&files=${part(files)}` : ""),
    /** A new chat with `fill` in its box, sent by nobody: what a widget
     *  asked for when it wanted a new chat. */
    filled: (podId: string, fill: string) => `${routes.chat(podId, NEW_CHAT)}?fill=${part(fill)}`,
};
