/** Adopt a newly created conversation before starting its message stream. */
export async function sendToConversation<T extends { id: string }>(text: string, deps: {
    conversationId: string | null;
    create: () => Promise<T>;
    isActive: () => boolean;
    /** Hand the new conversation to the session itself, synchronously, before
     *  anything opens a stream on it. The session cancels an in-flight stream
     *  whenever the id handed to it from outside differs from the one it holds,
     *  and the shell hands it that id a render later — by which time the send
     *  has installed the abort controller the cancel then lands on. Adopting
     *  first is what makes the shell's later, identical id a no-op. */
    adopt: (conversation: T) => void;
    onCreated: (conversation: T) => void;
    send: (text: string, id: string, knownConversation?: T) => Promise<unknown>;
}) {
    let id = deps.conversationId;
    let created: T | undefined;
    if (!id) {
        created = await deps.create();
        id = created.id;
        if (!deps.isActive()) throw new Error("Conversation changed before the message was sent.");
        deps.adopt(created);
        deps.onCreated(created);
    }
    if (!deps.isActive()) throw new Error("Conversation changed before the message was sent.");
    await deps.send(text, id, created);
}
