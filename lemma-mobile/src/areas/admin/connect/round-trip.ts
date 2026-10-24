import * as WebBrowser from "expo-web-browser";
import { CONNECT_RETURN, outcomeOf, type ConnectOutcome } from "./outcome";

export { CONNECT_RETURN, isConnectReturn, outcomeNote, outcomeOf, type ConnectOutcome } from "./outcome";

/** Signing in to a provider from the phone, and hearing how it went.
 *
 *  The provider's consent page opens in the phone's browser as an auth
 *  session (a Custom Tab on Android, an `ASWebAuthenticationSession` on iOS),
 *  never in a WebView: Google and most others refuse an embedded one, and the
 *  person's own browser is where they are already signed in to the provider.
 *
 *  The backend ends every OAuth callback with a redirect to the web app
 *  (`{web}{return_to}?connect=…&connector=…&account=…`), and `return_to` may
 *  only be a path there (`safe_return_path`). So the session ends on the
 *  web's page, and the person closes it to come back. The app then reads the
 *  organization's accounts and finds the one this authorisation made: usable
 *  now, and not usable when it began (`findAccount`).
 *
 *  `CONNECT_RETURN` is the address that would bring the app back on its own,
 *  once the web's completion page knows to send a phone's round trip there
 *  (as its sign-in page does with `client=mobile`). The session already waits
 *  for it, and `+native-intent.tsx` keeps it out of the router, so that web
 *  change needs nothing more here. */

/** Open a provider's consent page and wait for the person to come back.
 *  Answers the outcome when the session ended on the app's own address with
 *  one, else null: closed by hand, which is the usual end today, and the
 *  caller then reads the accounts to see what changed. */
export async function authorize(url: string): Promise<ConnectOutcome | null> {
    const result = await WebBrowser.openAuthSessionAsync(url, CONNECT_RETURN, { showInRecents: true });
    return result.type === "success" ? outcomeOf(result.url) : null;
}

/** Open a page of the provider's own that needs no answer back: a manifest
 *  to paste, an app's settings, a developer console. */
export async function openOutside(url: string): Promise<void> {
    await WebBrowser.openBrowserAsync(url);
}
