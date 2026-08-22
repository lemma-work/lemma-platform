import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { sessionCookiePresent } from "./session-cookie";

const source = (path: string) =>
    readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

const cookieJar =
    (...names: string[]) =>
    (name: string) =>
        names.includes(name) ? "a-value" : undefined;

/**
 * A signed-in visitor must not be shown the marketing page.
 *
 * The root page renders one of two placeholders while the client's `/users/me`
 * check is in flight. Getting that choice wrong is not a subtle bug: it is a
 * full second of the pitch, on every visit to the root, for people who already
 * bought it. The session cookie is the only thing the server knows about them
 * that early, so this is the whole of the decision.
 */
describe("the session hint", () => {
    it("reads a session from either cookie SuperTokens sets", () => {
        // Either alone is enough. They are set together, so requiring both
        // would turn a partial expiry back into the flash this prevents.
        expect(sessionCookiePresent(cookieJar("sFrontToken"))).toBe(true);
        expect(sessionCookiePresent(cookieJar("st-access-token"))).toBe(true);
        expect(
            sessionCookiePresent(cookieJar("sFrontToken", "st-access-token")),
        ).toBe(true);
    });

    it("reads no session from a request that carries none", () => {
        // The crawler case, and the signed-out visitor's case. Both must still
        // get real marketing copy in the server-rendered HTML — that is what
        // #444 fixed, and this must not undo it.
        expect(sessionCookiePresent(cookieJar())).toBe(false);
        expect(sessionCookiePresent(cookieJar("st-refresh-token"))).toBe(false);
    });

    it("treats an empty cookie as no cookie", () => {
        // A cleared cookie is often an empty one rather than an absent one —
        // `clearFrontendSessionState` expires them by writing `name=`.
        expect(sessionCookiePresent(() => "")).toBe(false);
    });
});

/**
 * The hint is worthless unless it reaches the switch.
 *
 * A source contract rather than a render test for the same reason as the rest
 * of this suite: the failure it guards is a wiring mistake that typechecks
 * perfectly — `hasSessionCookie` defaults to false, so a call site that forgets
 * to pass it compiles, ships, and flashes the landing page exactly as before.
 */
describe("both root call sites read the hint on the server", () => {
    it.each(["app/page.tsx", "app/home/page.tsx"])("wires %s", (path) => {
        const pageSource = source(path);

        expect(pageSource).toContain("hasSessionCookie");
        expect(pageSource).toContain("@/lib/auth/server-session");
        expect(pageSource).toMatch(/<RootPageSwitch[^>]*hasSessionCookie=\{/);
    });
});
