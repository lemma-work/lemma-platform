import { underLemma } from "../../paths";
import { adminRoutes } from "../routes";

/** Where a Lemma is reached. The list is the Lemma's "channels" setting
 *  (`settings/[section]`); a connected place and the way to connect one are
 *  screens of their own:
 *
 *  - `place`: `(in)/lemma/[podId]/settings/reach/place/[name]`, one place by
 *    its name (unique in a Lemma);
 *  - `add`: `(in)/lemma/[podId]/settings/reach/add/[platform]`, connecting
 *    one, `taken` when the organization's account there turned out to be
 *    another Lemma's. */
export const reachRoutes = {
    list: (podId: string) => adminRoutes.lemmaSection(podId, "channels"),
    place: (podId: string, name: string) => underLemma(podId, "settings", "reach", "place", name),
    add: (podId: string, platform: string, options: { taken?: boolean } = {}) =>
        underLemma(podId, "settings", "reach", "add", platform) + (options.taken ? "?taken=1" : ""),
};
