import type { ConversationPage, ConversationRef } from "@/data";

/** Edits to the cached list of a teammate's conversations.
 *
 *  Patches rather than invalidations, and that is the whole point of the file.
 *  A title arrives on the conversation's own stream, mid-run, one string at a
 *  time; answering each one by refetching twenty-five conversations is a round
 *  trip to learn something the server has already told us, taken while the
 *  agent is still talking.
 *
 *  Every function here returns the array it was given when nothing changed, so
 *  a patch that is a no-op does not re-render the history panel.
 */

/** What a conversation with no title of its own is called.
 *
 *  It is a real state, not a missing value: the backend titles a conversation
 *  after its *first run completes*, so a conversation that has only ever been
 *  created genuinely has no name yet.
 */
export const UNTITLED = "Untitled";

/** Trim a title, and decide what an empty one means.
 *
 *  `null` rather than `""`, and this is the part that is easy to lose: `null`
 *  is what makes a conversation eligible for the backend's title generator
 *  again. Sending `""` sets the title *to* the empty string, which the
 *  generator reads as "already titled" and never touches again — so clearing a
 *  name you typed would permanently leave it nameless rather than handing it
 *  back to be generated.
 */
export function titleToSend(draft: string): string | null {
    const trimmed = draft.trim();
    return trimmed.length > 0 ? trimmed : null;
}

/** How a title should read in a list, given what the server holds. */
export function titleToShow(title: string | null | undefined, fallback = UNTITLED): string {
    return (title ?? "").trim() || fallback;
}

/** One conversation's title, replaced in place. */
export function applyTitle(
    list: ConversationRef[] | undefined,
    id: string,
    title: string | null,
): ConversationRef[] | undefined {
    if (!list) return list;
    const shown = titleToShow(title);
    let changed = false;
    const next = list.map((entry) => {
        if (entry.id !== id || entry.title === shown) return entry;
        changed = true;
        return { ...entry, title: shown };
    });
    return changed ? next : list;
}

/** One conversation, gone from the list.
 *
 *  Archiving is not deleting — the conversation is still readable by id — but
 *  the list this patches is "conversations you can open here", and an archived
 *  one is not one of those.
 */
export function applyArchived(
    list: ConversationRef[] | undefined,
    id: string,
): ConversationRef[] | undefined {
    if (!list) return list;
    const next = list.filter((entry) => entry.id !== id);
    return next.length === list.length ? list : next;
}

/** The conversations that belong in a teammate's recent history.
 *
 *  A resource's conversation is reached from the resource — that is its front
 *  door, and the reason it exists. Listing it here as well fills a five-row
 *  panel with tables and files and pushes the actual conversations off the
 *  bottom, which is the opposite of what the panel is for.
 *
 *  Note this is not `type !== "PROJECT"`. The default listing returns PROJECT
 *  conversations like any other — the type separates them for the server, not
 *  for this panel — so the binding is what has to be read.
 */
export function unbound(list: ConversationRef[] | undefined): ConversationRef[] {
    return (list ?? []).filter((entry) => !entry.boundTo);
}

/** Where the all-conversations pane keeps its pages. Under the short list's
 *  key, so invalidating `["conversations", podId]` reaches both. */
export function allConversationsKey(podId: string) {
    return ["conversations", podId, "all"] as const;
}

type ListPatch = (list: ConversationRef[] | undefined) => ConversationRef[] | undefined;

/** Just the two calls this needs, so it can be tested without a QueryClient. */
interface ConversationCache {
    getQueryData<T>(key: readonly unknown[]): T | undefined;
    setQueryData<T>(key: readonly unknown[], value: T | undefined): unknown;
}

interface Pages {
    pages: ConversationPage[];
    pageParams: unknown[];
}

/** Apply one patch to the short list and to every page the all-conversations
 *  pane has loaded, and hand back what undoes it.
 *
 *  Two caches because they are two queries: the sidebar's first page and the
 *  pane's pages. A rename made in the pane that only patched the first would
 *  show the old title in the very row that was just renamed.
 */
export function patchConversationLists(cache: ConversationCache, podId: string, patch: ListPatch): () => void {
    const shortKey = ["conversations", podId];
    const allKey = allConversationsKey(podId);
    const short = cache.getQueryData<ConversationRef[]>(shortKey);
    const all = cache.getQueryData<Pages>(allKey);

    if (short) cache.setQueryData(shortKey, patch(short));
    if (all) {
        let changed = false;
        const pages = all.pages.map((page) => {
            const items = patch(page.items) ?? page.items;
            if (items === page.items) return page;
            changed = true;
            return { ...page, items };
        });
        if (changed) cache.setQueryData<Pages>(allKey, { ...all, pages });
    }

    return () => {
        if (short) cache.setQueryData(shortKey, short);
        if (all) cache.setQueryData(allKey, all);
    };
}
