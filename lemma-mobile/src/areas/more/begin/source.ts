import type { JoinRequest } from "@web/data/joining";

/** A Lemma just made. */
export interface MadeLemma {
    id: string;
    name: string;
    /** Sample data only: its list of Lemmas is fixed, so nothing is added,
     *  and this existing one stands in for the one that would have been. */
    standsIn?: { id: string; name: string } | null;
}

/** Starting a Lemma, and asking to join one that is not yours (PARITY.md, G;
 *  the `/o` frame's new-Lemma list, the web's `stage/hiring.tsx` and
 *  `shell/not-yours.tsx`).
 *
 *  The same calls the web makes (`data/live.ts`: `createPod`, `setPodIcon`,
 *  `myJoinRequest`, `askToJoin`; `outcomes/data.ts`: `savePlan`, `askIn`).
 *  The live side is `live.ts`, the sample side `sample.ts`; screens reach it
 *  through `hooks.ts`, never the SDK directly. */
export interface BeginSource {
    /** A new Lemma in the organization, with what it is for as its
     *  description. */
    create(orgId: string, name: string, about: string): Promise<MadeLemma>;
    /** Store the face it was given (`lemma-identity:N`). */
    setFace(podId: string, icon: string): Promise<void>;
    /** Write its first plan to `/plan.md`, a file that does not exist yet. */
    writePlan(podId: string, text: string): Promise<void>;
    /** A new chat with it, these words said in it; answers the chat's id. */
    askToSetUp(podId: string, words: string): Promise<string>;
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
