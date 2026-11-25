import { isConnectReturn } from "../areas/admin/connect/outcome";
import { appPathOf, rememberLink } from "../areas/more/links";
import { returnedRequest } from "../session/handoff-link";

/** Links that open the app from outside, before the router reads them.
 *
 *  `lemma://auth/complete?request_id=…` is how the web's sign-in page sends
 *  the person back once they confirm in the browser (`handoff.ts`). Opening
 *  the app is all it is for: the sign-in screen is already waiting on that
 *  request, or picks it up from the keystore if Android stopped the app, and
 *  takes up the session itself. So the router is given nothing to navigate
 *  to. A second sign-in screen pushed on top would claim the same request a
 *  second time and report it gone.
 *
 *  `lemma://connect/done?connect=…` is a provider sign-in coming back
 *  (`areas/admin/connect/round-trip.ts`). The auth session that opened the
 *  provider is waiting on it and reads the outcome; a screen pushed for it
 *  would land on top of the one that asked.
 *
 *  Everything else a link can open (an invitation, an install, a remix, a
 *  Lemma, a sign-in a Lemma asked for) is read in the web's grammar, from
 *  `lemma://` or from the web's own https address, and opens the phone's
 *  screen at the same path (`areas/more/links.ts`). It is also held until
 *  that screen opens: signed out, the router shows the sign-in screen, and
 *  `LinkReturn` opens the link once the person is in. */
export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
    if (returnedRequest(path) || isConnectReturn(path)) return "";
    const opens = appPathOf(path);
    if (opens === null) return path;
    rememberLink(opens);
    return opens;
}
