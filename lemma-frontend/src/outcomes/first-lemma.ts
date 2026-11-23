import { createLemma, freshFace } from "./data";

/** A workspace of one's first Lemma, as the server names it too. */
const PERSONAL_LEMMA = "Personal";

/** The organization's first Lemma, made with it, so nobody lands in an empty
 *  container -- they land in a Lemma, on its welcome. A team's is open to
 *  everyone in it and joined by everyone who comes later, as a chat app's
 *  first channel is, and named after it. A workspace of one's is "Personal",
 *  its person's own: nobody invited later is put in it. The server makes the
 *  same two for a first workspace. Answers its id, or null when it could not
 *  be made: the organization exists either way, and the Lemmas page offers to
 *  make one.
 *
 *  A module of its own so the pages that make an organization by hand (the
 *  organization menu, the site's new-organization page) do not load the
 *  whole way in for one call. */
export async function firstLemma(orgId: string, name: string, { personal = false }: { personal?: boolean } = {}): Promise<string | null> {
    try {
        const made = await createLemma(personal
            ? { orgId, name: PERSONAL_LEMMA, purpose: "", face: freshFace([]), reach: "invited", people: [] }
            : { orgId, name, purpose: "", face: freshFace([]), reach: "org", people: [], everyone: true });
        return made.podId;
    } catch {
        return null;
    }
}
