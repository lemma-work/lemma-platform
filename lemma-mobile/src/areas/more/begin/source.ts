import type { JoinRequest } from "@web/data/joining";

/** A Lemma just made. */
export interface StartedLemma {
    id: string;
    name: string;
    /** Sample data only: its list of Lemmas is fixed, so nothing is added,
     *  and this existing one stands in for the one that would have been. */
    standsIn?: { id: string; name: string } | null;
}

/** What making a Lemma asks for, as the web's `createLemma`: its name, what
 *  it is for in a line, the face it wears (a cast name), and who can find it. */
export interface StartRequest {
    orgId: string;
    name: string;
    purpose: string;
    face: string;
    reach: "invited" | "org";
}

/** Starting a Lemma, and asking to join one that is not yours (PARITY.md, G;
 *  the frame's shelf, `outcomes/shelf.tsx`, and `shell/not-yours.tsx`).
 *
 *  The same calls the web makes (`outcomes/data.ts`: `createLemma`;
 *  `data/live.ts`: `myJoinRequest`, `askToJoin`).
 *  The live side is `live.ts`, the sample side `sample.ts`; screens reach it
 *  through `hooks.ts`, never the SDK directly. */
export interface BeginSource {
    /** Make a Lemma (`POST /pods`), open to whom it should be. */
    start(request: StartRequest): Promise<StartedLemma>;
    /** Whether a new Lemma here would have no model to answer with: the
     *  organization has none, or its default is not one of them. */
    needsModel(orgId: string): Promise<boolean>;
    /** The organization a Lemma the person can open belongs to, or null when
     *  they cannot open it. A link to one of yours in another organization
     *  is opened there, not offered a door. */
    homeOf(podId: string): Promise<string | null>;
    /** Where the signed-in person stands at a Lemma: their ask, or null.
     *  Asked without a Lemma scope, since they are not in it. */
    myJoinRequest(podId: string): Promise<JoinRequest | null>;
    /** Ask to be let in. An open door admits on the spot (`approved`). */
    askToJoin(podId: string): Promise<JoinRequest>;
}
