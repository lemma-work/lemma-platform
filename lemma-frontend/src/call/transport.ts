"use client";

/** Audio and context transport only. Jev routes transcripts outside the model. */
export interface CallTransportOptions {
    systemInstruction: string;
    onAudioChunk: (base64Pcm: string) => void;
    onInterrupted?: () => void;
    onTurnComplete?: () => void;
    /** Words, as they are being said, from either side. Fragments rather than
     *  lines — see `foldTranscript` for why they are joined rather than
     *  listed. Both transports report this; the field names differ, the
     *  meaning does not. */
    onTranscript?: (chunk: { speaker: "them" | "us"; text: string; final?: boolean }) => void;
    /** Whether the model still has work in flight after it stopped talking.
     *  Gemini 3.8 Live Extended Thinking only: it can finish a sentence and
     *  go on reasoning, so "it stopped speaking" and "it is done" came apart
     *  and now need two different signals. GPT-Live never fires this. */
    onThinking?: (working: boolean) => void;
    onOpen?: () => void;
    onClose?: (reason: string) => void;
    onError?: (error: unknown) => void;
}

export interface CallTransport {
    connect(): Promise<void>;
    sendAudioChunk(base64Pcm: string): void;
    /** The written conversation so far. Joins context; prompts nothing. */
    sendHistory(text: string): void;
    /** The teammate's finished answer — the one thing the voice should say. */
    sendAgentReply(text: string, id?: string): void;
    /** Something the teammate put on the call's screen. Named, not pasted:
     *  the voice model is being told a thing is visible so it can point at it
     *  instead of reading it out, which is the entire reason the widget was
     *  drawn rather than spoken. Silent — a widget appearing is not itself
     *  something to remark on. */
    sendAgentWidget(label: string): void;
    disconnect(): void;
}
