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

it('a runtime notice survives the status frame it arrives on', () => {
  // The shape the backend forwards a host notice in: `status` names the frame,
  // `notice` the kind, `detail` the sentence. `normalizeStatus` only
  // recognises the run-lifecycle words, so before this every one of these
  // normalised to undefined and the sentence was never read by anything.
  expect(parseAssistantStreamEvent({
    type: 'status',
    data: {
      status: 'config_update',
      notice: 'model_unavailable',
      detail: 'Claude Code no longer offers gpt-5. It ran on its default.',
      option: 'model',
    },
  })).toEqual({
    notice: 'Claude Code no longer offers gpt-5. It ran on its default.',
    noticeKind: 'model_unavailable',
  });
});

it('a notice in the older nested shape still reads', () => {
  // Earlier backends forwarded the host's own `{status, detail}` inside
  // `detail`, under a `config_update` frame.
  expect(parseAssistantStreamEvent({
    type: 'status',
    data: {
      status: 'config_update',
      detail: { status: 'session_lost', detail: 'The agent lost its session and started a new one.' },
    },
  })).toEqual({
    notice: 'The agent lost its session and started a new one.',
    noticeKind: 'session_lost',
  });
});

it('a notice with no kind of its own falls back to the frame status', () => {
  expect(parseAssistantStreamEvent({
    type: 'status',
    data: { status: 'model_unavailable', detail: 'This agent no longer offers gpt-5.' },
  })).toEqual({ notice: 'This agent no longer offers gpt-5.', noticeKind: 'model_unavailable' });
});

it('a lifecycle status is still a status and not a notice', () => {
  expect(parseAssistantStreamEvent({
    type: 'status',
    data: { status: 'COMPLETED', detail: 'ignored' },
  })).toEqual({ status: 'COMPLETED' });
});

it('a status frame with nothing to say produces nothing', () => {
  expect(parseAssistantStreamEvent({ type: 'status', data: { status: 'config_update' } })).toEqual({});
  expect(parseAssistantStreamEvent({ type: 'status', data: { detail: '   ' } })).toEqual({});
  expect(parseAssistantStreamEvent({ type: 'status', data: { status: 'config_update', notice: 'session_lost', detail: { detail: '' } } })).toEqual({});
});
