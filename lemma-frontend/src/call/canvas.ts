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

/** Which item is on screen: the one the person paged to (`showing`, by id),
 *  else the newest. The call holds `showing` and clears it whenever an item
 *  arrives, replayed or new, since the voice is told the newest is up. */
export function onScreenIndex(items: readonly { id: string }[], showing: string | null): number {
    const picked = showing === null ? -1 : items.findIndex((one) => one.id === showing);
    return picked >= 0 ? picked : Math.max(0, items.length - 1);
}
