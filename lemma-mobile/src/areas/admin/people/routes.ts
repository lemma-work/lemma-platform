import { underLemma } from "../../paths";

/** People's own screens past its part screen: Add people, at
 *  `(in)/lemma/[podId]/settings/people/add`. */
export const peopleRoutes = {
    add: (podId: string) => underLemma(podId, "settings", "people", "add"),
};
