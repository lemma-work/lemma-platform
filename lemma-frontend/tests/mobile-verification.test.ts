import test from "node:test";
import assert from "node:assert/strict";
import {
    isCompleteMobileNumber, normalizeMobileNumber, storedMobileNumber,
    verificationMessage, VERIFICATION_POLL_MS,
} from "../src/session/mobile-number.ts";

test("the message is the one the webhook looks for, whitespace and all", () => {
    assert.equal(verificationMessage("23456789AB"), "LEMMA VERIFY 23456789AB");
    assert.equal(verificationMessage("  23456789AB \n"), "LEMMA VERIFY 23456789AB");
});

test("the status route is asked no more than once every five seconds", () => {
    assert.equal(VERIFICATION_POLL_MS, 5_000);
});

test("a typed number keeps its digits and the plus the typist wrote", () => {
    assert.equal(normalizeMobileNumber(" +1 (415) 555-2671 "), "+14155552671");
    assert.equal(normalizeMobileNumber(""), "");
    assert.equal(normalizeMobileNumber("   "), "");
});

test("no country code is ever invented for somebody who did not write one", () => {
    assert.equal(normalizeMobileNumber("4155552671"), "4155552671");
    assert.equal(isCompleteMobileNumber(normalizeMobileNumber("4155552671")), false);
});

test("a stored number gets its plus back, having passed the server's validator once", () => {
    assert.equal(storedMobileNumber("14155552671"), "+14155552671");
    assert.equal(storedMobileNumber("+14155552671"), "+14155552671");
    assert.equal(storedMobileNumber(""), "");
});

test("empty is incomplete rather than acceptable", () => {
    assert.equal(isCompleteMobileNumber(""), false);
});

/* The bounds are `normalize_mobile_e164`'s, and these are the cases that
   separate the two: a country code cannot start at zero, and E.164 stops at
   fifteen digits. A form that let either through would spend an attempt to be
   told so. */
test("a country code starting at zero, and anything past E.164's fifteen digits, are refused", () => {
    assert.equal(isCompleteMobileNumber("+04155552671"), false);
    assert.equal(isCompleteMobileNumber("+1" + "5".repeat(14)), true);
    assert.equal(isCompleteMobileNumber("+1" + "5".repeat(15)), false);
    assert.equal(isCompleteMobileNumber("+1234567"), false);
    assert.equal(isCompleteMobileNumber("+12345678"), true);
    assert.equal(isCompleteMobileNumber("14155552671"), false);
});
