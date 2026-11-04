/** A call's own conversation is marked `call` in its metadata (the server's
 *  `CALL_METADATA_KEY`). Read before briefing a new call from the open
 *  conversation: an earlier call's is not the chat a new call grew out of. */
export function isCallConversation(conversation: { metadata?: Record<string, unknown> | null } | null): boolean {
    const metadata = conversation?.metadata;
    return Boolean(metadata && typeof metadata === "object" && "call" in metadata);
}
