"use client";

// JSON that arrives in a chat message — fenced or bare. Detection lives in
// lib/assistant/json-blocks; the block itself is the shared JsonView, so a tool
// dump in chat and a step output in a run log render identically. All this
// wrapper adds is the user-bubble palette, where the brand fill owns the colors
// and the syntax highlighting has to stand down.

import { JsonView } from "@/components/shared/json-view";
import type { AssistantJsonPayload } from "@/lib/assistant/json-blocks";

export function AssistantJsonBlock({
  json,
  isUserMessage = false,
}: {
  json: AssistantJsonPayload;
  isUserMessage?: boolean;
}) {
  // The payload is already parsed and formatted by the detector, so hand
  // JsonView the source it came from rather than re-serializing a re-parse.
  return <JsonView value={json.raw} monochrome={isUserMessage} />;
}
