import type { QueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { cardFor, skillFoldersFrom, SKILLS_ROOT, type SkillCard, type ListedItem } from "./skills";

/** Fetching a pod's skills, which the platform does not offer in one call.
 *
 *  There is no skills endpoint for this app to read. The catalogue the agent
 *  gets is built inside the backend by listing `/skills` and opening every
 *  `SKILL.md` in it, so listing N skills here costs N+1 requests: one listing,
 *  then one file per skill for its frontmatter. That is fine for the handful a
 *  pod has and it is the honest cost of the only route there is — but it is a
 *  cost, so the reads go out together and the answer is cached per pod.
 *
 *  Each `SKILL.md` is fetched *through the query cache*, under the same
 *  `["file", podId, path]` key `FileView` uses. That is the whole reason this
 *  takes a client rather than just awaiting: opening a skill costs nothing
 *  afterwards, because the file it opens is already the one this fetched, and
 *  a person who opened it from the Library first spends no request here.
 */

/** How long a pod's skills stay fresh. Five minutes, like the profile they
 *  sit on: a skill is a file somebody edits deliberately, not a run status. */
export const SKILLS_FRESH = 5 * 60_000;

/** A listing page is 50 rows. Four of them is 200 skills, which no pod has —
 *  but stopping at the first page would silently draw a shorter deck than the
 *  teammate actually has, and a truncation nobody is told about is the one
 *  failure this app keeps refusing to ship. */
const MAX_PAGES = 4;

export function skillsKey(podId: string): [string, string] {
    return ["skills", podId];
}

export async function readSkills(podId: string, cache: QueryClient): Promise<SkillCard[]> {
    const items: ListedItem[] = [];
    let next: string | undefined;
    for (let page = 0; page < MAX_PAGES; page += 1) {
        const listed = await source.listLibrary(podId, "files", SKILLS_ROOT, next);
        items.push(...(listed.items ?? []));
        next = listed.next || undefined;
        if (!next) break;
    }

    const folders = skillFoldersFrom(items);

    /* Together, not one after another: a pod with eight skills would otherwise
       spend eight round trips in a row for a section you can see all of at
       once. Each read fails on its own — one unreadable SKILL.md must not take
       the other seven with it. */
    return Promise.all(
        folders.map(async (folder) => {
            try {
                const file = await cache.fetchQuery({
                    queryKey: ["file", podId, folder.file],
                    queryFn: () => source.readFile(podId, folder.file),
                    staleTime: SKILLS_FRESH,
                });
                if (typeof file.text !== "string") {
                    /* `readFile` returns no text for anything it judges binary,
                       which includes a text file over the inline limit. Either
                       way there is no frontmatter to read, and the folder is
                       still a skill the teammate has. */
                    return cardFor(folder, { failure: "Its SKILL.md is here but did not come back as text, so nothing can be read from it." });
                }
                return cardFor(folder, { text: file.text });
            } catch {
                return cardFor(folder, { failure: "Its SKILL.md could not be read. It may be missing, or you may not have access to it." });
            }
        }),
    );
}
