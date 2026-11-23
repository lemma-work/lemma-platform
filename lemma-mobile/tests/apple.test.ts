import { describe, expect, it } from "vitest";
import { APPLE_CLIENT_TYPE, CODE_KEY, emailOf, heldNameFor, nameFrom, replyOf, signInUpBody } from "../src/session/apple-link";

const base64Url = (text: string) => Buffer.from(text).toString("base64url");
const idToken = (claims: Record<string, unknown>) => `${base64Url('{"alg":"RS256"}')}.${base64Url(JSON.stringify(claims))}.signature`;
const headers = (held: Record<string, string>) => (name: string) => held[name] ?? null;

describe("what the phone sends", () => {
    it("names the server's phone client and carries the code beside the id token", () => {
        /* `clientType` and the code's key are read on the server
           (`NATIVE_CLIENT_TYPE`, `NATIVE_CODE_KEY` in apple.py). */
        expect(APPLE_CLIENT_TYPE).toBe("ios");
        expect(CODE_KEY).toBe("authorization_code");
        expect(signInUpBody({ identityToken: "t", authorizationCode: "c" }, null)).toEqual({
            thirdPartyId: "apple",
            clientType: "ios",
            oAuthTokens: { id_token: "t", authorization_code: "c" },
        });
    });

    it("sends the name in Apple's own shape, which the server already reads", () => {
        const body = signInUpBody({ identityToken: "t", authorizationCode: null }, { firstName: "Ada", lastName: null });
        expect(body.oAuthTokens).toEqual({ id_token: "t", user: { name: { firstName: "Ada", lastName: "" } } });
    });
});

describe("the name Apple shares once", () => {
    it("is taken when Apple gives one, and is no name when it gives blanks", () => {
        expect(nameFrom({ givenName: "  Ada ", familyName: "Lovelace  Byron" })).toEqual({ firstName: "Ada", lastName: "Lovelace Byron" });
        expect(nameFrom({ givenName: " ", familyName: null })).toBeNull();
        expect(nameFrom(null)).toBeNull();
    });

    it("is read back from the keystore only for the account it came with", () => {
        const held = JSON.stringify({ user: "001234.abc", name: { firstName: "Ada", lastName: "Byron" } });
        expect(heldNameFor(held, "001234.abc")).toEqual({ firstName: "Ada", lastName: "Byron" });
        expect(heldNameFor(held, "009999.other")).toBeNull();
        expect(heldNameFor("not json", "001234.abc")).toBeNull();
        expect(heldNameFor(null, "001234.abc")).toBeNull();
    });
});

describe("who is being signed in", () => {
    it("is the address in Apple's id token", () => {
        expect(emailOf(idToken({ email: "x7@privaterelay.appleid.com" }))).toBe("x7@privaterelay.appleid.com");
        expect(emailOf("not-a-token")).toBeNull();
    });
});

describe("what the server answers", () => {
    const now = 1_800_000_000_000;
    const front = Buffer.from(JSON.stringify({ uid: "u-1", ate: now + 3_600_000, up: {} })).toString("base64");

    it("is the pair the app keeps, from the headers of a header-mode sign-in", () => {
        const reply = replyOf(
            200,
            headers({ "st-access-token": "a.access", "st-refresh-token": "r.refresh", "front-token": front }),
            { status: "OK", user: { id: "u-1", emails: ["ada@example.com"] }, createdNewRecipeUser: true },
            now,
        );
        expect(reply).toEqual({ tokens: { access: "a.access", refresh: "r.refresh", expiresAt: now + 3_600_000, userId: "u-1", email: "ada@example.com" } });
    });

    it("guesses when the access token runs out if the server did not say", () => {
        const reply = replyOf(200, headers({ "st-access-token": "a", "st-refresh-token": "r" }), { status: "OK", user: {} }, now);
        expect(reply).toEqual({ tokens: { access: "a", refresh: "r", expiresAt: now + 15 * 60_000, userId: null, email: null } });
    });

    it("is no session without both tokens", () => {
        expect(replyOf(200, headers({ "st-access-token": "a" }), { status: "OK" }, now)).toHaveProperty("problem");
    });

    it("says why, in the server's words, when this account cannot sign in", () => {
        const reason = "This email already signs in with a password. Sign in with it instead.";
        expect(replyOf(200, headers({}), { status: "SIGN_IN_UP_NOT_ALLOWED", reason }, now)).toEqual({ problem: reason });
        expect(replyOf(200, headers({}), { status: "NO_EMAIL_GIVEN_BY_PROVIDER" }, now)).toEqual({
            problem: "Apple did not share an email address, and Lemma needs one to sign you in.",
        });
    });

    it("tells a server without the phone's client apart from one that failed", () => {
        expect(replyOf(400, headers({}), null, now)).toEqual({ problem: "This server does not offer Sign in with Apple on the phone." });
        expect(replyOf(429, headers({}), null, now)).toEqual({ problem: "Too many sign-ins were tried from here. Wait a minute and try again." });
        expect(replyOf(502, headers({}), null, now)).toEqual({ problem: "Signing in with Apple did not work (502). Try again." });
    });
});
