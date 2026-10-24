import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { base64Url, browserUrl, codeFrom, codeInput, RETURN_URL, returnedRequest } from "../src/session/handoff-link";

const sha256 = (text: string) => new Uint8Array(createHash("sha256").update(text).digest());

describe("the code a browser sign-in shows", () => {
    it("is the code the web shows for the same request", () => {
        /* From `handoffCode` in lemma-frontend/src/desktop/auth-handoff.ts. The
           person compares the two, so they must never drift apart. */
        expect(codeFrom(sha256(codeInput("2GSg8CAqzhCX8qhib38KZk3d2-Fisgta")))).toBe("C4WC-DCA2");
        expect(codeFrom(sha256(codeInput("abcdefghijklmnopqrstuvwxyz012345")))).toBe("6KHQ-JR5G");
    });
});

describe("the PKCE challenge", () => {
    it("is the base64url SHA-256 of the verifier, unpadded", () => {
        /* RFC 7636, appendix B; the web's `challengeFor` gives the same. */
        expect(base64Url(sha256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"))).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    });
});

describe("the link out", () => {
    it("opens the web's sign-in holding the request, as the phone app's", () => {
        expect(browserUrl("https://lemma.work", "2GSg8CAqzhCX8qhib38KZk3d2-Fisgta"))
            .toBe("https://lemma.work/auth?desktop_browser=1&desktop_request=2GSg8CAqzhCX8qhib38KZk3d2-Fisgta&client=mobile");
    });
});

describe("the link back", () => {
    const id = "2GSg8CAqzhCX8qhib38KZk3d2-Fisgta";

    it("names the request, however the router hands it over", () => {
        expect(returnedRequest(`${RETURN_URL}?request_id=${id}`)).toBe(id);
        expect(returnedRequest(`lemma://auth/complete?request_id=${id}`)).toBe(id);
        expect(returnedRequest(`/auth/complete?request_id=${id}`)).toBe(id);
    });

    it("is not any other link", () => {
        expect(returnedRequest("lemma://lemma/abc")).toBeNull();
        expect(returnedRequest(`lemma://auth/completed?request_id=${id}`)).toBeNull();
        expect(returnedRequest("lemma://auth/complete?request_id=short")).toBeNull();
        expect(returnedRequest("lemma://auth/complete")).toBeNull();
    });
});
