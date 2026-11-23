/** What the arrival screen does next, decided from what it has read.
 *
 *  - `wait`: the reads are not back yet.
 *  - `knock`: they opened a Lemma's link and nobody invited them, so that
 *    Lemma's door comes first.
 *  - `choose`: their company's organization could take them, which is a
 *    choice for them to make.
 *  - `begin`: accept their invitations, or make them an organization. */
export type ArrivalStep = "wait" | "knock" | "choose" | "begin";

export function arrivalStep({ invitations, suggested, linkedPodId, ownInstead }: {
    invitations: number | null;
    suggested: number | null;
    linkedPodId: string | null;
    ownInstead: boolean;
}): ArrivalStep {
    if (invitations === null || suggested === null) return "wait";
    if (invitations > 0) return "begin";
    if (linkedPodId && !ownInstead) return "knock";
    return suggested > 0 ? "choose" : "begin";
}
