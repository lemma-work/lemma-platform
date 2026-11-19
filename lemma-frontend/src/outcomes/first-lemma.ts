import { createLemma, freshFace } from "./data";

/** The organization's own Lemma, made with it: open to everyone in it, and
 *  joined by everyone who comes later, as a chat app's first channel is. So
 *  nobody lands in an empty container -- they land in a Lemma, on its
 *  welcome. Answers its id, or null when it could not be made: the
 *  organization exists either way, and the Lemmas page offers to make one.
 *
 *  A module of its own so the pages that make an organization by hand (the
 *  organization menu, the site's new-organization page) do not load the
 *  whole way in for one call. */
export async function firstLemma(orgId: string, name: string): Promise<string | null> {
    try {
        const made = await createLemma({ orgId, name, purpose: "", face: freshFace([]), reach: "org", people: [], everyone: true });
        return made.podId;
    } catch {
        return null;
    }
}
