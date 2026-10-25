/** What is being shared: the Lemma itself, or one thing in it (the web's
 *  `ShareSubject`, `space/share-sheet.tsx`). `label` is what it is called on
 *  screen; `path` and `name` are what the server knows it by. */
export type ShareSubject =
    | { kind: "lemma" }
    | { kind: "file"; path: string; label: string }
    | { kind: "table"; name: string; label: string }
    | { kind: "app"; name: string; label: string };

/** One thing in a Lemma, which has a reach of its own. */
export type SharedThing = Exclude<ShareSubject, { kind: "lemma" }>;

/** Who can open a file, a table or an app, as the server records it: its
 *  visibility (`PUBLIC`, `POD`, `PERSONAL`, `RESTRICTED`; null when it says
 *  nothing), and for a table whether each person sees only their own rows. */
export interface Reach {
    visibility: string | null;
    rowsOwn: boolean;
}

/** Share (PARITY.md, G): who can open a Lemma, a file, a table or an app,
 *  and its links.
 *
 *  The people come from settings (`admin/people`), a file's links from the
 *  files area (`files/source.ts`), an app's address from the apps list. What
 *  is left is what the web reads off the lists it already holds (the
 *  library's rows, the app tabs), which the phone asks for directly. */
export interface ShareSource {
    /** Who can open it (`files.get`, `tables.get`, `apps.get`). */
    reach(podId: string, thing: SharedThing): Promise<Reach>;
}
