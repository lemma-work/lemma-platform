/** Every Lemma, and everything about one: `/l`, `/l/new`, `/l/{id}` and
 *  `/l/{id}/{section}/…`, one route.
 *
 *  The grammar is in `outcomes/address.ts` and is read in the browser, not
 *  here, by the app the layout mounts -- so moving inside it never crosses a
 *  route boundary. An address it does not read -- a link written for the
 *  workspace that lived here before, `/l/{pod}/conversation/{id}` -- is read
 *  the workspace's way and written back as the app's own.
 */
export default function LemmaRoute() {
    return null;
}
