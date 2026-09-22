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

test('a runtime notice survives the status frame it arrives on', () => {
  // The Agent Host writes these with a human sentence in `detail`, and the
  // backend forwards them as STATUS frames. `normalizeStatus` only recognises
  // the twelve run-lifecycle words, so every one of these normalised to
  // undefined and the sentence was never read by anything.
  expect(parseAssistantStreamEvent({
    type: 'status',
    data: {
      status: 'model_unavailable',
      detail: 'This agent no longer offers gpt-5. It answered on its own default.',
    },
  })).toEqual({
    notice: 'This agent no longer offers gpt-5. It answered on its own default.',
    noticeKind: 'model_unavailable',
  });
});

test('a lifecycle status is still a status and not a notice', () => {
  expect(parseAssistantStreamEvent({
    type: 'status',
    data: { status: 'COMPLETED', detail: 'ignored' },
  })).toEqual({ status: 'COMPLETED' });
});

test('a status frame with nothing to say produces nothing', () => {
  expect(parseAssistantStreamEvent({ type: 'status', data: { status: 'config_update' } })).toEqual({});
  expect(parseAssistantStreamEvent({ type: 'status', data: { detail: '   ' } })).toEqual({});
});
