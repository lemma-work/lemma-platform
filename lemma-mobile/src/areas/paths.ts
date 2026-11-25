/** Building blocks for each area's `routes.ts`. Screens link with routes,
 *  never by building paths themselves, so a route that moves moves once. */

export const part = encodeURIComponent;

/** A place under a Lemma: `/lemma/{podId}/{...rest}`. */
export function underLemma(podId: string, ...rest: string[]): string {
    return ["/lemma", part(podId), ...rest.map(part)].join("/");
}

/** One of a Lemma's sections (`ui/lemmas/sections.tsx`), on a screen of its own. */
export function lemmaTab(podId: string, tab: string): string {
    return `/lemma/${part(podId)}/section/${part(tab)}`;
}
