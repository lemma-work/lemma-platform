import { Platform } from "react-native";
import * as AppleAuthentication from "expo-apple-authentication";
import * as SecureStore from "expo-secure-store";
import { fetch as expoFetch } from "expo/fetch";
import { APPLE_CLIENT_TYPE, emailOf, heldNameFor, nameFrom, replyOf, signInUpBody, whyNoApple, type AppleCheck, type AppleName } from "./apple-link";
import type { Tokens } from "./auth";
import type { Server } from "./server";

/** Sign in with Apple, natively, on iOS (what is sent: `apple-link.ts`).
 *
 *  An app that offers Google has to offer Apple too (App Review 4.8), and
 *  Apple's own sheet is the way it is offered on an iPhone: no browser, no
 *  WebView, Face ID and the person's Apple account. Android has no such
 *  sheet, and Apple is not offered there. */

/** The name Apple shares on the first sign-in only, kept on the phone until
 *  a sign-in with it reaches the server: if that first one fails, Apple
 *  will not share it again. Its own keystore entry, cleared on success. */
const NAME_KEY = "lemma.apple-name";

async function heldName(appleUser: string): Promise<AppleName | null> {
    try {
        return heldNameFor(await SecureStore.getItemAsync(NAME_KEY), appleUser);
    } catch {
        return null;
    }
}

async function holdName(appleUser: string, name: AppleName | null): Promise<void> {
    try {
        if (name) await SecureStore.setItemAsync(NAME_KEY, JSON.stringify({ user: appleUser, name }));
        else await SecureStore.deleteItemAsync(NAME_KEY);
    } catch {
        /* Kept for this attempt only. */
    }
}

/** Ask the server for this app's Apple sign-in: SuperTokens' own
 *  `GET /authorisationurl` answers for the phone's client, or 400 when the
 *  server has none. */
async function checkApple(server: Server): Promise<AppleCheck> {
    const query = new URLSearchParams({
        thirdPartyId: "apple",
        clientType: APPLE_CLIENT_TYPE,
        redirectURIOnProviderDashboard: `${server.api}/st/auth/callback/apple`,
    });
    try {
        const response = await expoFetch(`${server.api}/st/auth/authorisationurl?${query}`, { headers: { Accept: "application/json" } });
        return { status: response.status, body: await response.json().catch(() => null) };
    } catch {
        return null;
    }
}

/** Servers already told, in a development build, why Apple is hidden. */
const explained = new Set<string>();

/** Whether to show "Continue with Apple" for `server`: on iOS, where the
 *  sheet is there, and only when the server offers this app's client
 *  (`whyNoApple`), so a button never leads nowhere. A development build
 *  says once per server why it is hidden, since the button decides whether
 *  App Review's guideline 4.8 is met and a silent "no" is easy to miss. */
export async function offersApple(server: Server): Promise<boolean> {
    if (Platform.OS !== "ios") return false;
    try {
        if (!(await AppleAuthentication.isAvailableAsync())) return false;
    } catch {
        return false;
    }
    const why = whyNoApple(await checkApple(server));
    if (why && typeof __DEV__ !== "undefined" && __DEV__ && !explained.has(server.id)) {
        explained.add(server.id);
        console.warn(`Sign in with Apple is hidden for ${server.label}: ${why}.`);
    }
    return why === null;
}

function cancelled(error: unknown): boolean {
    return typeof error === "object" && error !== null && (error as { code?: unknown }).code === "ERR_REQUEST_CANCELED";
}

/** Ask Apple, then the server, for a session of this app's own. Null when
 *  the person closed Apple's sheet; throws what to tell them otherwise.
 *  `onSigning` names who is being signed in while the server answers. */
export async function signInWithApple(server: Server, onSigning: (email: string | null) => void): Promise<Tokens | null> {
    let credential: AppleAuthentication.AppleAuthenticationCredential;
    try {
        credential = await AppleAuthentication.signInAsync({
            requestedScopes: [AppleAuthentication.AppleAuthenticationScope.FULL_NAME, AppleAuthentication.AppleAuthenticationScope.EMAIL],
        });
    } catch (error) {
        if (cancelled(error)) return null;
        throw new Error("Apple could not sign you in. Try again.");
    }
    const identityToken = credential.identityToken;
    if (!identityToken) throw new Error("Apple did not finish signing you in. Try again.");

    const fresh = nameFrom(credential.fullName);
    if (fresh) await holdName(credential.user, fresh);
    const name = fresh ?? (await heldName(credential.user));
    onSigning(credential.email ?? emailOf(identityToken));

    let response: Response;
    try {
        response = await expoFetch(`${server.api}/st/auth/signinup`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json", "st-auth-mode": "header", rid: "thirdparty" },
            body: JSON.stringify(signInUpBody({ identityToken, authorizationCode: credential.authorizationCode }, name)),
        }) as unknown as Response;
    } catch {
        throw new Error(`${server.label} did not answer. Check the connection and try again.`);
    }
    const reply = replyOf(response.status, (header) => response.headers.get(header), await response.json().catch(() => null));
    if ("problem" in reply) throw new Error(reply.problem);
    await holdName(credential.user, null);
    return reply.tokens;
}
