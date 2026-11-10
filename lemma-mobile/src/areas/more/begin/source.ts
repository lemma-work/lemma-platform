import type { JoinRequest } from "@web/data/joining";
import type { ShelfCard } from "@web/outcomes/model";

/** A Lemma just started, and where it sets itself up. */
export interface StartedLemma {
    id: string;
    name: string;
    /** The chat its agent sets itself up in. */
    conversationId: string;
    /** The import bringing a template's tables, skills and schedules. */
    importId: string | null;
    /** Sample data only: its list of Lemmas is fixed, so nothing is added,
     *  and this existing one stands in for the one that would have been. */
    standsIn?: { id: string; name: string } | null;
}

/** What starting a Lemma asks for: a template (with a date for a finish
 *  line), or what it owns in its owner's own words, and its name. */
export type StartRequest =
    | { orgId: string; template: string; due: string | null }
    | { orgId: string; name: string; sentence: string; kind: "week" | "date"; due: string | null };

/** How far a template's import has got (`ImportStatusResponse`). */
export interface SetupStatus {
    status: string;
    steps: { kind: string; name: string }[];
    error: string | null;
}

/** Starting a Lemma, and asking to join one that is not yours (PARITY.md, G;
 *  the frame's shelf, `outcomes/shelf.tsx`, and `shell/not-yours.tsx`).
 *
 *  The same calls the web makes (`outcomes/data.ts`: `useShelf`,
 *  `startLemma`, `useSetup`; `data/live.ts`: `myJoinRequest`, `askToJoin`).
 *  The live side is `live.ts`, the sample side `sample.ts`; screens reach it
 *  through `hooks.ts`, never the SDK directly. */
export interface BeginSource {
    /** The templates a new Lemma can start from, as the server ships them. */
    templates(): Promise<ShelfCard[]>;
    /** Start a Lemma (`POST /pods/start`): the pod, its setup chat, and for
     *  a template the import bringing what it starts with. */
    start(request: StartRequest): Promise<StartedLemma>;
    /** Wear a template's face (a cast path, as the web stores it). */
    setFace(podId: string, icon: string): Promise<void>;
    /** A template's import, while it comes in. */
    setup(podId: string, importId: string): Promise<SetupStatus>;
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
