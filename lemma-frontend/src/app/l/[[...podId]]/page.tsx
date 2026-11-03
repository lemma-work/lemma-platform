/** Every workspace URL, one route.
 *
 *  The grammar is in `shell/address.ts` and is read in the browser, not here.
 *  This file renders nothing on purpose: the workspace is mounted by the
 *  layout beside it, and it has to stay mounted — an app tab that unmounted on
 *  navigation would cold-boot somebody's app, which is the one thing the
 *  README says must never happen. So the whole grammar lives under a single
 *  optional catch-all and `pushState` never crosses a route boundary.
 *
 *  It does not validate the shape either, and that is deliberate rather than
 *  lazy. `readAddress` answers an address it cannot read with "the teammate,
 *  wherever they were left", so a link from a newer build still opens. A
 *  server that were stricter than the grammar would make the same URL behave
 *  two different ways depending on whether you arrived by link or by clicking
 *  inside the app — which is the one thing worse than either behaviour on its
 *  own.
 */
export default function PodRoute() {
    return null;
}
