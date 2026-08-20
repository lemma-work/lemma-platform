"use client";

// Re-export shim. The transcript no longer renders per-message rows — the
// turn renderer (assistant-turn.tsx over lib/assistant/turns.ts) replaced
// MessageGroup and the completed-run fold. What remains here is the original
// import surface so existing importers keep working unchanged.

export {
  collectDisplayResourceCardsByRow,
  currentPodIdFromBrowserPath,
  DisplayResourceCards,
} from "./assistant-resource-cards";
export { ComposerApprovalPanel, ComposerAskUserPanel } from "./assistant-approval-cards";
export { pluralize, RunTraceHeader } from "./assistant-tool-details";
