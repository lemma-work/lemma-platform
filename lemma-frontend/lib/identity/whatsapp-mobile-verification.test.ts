import { describe, expect, it } from "vitest";

import {
  buildWhatsAppVerificationMessage,
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
