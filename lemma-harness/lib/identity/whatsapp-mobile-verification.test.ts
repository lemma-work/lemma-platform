import { describe, expect, it } from "vitest";

import {
  buildWhatsAppVerificationMessage,
  isCompleteMobileNumber,
  normalizeMobileNumber,
  normalizeStoredMobileNumber,
  WHATSAPP_VERIFICATION_POLL_INTERVAL_MS,
} from "./whatsapp-mobile-verification";

describe("WhatsApp mobile verification presentation", () => {
  it("copies the complete reserved verification message", () => {
    expect(buildWhatsAppVerificationMessage("23456789AB")).toBe(
      "LEMMA VERIFY 23456789AB",
    );
  });

  it("polls no more frequently than every five seconds", () => {
    expect(WHATSAPP_VERIFICATION_POLL_INTERVAL_MS).toBe(5_000);
  });
});

describe("mobile numbers", () => {
  it("keeps a typed number's digits and its leading plus", () => {
    expect(normalizeMobileNumber(" +1 (415) 555-2671 ")).toBe("+14155552671");
  });

  it("never invents a country code the typist did not write", () => {
    expect(normalizeMobileNumber("4155552671")).toBe("4155552671");
    expect(isCompleteMobileNumber(normalizeMobileNumber("4155552671"))).toBe(false);
  });

  it("restores the plus on a stored number, which already passed validation", () => {
    expect(normalizeStoredMobileNumber("14155552671")).toBe("+14155552671");
    expect(normalizeStoredMobileNumber("")).toBe("");
  });

  it("treats an empty number as incomplete rather than acceptable", () => {
    expect(isCompleteMobileNumber("")).toBe(false);
  });

  it("rejects a country code starting at zero and a number past E.164 length", () => {
    expect(isCompleteMobileNumber("+04155552671")).toBe(false);
    expect(isCompleteMobileNumber(`+1${"5".repeat(15)}`)).toBe(false);
  });
});
