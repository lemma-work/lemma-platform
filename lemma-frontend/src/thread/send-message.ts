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

/** Say something to a run that is already going: attach, then append.
 *
 *  Every failure is said out loud before it is rethrown -- the composer only
 *  puts the draft back, so an upload that failed and was merely rethrown left
 *  the person looking at their text restored and no reason why. `putFiles`
 *  marks a failed upload on its own chip; the sentence here is what says the
 *  message did not go. Files already uploaded when the append fails are handed
 *  back as uploaded, so a retry references them rather than uploading twice. */
export async function steerConversation<A>(text: string, id: string, deps: {
    putFiles: (id: string, text: string) => Promise<{ content: string; settled: A[] }>;
    append: (id: string, content: string) => Promise<unknown>;
    clearAttachments: () => void;
    restoreAttachments: (settled: A[]) => void;
    report: (message: string) => void;
}): Promise<void> {
    const said = (problem: unknown) => problem instanceof Error ? problem.message : "That did not send.";
    let attached: { content: string; settled: A[] };
    try {
        attached = await deps.putFiles(id, text);
    } catch (problem) {
        deps.report(said(problem));
        throw problem;
    }
    deps.clearAttachments();
    try {
        await deps.append(id, attached.content);
    } catch (problem) {
        deps.restoreAttachments(attached.settled);
        deps.report(said(problem));
        throw problem;
    }
}
