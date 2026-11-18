/** A Lemma's address in names a person can read: `/l/acme/sales/apps/people/1042`
 *  for `/l/{id}/apps/people/1042`.
 *
 *  The app's own grammar (`address.ts`) names a Lemma by its id, and keeps
 *  doing so: an id is never renamed, never shared by two Lemmas, and every
 *  link ever written carries one. This is a second spelling of the same
 *  places -- the id swapped for the organization's name and the Lemma's, and
 *  nothing else moved -- turned back into the first before anything reads it,
 *  so nothing else in the app has to know it exists.
 *
 *  The two are told apart by the word after the first. Under an id it is one
 *  of the Lemma's own sections (`chats`, `tables`, `apps`…); under names it
 *  is the Lemma, which is why a Lemma called by a section's name keeps its id.
 *
 *  An app keeps `apps/` in front of it here too. The words a Lemma's own
 *  sections go by include the ones an app is likeliest to be called -- people,
 *  contacts, pages -- so an app named straight after the Lemma could not be
 *  told from them.
 *
 *  Free of imports, like `address.ts`, so the analytics templater can tell a
 *  named address from the rest without the data layer behind it. */

/** What an organization and its Lemmas are called, for the one the reader is in. */
export interface Names {
    org: string;
    lemmas: readonly { id: string; name: string }[];
}

/** The words that follow a Lemma's id in the app's own grammar. */
const SECTIONS: ReadonlySet<string> = new Set([
    "plan", "chats", "groups", "contacts", "apps", "pages", "about", "people", "files", "tables",
    "automations", "workflows", "runs", "agents", "settings", "computer",
]);

/** Words `/l/{word}` already means something by. */
const TAKEN: ReadonlySet<string> = new Set(["new", "teammates"]);

/** A name as one word of an address: lower case, letters and digits, with a
 *  hyphen wherever anything else ran. Empty for a name with none of them. */
export function slug(name: string): string {
    return name.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function parts(path: string): string[] {
    return path.split("/").filter(Boolean);
}

/** The Lemmas whose names make a word no other Lemma's does, by that word.
 *  Two that read the same, or one that reads as a section, keep their ids. */
function byWord(names: Names): Map<string, string> {
    const seen = new Map<string, string | null>();
    for (const lemma of names.lemmas) {
        const word = slug(lemma.name);
        if (!word || SECTIONS.has(word) || TAKEN.has(word)) continue;
        seen.set(word, seen.has(word) ? null : lemma.id);
    }
    const words = new Map<string, string>();
    for (const [word, id] of seen) if (id) words.set(word, id);
    return words;
}

/** Whether an address under `/l` is spelled in names rather than an id: its
 *  second word is not a section. Says nothing of whether the names are real. */
export function looksNamed(path: string): boolean {
    const [root, , second] = parts(path);
    return root === "l" && second !== undefined && !SECTIONS.has(second);
}

/** What a named address says after the Lemma -- `/apps/people/1042`, or
 *  nothing for the Lemma itself -- and null for an address not spelled in
 *  names. What it is, without whose. */
export function namedTail(path: string): string | null {
    if (!looksNamed(path)) return null;
    return parts(path).slice(3).map((part) => "/" + part).join("");
}

/** A named address as the app's own, or null when it is not one, or names
 *  somebody else's organization or a Lemma this one does not have. */
export function unnamed(path: string, names: Names): string | null {
    const tail = namedTail(path);
    if (tail === null) return null;
    const [, org, lemma] = parts(path);
    if (org !== slug(names.org)) return null;
    const id = byWord(names).get(lemma);
    return id ? "/l/" + encodeURIComponent(id) + tail : null;
}

/** The app's own address for somewhere in a Lemma, spelled in names; the
 *  address unchanged where it is not in a Lemma, or the names would not read
 *  back to the same place. */
export function named(path: string, names: Names): string {
    const [root, id, section, ...rest] = parts(path);
    if (root !== "l" || !id || (section !== undefined && !SECTIONS.has(section))) return path;
    const org = slug(names.org);
    let plain: string;
    try {
        plain = decodeURIComponent(id);
    } catch {
        return path;
    }
    const word = [...byWord(names)].find(([, lemmaId]) => lemmaId === plain)?.[0];
    if (!org || !word) return path;
    return "/l/" + org + "/" + word + (section === undefined ? "" : "/" + [section, ...rest].join("/"));
}
