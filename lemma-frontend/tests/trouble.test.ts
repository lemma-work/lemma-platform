import test from "node:test";
import assert from "node:assert/strict";
import { codeOf, saidAbout } from "../src/data/trouble.ts";

/** The SDK guarantees a `message` on every error and invents one where the
 *  body had none, so `problem.message` is not safe to put on a screen. These
 *  are the three inventions, and the real thing they were hiding. */

/** What the SDK builds for a refusal the platform explained. */
function refusal(status: number, body: unknown, message: string): Error {
    const problem = new Error(message) as Error & { statusCode: number; code?: string; rawResponse: unknown };
    problem.statusCode = status;
    problem.rawResponse = body;
    if (body && typeof body === "object" && "code" in body) problem.code = String((body as { code: unknown }).code);
    return problem;
}

const OPENING = {
    message: "This pod cannot be opened to everyone while its organization is not public. "
        + "Ask an organization owner to open the organization first, or use ORG_MEMBERS to open the pod to the organization.",
    code: "POD_ACCESS_DENIED",
    request_id: "c377ab806b194d56bf96991201d691da",
    details: null,
};

test("the platform's own sentence wins", () => {
    const problem = refusal(403, OPENING, OPENING.message);
    assert.equal(saidAbout(problem, "That could not be saved."), OPENING.message);
    assert.equal(codeOf(problem), "POD_ACCESS_DENIED");
});

test("a status with no sentence behind it is not a sentence", () => {
    // `catchErrorCodes` fills the message in from a table: 403 becomes
    // "Forbidden", which tells the reader nothing they did not just see.
    assert.equal(saidAbout(refusal(403, null, "Forbidden"), "That could not be saved."), "That could not be saved.");
    assert.equal(
        saidAbout(refusal(500, undefined, "Internal Server Error"), "That could not be saved."),
        "That could not be saved.",
    );
});

test("a raw body is never pasted into the UI", () => {
    // The other fallback: "Generic Error: status: 418; …; body: {…}" — the
    // whole response, JSON and all, in a paragraph under a control.
    const problem = refusal(418, { detail: "teapot" }, 'Generic Error: status: 418; status text: ; body: {\n  "detail": "teapot"\n}');
    assert.equal(saidAbout(problem, "That could not be saved."), "That could not be saved.");
});

test("a transport failure is a log line, not a message", () => {
    const dropped = new Error("Network request failed: fetch failed");
    dropped.name = "NetworkError";
    assert.equal(saidAbout(dropped, "That could not be saved."), "That could not be saved.");
});

test("a sentence this app threw itself is shown as written", () => {
    // The sample source and this app's own guards already speak English, and
    // they carry no status — that is how they are told apart.
    assert.equal(saidAbout(new Error("A teammate needs a name."), "That name would not save."), "A teammate needs a name.");
});

test("anything else falls back rather than printing itself", () => {
    assert.equal(saidAbout("nope", "Fallback."), "Fallback.");
    assert.equal(saidAbout(null, "Fallback."), "Fallback.");
    assert.equal(saidAbout(new Error("   "), "Fallback."), "Fallback.");
    assert.equal(codeOf(null), "");
});
