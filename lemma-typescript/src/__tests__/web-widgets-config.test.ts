import { afterEach, describe, expect, it } from "vitest";

import { LemmaClient } from "../client.js";

/* A hosted app is told its pod's web widgets in window.__LEMMA_CONFIG__, so a
   Public app reaches its pod's chat without carrying the key in its links. */

const BOOKING = { name: "Booking", publicKey: "pk_booking", scriptUrl: "https://api.x.test/public/web/widget.js" };
const SHOP = { name: "Shop", publicKey: "pk_shop", scriptUrl: "https://api.x.test/public/web/widget.js" };

function hostSays(config: Record<string, unknown>): void {
  (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__ = { apiUrl: "https://api.x.test", ...config };
}

describe("LemmaClient.webWidget", () => {
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__LEMMA_CONFIG__;
  });

  it("gives the page the widget the host named", () => {
    hostSays({ podId: "p", webWidgets: [BOOKING, SHOP] });
    const client = new LemmaClient();

    expect(client.webWidget()).toEqual(BOOKING);
    expect(client.webWidget("Shop")).toEqual(SHOP);
    expect(client.webWidget("Nope")).toBeUndefined();
  });

  it("has none to give when the host named none", () => {
    hostSays({ podId: "p" });
    expect(new LemmaClient().webWidget()).toBeUndefined();
  });
});
