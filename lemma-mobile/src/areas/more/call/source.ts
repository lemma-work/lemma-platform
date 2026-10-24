import type { RawMessage } from "@web/thread/turns";
import type { CallReadiness, FromPage, ToPage } from "./model";
import type { VoiceEvent } from "@web/call/routing";

/** Call (PARITY.md, G): a voice call with a Lemma.
 *
 *  What a call reads and does with the phone's own session. Its sound is not
 *  here: live, it is the page in a WebView the screen mounts (`engine.tsx`);
 *  in sample data, a scripted voice (`engine` below). */
export interface CallSource {
    /** Whether this server can carry a call, and on which voice model:
     *  `{web}/api/call/config` and `voiceProvider` in `{web}/site-config.js`. */
    readiness(): Promise<CallReadiness>;
    /** What the voice is told about the Lemma before it says anything
     *  (`web/pod-brief.ts`, from the reads the web's profile makes). */
    brief(podId: string, name: string): Promise<string>;
    /** What is said, routed to the Lemma's chats: the web's
     *  `ConversationRouter`, its conversations streaming over the pod socket.
     *  `changed` is called whenever what it holds moves. */
    session(podId: string, changed: () => void): CallSession;
    /** The call's sound when it is not the page: sample data's scripted
     *  voice. `null` means the page (and the phone's microphone). */
    engine: ((podId: string) => EngineLink) | null;
}

/** The routing half of a call: the web's `ConversationRouter`, as the call
 *  uses it. */
export interface CallSession {
    /** Start following the open chat (or none) and the Lemma's others. */
    open(selectedId: string | null): Promise<string | null>;
    /** Stop following. Never cancels work already accepted. */
    close(): void;
    route(id: string, text: string, transcript: string): Promise<void>;
    subscribe(listener: (event: VoiceEvent) => void): () => void;
    setContext(text: string): void;
    observeTranscript(text: string): void;
    /** The chat the call is about now. */
    readonly focusedId: string | null;
    readonly messages: ReadonlyMap<string, readonly RawMessage[]>;
    readonly snapshots: ReadonlyMap<string, { title: string }>;
    /** Whether the Lemma is still working on something the call asked. */
    readonly isRunning: boolean;
}

/** The call's sound, as the call drives it: the page in a WebView, or a
 *  scripted voice. */
export interface EngineLink {
    /** Begin: whatever the engine says comes to `hear`. */
    open(hear: (message: FromPage) => void): void;
    post(message: ToPage): void;
    close(): void;
}
