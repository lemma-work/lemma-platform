import { describe, expect, it } from "vitest";

import { withClientMessageId } from "../namespaces/conversations.js";

/** A send the server can recognise when it arrives twice.
 *
 *  The server stores one message per `client_message_id` and answers a repeat
 *  with the first send's run, so every send carries one: the caller's, the one
 *  the web app already put in the metadata, or a fresh one. */
describe("withClientMessageId", () => {
  it("keeps an id the caller chose", () => {
    const payload = withClientMessageId({ content: "hi", client_message_id: "mine" });

    expect(payload.client_message_id).toBe("mine");
  });

  it("lifts an id carried in the metadata", () => {
    const payload = withClientMessageId({
      content: "hi",
      metadata: { client_message_id: "from-metadata" },
    });

    expect(payload.client_message_id).toBe("from-metadata");
    expect(payload.metadata).toEqual({ client_message_id: "from-metadata" });
  });

  it("makes a new id for each send that has none", () => {
    const first = withClientMessageId({ content: "hi" });
    const second = withClientMessageId({ content: "hi" });

    expect(first.client_message_id).toBeTruthy();
    expect(second.client_message_id).toBeTruthy();
    expect(first.client_message_id).not.toBe(second.client_message_id);
  });
});
