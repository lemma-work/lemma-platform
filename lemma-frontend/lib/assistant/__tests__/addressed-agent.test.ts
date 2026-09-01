import { describe, expect, it } from "vitest";

import { addressedAgentName } from "../addressed-agent";

describe("addressedAgentName", () => {
  it("finds a mention anywhere in the message, not only at the start", () => {
    expect(addressedAgentName("can you ask @batman about this", ["batman"]))
      .toBe("batman");
  });

  it("ignores a name that is not mentioned with @", () => {
    // The case that made this necessary: "what's up batman" is prose, and
    // routing on it would hand a turn to an agent nobody addressed.
    expect(addressedAgentName("what's up batman", ["batman"])).toBeNull();
  });

  it("prefers the longest matching name", () => {
    // Otherwise `@ops-lead` is unreachable whenever `@ops` also exists.
    expect(addressedAgentName("@ops-lead please look", ["ops", "ops-lead"]))
      .toBe("ops-lead");
  });

  it("does not match a prefix of a longer name", () => {
    expect(addressedAgentName("@batman hi", ["bat"])).toBeNull();
  });

  it("is case-insensitive but answers with the roster's spelling", () => {
    expect(addressedAgentName("@BatMan hi", ["batman"])).toBe("batman");
  });

  it("returns null when nobody is in the room", () => {
    expect(addressedAgentName("@batman hi", [])).toBeNull();
  });
});
