/** Who is on the other side of the door.
 *
 *  A sign-in page that says "your teammates are behind your account" and then
 *  shows a blank field is making a claim it does not back up. This works out
 *  what the page can honestly say about where somebody is going, and which of
 *  the cast to put on it.
 *
 *  The destination is already parsed for safety before it reaches here, and
 *  `readAddress` already knows how to read a teammate out of one — the same
 *  grammar the workspace navigates by. So a link that sent somebody here from
 *  `/t/marketing/library` can put Marketing's own creature on the page and
 *  name them, rather than showing a stranger and saying nothing.
 */

import { readAddress } from "@/shell/address";
import { characterForSeed, CHARACTERS, type CharacterName } from "@/shell/cast";
import { readableName } from "@/library/reading";

export interface Waiting {
    /** The teammate somebody is heading back to, if the destination named one
     *  and it has a name worth saying. */
    name: string | null;
    /** Who to draw. One face when we know whose door this is, a few when we
     *  do not — a crowd is the honest picture of "your teammates" when we
     *  cannot say which. */
    faces: CharacterName[];
}

/** Whether a pod id is something to say out loud.
 *
 *  Pods are addressed by id, and an id is whatever the platform made it. A
 *  slug reads as a name; a uuid reads as a fault. Rather than print
 *  `a3f9c1e2-…` under "get back to", anything that looks generated is treated
 *  as having no name — the creature still appears, because that is derived
 *  from the id and is right either way.
 */
export function sayableName(podId: string): string | null {
    if (!podId) return null;
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(podId)) return null;
    if (/^(pod_)?[0-9a-f]{16,}$/i.test(podId)) return null;
    /* Long, or mostly digits, and it is an identifier rather than a word. */
    if (podId.length > 32) return null;
    if (/^\d+$/.test(podId)) return null;
    return readableName(podId);
}

/** A few of the cast, chosen from a seed so the same visit draws the same
 *  faces. Random per render would have them shuffling while somebody typed. */
function crowd(seed: string, howMany: number): CharacterName[] {
    const faces: CharacterName[] = [];
    let at = 0;
    for (let index = 0; index < seed.length; index += 1) at = (at * 131 + seed.charCodeAt(index)) % 1000003;
    while (faces.length < howMany) {
        at = (at * 131 + 17) % 1000003;
        const face = CHARACTERS[at % CHARACTERS.length];
        if (!faces.includes(face)) faces.push(face);
    }
    return faces;
}

/** What this sign-in is for, read off where it is sending somebody.
 *
 *  `seed` only decides which strangers appear when the destination names
 *  nobody — pass something stable for a visit, such as the path.
 */
export function waitingFor(destination: string | null, seed = "lemma"): Waiting {
    const path = pathOf(destination);
    const podId = path ? readAddress(path).podId : null;

    if (podId) return { name: sayableName(podId), faces: [characterForSeed(podId)] };
    return { name: null, faces: crowd(seed, 3) };
}

/** The path part of a destination, which may be absolute or relative. */
function pathOf(destination: string | null): string | null {
    if (!destination) return null;
    try {
        /* The base is only there to make a relative path parse; the origin is
           never read, because the destination has already been checked against
           the real one before it got here. */
        return new URL(destination, "https://lemma.invalid").pathname;
    } catch {
        return null;
    }
}
