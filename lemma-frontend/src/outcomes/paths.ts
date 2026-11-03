/** The app's own paths under `/t`, told apart from the workspace addresses
 *  (`/t/{pod}/…`) that share the root. No imports, so a page outside the app
 *  -- sign-in, reading where it is sending somebody -- can ask without
 *  loading the app. */

/** The first word of every path the app writes after `/t`. Anything else
 *  there is a workspace address, from a link written before the app was the
 *  app or by a workspace page set into it. Pod ids are UUIDs, so none of
 *  these can be one. */
const FRAME_HEADS: ReadonlySet<string> = new Set(["outcomes", "needs-you", "people", "new-lemma", "chats", "outcome"]);

/** Whether a path is in the app's own grammar: `/t` and `/t/{one of the
 *  above}/…`, and `/o/…`, where the app was tried before it was `/t`. */
export function isFramePath(pathname: string): boolean {
    const [root, head] = pathname.split("/").filter(Boolean);
    if (root === "o") return true;
    return root === "t" && (head === undefined || FRAME_HEADS.has(head));
}

/** The Lemma an app path is inside, if it names one: `/t/outcome/{pod}/…`
 *  and `/t/chats/{pod}/…`. */
export function podOfFramePath(pathname: string): string | null {
    const [, head, id] = pathname.split("/").filter(Boolean);
    if ((head !== "outcome" && head !== "chats") || !id) return null;
    try {
        return decodeURIComponent(id);
    } catch {
        return null;
    }
}
