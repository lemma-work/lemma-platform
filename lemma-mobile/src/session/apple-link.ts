import type { Tokens } from "./auth";

/** Sign in with Apple on the phone: what is sent and what comes back, as
 *  plain data so it can be tested away from the device (`apple.ts` does the
 *  asking).
 *
 *  Apple signs the person in on the phone itself and hands the app an id
 *  token issued to the app's bundle id, plus a one-time authorization code.
 *  Both go to SuperTokens' own `POST /st/auth/signinup`, naming the
 *  provider's phone client (`clientType: "ios"`, registered on the server
 *  from `APPLE_APP_BUNDLE_ID`), in header mode: the answer carries the
 *  session in `st-access-token` and `st-refresh-token`, the same pair the
 *  app keeps from every other way in, and refreshes at `/auth/cli/refresh`
 *  like those. The server trades the code for the refresh token Apple is
 *  asked to revoke when the account is deleted. */

/** The provider's client for this app, as the server registers it. */
export const APPLE_CLIENT_TYPE = "ios";

/** Where the server reads the code (`NATIVE_CODE_KEY` in `apple.py`). */
export const CODE_KEY = "authorization_code";

export interface AppleName {
    firstName: string | null;
    lastName: string | null;
}

/** The part of Apple's credential the server takes. */
export interface AppleCredential {
    identityToken: string;
    authorizationCode: string | null;
}

const clean = (value: string | null | undefined): string | null => {
    const said = (value ?? "").replace(/\s+/g, " ").trim();
    return said || null;
};

/** The name Apple shares on the first sign-in only, or null when it gave none. */
export function nameFrom(fullName: { givenName?: string | null; familyName?: string | null } | null | undefined): AppleName | null {
    const firstName = clean(fullName?.givenName);
    const lastName = clean(fullName?.familyName);
    return firstName || lastName ? { firstName, lastName } : null;
}

/** A name kept for an Apple account until a sign-in with it reaches the
 *  server, read back from the keystore; null when it is not that account's. */
export function heldNameFor(held: string | null, appleUser: string): AppleName | null {
    if (!held) return null;
    try {
        const kept = JSON.parse(held) as { user?: unknown; name?: { firstName?: unknown; lastName?: unknown } };
        if (kept.user !== appleUser) return null;
        return nameFrom({
            givenName: typeof kept.name?.firstName === "string" ? kept.name.firstName : null,
            familyName: typeof kept.name?.lastName === "string" ? kept.name.lastName : null,
        });
    } catch {
        return null;
    }
}

/** The body for `POST /st/auth/signinup`. The name goes in Apple's own
 *  shape, `user.name`, which the server already reads for the web's
 *  sign-in, and only when there is one: an empty one would be no name. */
export function signInUpBody(credential: AppleCredential, name: AppleName | null): Record<string, unknown> {
    const tokens: Record<string, unknown> = { id_token: credential.identityToken };
    if (credential.authorizationCode) tokens[CODE_KEY] = credential.authorizationCode;
    if (name) tokens.user = { name: { firstName: name.firstName ?? "", lastName: name.lastName ?? "" } };
    return { thirdPartyId: "apple", clientType: APPLE_CLIENT_TYPE, oAuthTokens: tokens };
}

function decodeBase64(text: string): string | null {
    try {
        const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
        const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
        const bytes = Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
        return new TextDecoder().decode(bytes);
    } catch {
        return null;
    }
}

function jsonOf(text: string | null): Record<string, unknown> | null {
    if (!text) return null;
    try {
        const parsed: unknown = JSON.parse(text);
        return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
    } catch {
        return null;
    }
}

/** The address in Apple's id token, for "Signing in as …" while the server
 *  answers. Read, not trusted: the server checks the token. */
export function emailOf(identityToken: string): string | null {
    const payload = jsonOf(decodeBase64(identityToken.split(".")[1] ?? ""));
    return typeof payload?.email === "string" ? payload.email : null;
}

export type SignInReply = { tokens: Tokens } | { problem: string };

/** What `/st/auth/signinup` answered: the session, or what to tell the
 *  person. `header` reads one response header. */
export function replyOf(status: number, header: (name: string) => string | null, raw: unknown, now = Date.now()): SignInReply {
    const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    if (status === 429) return { problem: "Too many sign-ins were tried from here. Wait a minute and try again." };
    if (status === 400) return { problem: "This server does not offer Sign in with Apple on the phone." };
    if (status !== 200) return { problem: `Signing in with Apple did not work (${status}). Try again.` };
    switch (body.status) {
        case "OK":
            break;
        case "SIGN_IN_UP_NOT_ALLOWED":
            /* The server's own words: an address that already signs in
               another way, or a closed sign-up. */
            return { problem: typeof body.reason === "string" && body.reason ? body.reason : "This Apple account cannot sign in here." };
        case "NO_EMAIL_GIVEN_BY_PROVIDER":
            return { problem: "Apple did not share an email address, and Lemma needs one to sign you in." };
        default:
            return { problem: typeof body.message === "string" && body.message ? body.message : "Signing in with Apple did not work. Try again." };
    }
    const access = header("st-access-token");
    const refresh = header("st-refresh-token");
    if (!access || !refresh) return { problem: "The server signed you in but sent no session. Try again." };
    /* `front-token` is base64 JSON whose `ate` is when the access token runs
       out; without it, the same guess `tokensFrom` makes. */
    const front = jsonOf(decodeBase64(header("front-token") ?? ""));
    const expires = Number(front?.ate);
    const user = body.user && typeof body.user === "object" ? (body.user as { id?: unknown; emails?: unknown }) : {};
    const emails = Array.isArray(user.emails) ? user.emails : [];
    return {
        tokens: {
            access,
            refresh,
            expiresAt: Number.isFinite(expires) && expires > 0 ? expires : now + 15 * 60_000,
            userId: typeof user.id === "string" ? user.id : null,
            email: typeof emails[0] === "string" ? emails[0] : null,
        },
    };
}
