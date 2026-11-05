import { parseDisplayResource, resourceLabel, type DisplayResource } from "@/thread/display-resource";
import type { CanvasFrameItem } from "./call-socket";

/** One thing on the call's canvas: a `display_resource` from anyone in the
 *  call, the same call that draws it in the conversation afterwards.
 *
 *  It carries its own conversation because anyone in the call may draw -- the
 *  coordinator and each of its sub-agents -- and a widget is served by the
 *  conversation and tool call that made it. */
export interface CanvasItem {
    id: string;
    conversationId: string;
    resource: DisplayResource;
    label: string;
}

export function canvasItemFrom(frame: CanvasFrameItem): CanvasItem | null {
    const resource = parseDisplayResource(frame.args, frame.result);
    if (!resource || !frame.tool_call_id || !frame.conversation_id) return null;
    return { id: frame.tool_call_id, conversationId: frame.conversation_id, resource, label: resourceLabel(resource) };
}
