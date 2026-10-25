import type { CallSocketHandlers, CallStart } from "@web/call/call-socket";

/** Call (PARITY.md, G): a voice call with a Lemma, held by the server.
 *
 *  What a call reads and does with the phone's own session. Its sound is
 *  not here: live, it is the page in a WebView the screen mounts
 *  (`engine.tsx`); in sample data there is none, and a scripted socket
 *  stands in for the server. */
export interface CallSource {
    /** Whether this server can carry a call (`calls.config` names a voice). */
    callable(podId: string): Promise<boolean>;
    /** Open a call with the Lemma. A call is a conversation the server holds,
     *  and its id is the call's. */
    open(podId: string, name: string): Promise<string>;
    /** What the voice is told before it says anything: the Lemma
     *  (`pod-brief.ts`), and the chat the call grew out of, when it did
     *  (`history-brief.ts`). */
    brief(podId: string, name: string, conversationId: string | null): Promise<string>;
    /** The call's socket (the web's `openCallSocket`), carrying `start` once
     *  it opens. */
    connect(podId: string, callId: string, start: CallStart, handlers: CallSocketHandlers): Promise<CallLink>;
    /** Whether the call's sound is the phone's: the page, its microphone and
     *  its speaker. Sample data has none. */
    readonly sound: boolean;
}

/** The open call, as the phone drives it: the web's `CallConnection`. */
export interface CallLink {
    sendAudio(pcm: Int16Array): void;
    send(control: Record<string, unknown>): void;
    close(): void;
}
