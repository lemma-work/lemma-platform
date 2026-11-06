/** The app's own paths, told apart from the rest of the site -- the
 *  website, sign-in, shared pages. No imports, so a page outside the app
 *  (sign-in, reading where it is sending somebody) can ask without loading
 *  the app. */

/** The first word of every path the app writes. */
const TOP: ReadonlySet<string> = new Set(["home", "needs", "apps", "connectors", "people", "chats", "l"]);

/** Whether a path is one of the app's: `/home`, `/l/{id}/…` and the rest. */
export function isFramePath(pathname: string): boolean {
    const [head] = pathname.split("/").filter(Boolean);
    return head !== undefined && TOP.has(head);
}

/** The Lemma an app path is inside, if it names one: `/l/{id}/…`. */
export function podOfFramePath(pathname: string): string | null {
    const [head, id] = pathname.split("/").filter(Boolean);
    if (head !== "l" || !id || id === "new" || id === "teammates") return null;
    try {
        return decodeURIComponent(id);
    } catch {
        return null;
    }
}
