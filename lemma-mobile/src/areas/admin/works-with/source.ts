import type { AskablePod, LibraryItem, LinkShare, PodLink, ResourcePage } from "./model";

/** Other Lemmas a Lemma works with: through you, or allowed (PARITY.md, F).
 *
 *  The web's `WorksWith`, `AskedBy` and `ConnectTeammate`
 *  (`space/about-page.tsx`, `space/connect-teammate.tsx`), with the same
 *  calls (`data/live.ts`: `askablePods`, `podLinks`, `connectPod`,
 *  `disconnectPod`, `listLibrary`). The live side is `live.ts`, the sample
 *  side `sample.ts`; screens reach it through `hooks.ts`. */
export interface WorksWithSource {
    /** The other Lemmas this one can ask, and how: as you, or allowed by them.
     *  Partly yours: "as you" means you are in both. */
    askable(podId: string): Promise<AskablePod[]>;
    /** The other Lemmas allowed to ask this one with nobody present, and what
     *  each reads here. */
    links(podId: string): Promise<PodLink[]>;
    /** Let `askingPodId` ask this one, reading what is shared and what is
     *  Public. Connecting again replaces every share. */
    connect(podId: string, askingPodId: string, shares: LinkShare[]): Promise<void>;
    /** Stop it asking, and take back what it read. */
    disconnect(podId: string, askingPodId: string): Promise<void>;
    /** One page of what could be shared: the tables, or one folder's listing. */
    shareable(podId: string, kind: "tables" | "files", directory: string, page?: string): Promise<ResourcePage<LibraryItem>>;
}
