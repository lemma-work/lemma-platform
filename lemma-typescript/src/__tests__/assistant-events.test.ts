import { it, expect } from "vitest";
import { assistantFailureDetails, AssistantRunError, parseAssistantStreamEvent } from "../assistant-events.js";

it('preserves structured usage failure information without parsing its message', () => {
  expect(parseAssistantStreamEvent({ type: 'error', data: 'A localized message', error_code: 'USAGE_LIMIT_EXCEEDED', error_reason: 'configuration' })).toMatchObject({
    error: 'A localized message', errorCode: 'USAGE_LIMIT_EXCEEDED', errorReason: 'configuration', status: 'FAILED',
  });
});

it("preserves HTTP and stream failure reasons consistently", () => {
  expect(assistantFailureDetails(new AssistantRunError("Limit", "USAGE_LIMIT_EXCEEDED", "exhausted"))).toEqual({ code: "USAGE_LIMIT_EXCEEDED", reason: "exhausted" });
  expect(assistantFailureDetails({ code: "USAGE_LIMIT_EXCEEDED", details: { reason: "configuration" } })).toEqual({ code: "USAGE_LIMIT_EXCEEDED", reason: "configuration" });
  expect(assistantFailureDetails(new Error("unknown"))).toEqual({ code: null, reason: null });
});
