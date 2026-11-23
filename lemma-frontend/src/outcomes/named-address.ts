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
    /** The organization's handle: unique and never renamed, so it is the word
     *  written for the organization where there is one. Its name's word is
     *  still read, as links written before handles were are spelled in it. */
    handle?: string | null;
    lemmas: readonly { id: string; name: string }[];
}

/** An organization as the reader's list has it, for telling whose a link is. */
export interface OrgNames {
    id: string;
    name: string;
    handle?: string | null;
}

/** The word an organization is written as in its addresses. */
function orgWord(org: { org?: string; name?: string; handle?: string | null }): string {
    return org.handle || slug(org.org ?? org.name ?? "");
}

/** Whether a link's first word names this organization: its handle, or the
 *  word its name makes, which older links carry. */
function namesOrg(word: string, org: { org?: string; name?: string; handle?: string | null }): boolean {
    return word === org.handle || word === slug(org.org ?? org.name ?? "");
}

/** The words that follow a Lemma's id in the app's own grammar. */
const SECTIONS: ReadonlySet<string> = new Set([
    "plan", "chats", "groups", "contacts", "apps", "pages", "about", "people", "files", "tables",
    "automations", "workflows", "runs", "agents", "settings", "computer",
]);

/** The words the workspace put after a Lemma's id, which links written before
 *  the app moved still carry (`/l/{id}/conversation/…`, `/l/{id}/record/…`).
 *  They say the word before them was an id only when it reads as one: a
 *  Lemma may be called Library or History, and `/l/acme/library` is its
 *  name. Read as names, an old link would be sent to the server as an
 *  organization called by a Lemma's id, and open nothing. */
const OLD_SECTIONS: ReadonlySet<string> = new Set([
    "conversation", "home", "all", "channels", "library", "history", "profile",
    "table", "bot", "run", "workflow", "group", "channel", "app", "record", "file",
]);

/** A Lemma's id as the server makes them. */
const AN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Whether the second word under `/l` says the first was an id. */
function followsId(first: string, second: string): boolean {
    return SECTIONS.has(second) || (OLD_SECTIONS.has(second) && AN_ID.test(first));
}

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
 *  second word is not a section, nor one of the workspace's after an id.
 *  Says nothing of whether the names are real. */
export function looksNamed(path: string): boolean {
    const [root, first, second] = parts(path);
    return root === "l" && second !== undefined && !followsId(first, second);
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
    if (!namesOrg(org, names)) return null;
    const id = byWord(names).get(lemma);
    return id ? "/l/" + encodeURIComponent(id) + tail : null;
}

/** The address to hand somebody for a Lemma: the same place in it, with the
 *  Lemma named by its id.
 *
 *  A name opens only for somebody who could already see the Lemma or walk in
 *  (`GET /pods/by-name`); an id opens for anyone, as the Lemma itself or, for
 *  somebody not in it, its door. So a link meant to be sent spells the Lemma
 *  by id, and the frame writes it back in names for whoever is in it.
 *
 *  Where the page is somewhere else -- another Lemma, or no Lemma at all --
 *  the Lemma's own address, rather than the page's. */
export function idAddress(path: string, lemma: { id: string; name: string }): string {
    const own = "/l/" + encodeURIComponent(lemma.id);
    const tail = namedTail(path);
    if (tail !== null) return parts(path)[2] === slug(lemma.name) ? own + tail : own;
    const [root, id, ...rest] = parts(path);
    if (root !== "l" || !id) return own;
    let plain: string;
    try {
        plain = decodeURIComponent(id);
    } catch {
        return own;
    }
    return plain === lemma.id ? own + rest.map((part) => "/" + part).join("") : own;
}

/** What a named address comes to, from what the frame has read so far.
 *
 *  - `plain`: the app's own address for it, by id. `asked` when only the
 *    server could say which Lemma the names mean -- the reader is not in it,
 *    so their own lists do not have it.
 *  - `wait`: something it turns on is still being read.
 *  - `elsewhere`: it names another of the reader's organizations, which the
 *    frame has to move to before the names can be read.
 *  - `ask`: the server is asked which Lemma it is -- by each of `orgs` in
 *    turn, as an organization's handle -- because no list of theirs has it,
 *    or because the word may be another organization's handle.
 *  - `nowhere`: it names no Lemma the reader may learn of. Not a door to
 *    knock on: there is no id to knock with. */
export type NamedReading =
    | { kind: "plain"; path: string; asked: boolean }
    | { kind: "wait" }
    | { kind: "elsewhere"; orgId: string }
    | { kind: "ask"; orgs: string[]; lemma: string }
    | { kind: "nowhere" };

export function readNamed(path: string, read: {
    /** The reader's organizations; null until read. */
    orgs: readonly OrgNames[] | null;
    /** The one the frame is in. */
    orgId: string | null;
    /** Its names; null until its Lemmas are read. */
    names: Names | null;
    /** The server's answer, once `ask` has had one: the Lemma's id, or null
     *  for none, and which of the asked handles found it. `failed` when the
     *  lookup itself failed, which says nothing of whose the word is. */
    found?: { podId: string | null; org?: string | null; failed?: boolean };
}): NamedReading {
    const tail = namedTail(path);
    if (tail === null) return { kind: "plain", path, asked: false };
    if (!read.orgs) return { kind: "wait" };
    const [, word, lemma] = parts(path);
    const byHandle = read.orgs.find((one) => one.handle === word);
    const own = byHandle ?? read.orgs.find((one) => namesOrg(word, one));
    /* The server reads handles. Where the reader's organization was matched
       by its name, its handle is asked after the word itself. */
    const asks = own?.handle && own.handle !== word ? [word, own.handle] : [word];
    const opened = (podId: string): NamedReading => ({ kind: "plain", path: "/l/" + encodeURIComponent(podId) + tail, asked: true });
    /* A handle is unique and a name is not, so a word that only one of theirs
       is called by may be another organization's handle. Only the server can
       tell, so it is asked before the name chooses for them. A link that
       means that organization opens it; failing that, the word is read as
       the name of theirs that links written before handles carried. */
    if (own && !byHandle) {
        if (!read.found) return { kind: "ask", orgs: asks, lemma };
        if (read.found.podId && read.found.org === word) return opened(read.found.podId);
        /* Only an answer that no handle has it lets the name choose. */
        if (read.found.failed) return { kind: "nowhere" };
    }
    if (own && own.id !== read.orgId) return { kind: "elsewhere", orgId: own.id };
    if (own) {
        if (!read.names) return { kind: "wait" };
        const local = unnamed(path, read.names);
        if (local) return { kind: "plain", path: local, asked: false };
    }
    /* Theirs or not, a Lemma they are not in is in none of their lists: an
       invite-only one in their own organization, one open to it they have not
       joined, or any in an organization they are not in. */
    if (!read.found) return { kind: "ask", orgs: asks, lemma };
    const { podId } = read.found;
    return podId ? opened(podId) : { kind: "nowhere" };
}

/** The app's own address for somewhere in a Lemma, spelled in names; the
 *  address unchanged where it is not in a Lemma, or the names would not read
 *  back to the same place. */
export function named(path: string, names: Names): string {
    const [root, id, section, ...rest] = parts(path);
    if (root !== "l" || !id || (section !== undefined && !SECTIONS.has(section))) return path;
    const org = orgWord(names);
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
