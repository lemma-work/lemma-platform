import { useCallback, useEffect, useRef, useState } from "react";
import type { CallTransport } from "./transport";
import { createPCMPlayer, startMicPCMStream, type MicStream, type PCMPlayer } from "./pcm-audio";
import { foldTranscript, type CallLine } from "./call-transcript";
import { UtteranceBuffer } from "./utterance-buffer";
import type { VoiceEvent } from "./routing";
import { VoiceEvents } from "./voice-events";
import { instructionFor } from "./voice-instructions";

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "error";

/** Which voice model holds the microphone. Both are wired; the difference is
 *  a different file under `src/call` and a different key on the server, and
 *  nothing below this line knows which one answered. */
export type VoiceProvider = "gemini" | "gpt-live";

const PROVIDER: VoiceProvider = process.env.NEXT_PUBLIC_VOICE_PROVIDER === "gpt-live" ? "gpt-live" : "gemini";

export interface CallHandlers {
    observeTranscript: (text: string) => void;
    route: (id: string, text: string, transcript: string) => Promise<void>;
    subscribe: (listener: (event: VoiceEvent) => void) => () => void;
    history?: () => string | Promise<string>;
    context?: () => string | Promise<string>;
}

export function useCall({ teammate, pod, handlers }: { teammate: string; pod: string; handlers: CallHandlers }) {
    const [status, setStatus] = useState<CallStatus>("idle");
    const [error, setError] = useState<string | null>(null);
    const [muted, setMuted] = useState(false);
    /** What has been said, both ways. */
    const [transcript, setTranscript] = useState<CallLine[]>([]);
    /** Reasoning is still running behind a turn that already ended. Only the
     *  Gemini transport reports this; on GPT-Live it stays false, which is
     *  the truth there rather than a gap. */
    const [thinking, setThinking] = useState(false);
    /** 0–1, for the orb. The face of the call is its own mark moving. */
    const [level, setLevel] = useState(0);

    const attemptRef = useRef(0);
    const utterancesRef = useRef<UtteranceBuffer | null>(null);
    const unsubscribeRef = useRef<(() => void) | null>(null);
    const eventTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const lastUserRef = useRef(0);
    const lastVoiceRef = useRef(0);
    const awaitingAudioRef = useRef(0);
    const providerBusyRef = useRef(0);
    const latestRequestRef = useRef<string | null>(null);
    const spokenHistoryRef = useRef("");
    const lastSpeakerRef = useRef<string | null>(null);
    const startingRef = useRef(false);
    const clientRef = useRef<CallTransport | null>(null);
    const micRef = useRef<MicStream | null>(null);
    const playerRef = useRef<PCMPlayer | null>(null);
    const mutedRef = useRef(false);
    const handlersRef = useRef(handlers);
    handlersRef.current = handlers;

    const end = useCallback(() => {
        ++attemptRef.current;
        utterancesRef.current?.close(); utterancesRef.current = null;
        unsubscribeRef.current?.(); unsubscribeRef.current = null;
        if (eventTimerRef.current) clearTimeout(eventTimerRef.current);
        eventTimerRef.current = null;
        spokenHistoryRef.current = ""; lastSpeakerRef.current = null;
        startingRef.current = false;
        awaitingAudioRef.current = 0;
        providerBusyRef.current = 0;
        micRef.current?.stop();
        micRef.current = null;
        playerRef.current?.stop();
        playerRef.current = null;
        clientRef.current?.disconnect();
        clientRef.current = null;
        setLevel(0);
        setThinking(false);
        setTranscript([]);
        setMuted(false);
        mutedRef.current = false;
        setStatus((current) => (current === "error" ? current : "ended"));
    }, []);

    useEffect(() => end, [end]);

    const start = useCallback(async () => {
        if (startingRef.current || clientRef.current) return;
        startingRef.current = true;
        const attempt = ++attemptRef.current;
        setError(null);
        setThinking(false);
        setTranscript([]);
        setStatus("connecting");
        try {
            const { Client, micSampleRate } =
                PROVIDER === "gpt-live"
                    ? await import("./gpt-live-client").then((m) => ({ Client: m.GptLiveClient, micSampleRate: m.MIC_SAMPLE_RATE }))
                    : await import("./gemini-live-client").then((m) => ({ Client: m.GeminiLiveClient, micSampleRate: m.MIC_SAMPLE_RATE }));
            if (attempt !== attemptRef.current) return;

            /* Before the session is built, because it goes in the system
               instruction. A pod that cannot be read is a call without the
               background — worse, not broken. */
            const context = (await Promise.resolve(handlersRef.current.context?.()).catch(() => "")) ?? "";
            if (attempt !== attemptRef.current) return;

            const player = createPCMPlayer();
            playerRef.current = player;

            const client = new Client({
                systemInstruction: instructionFor(teammate, pod, context),
                onAudioChunk: (chunk) => {
                    if (attempt !== attemptRef.current) return;
                    awaitingAudioRef.current = 0;
                    providerBusyRef.current = Date.now();
                    lastVoiceRef.current = Date.now();
                    player.push(chunk);
                    /* Someone is speaking; the orb should show it. */
                    setLevel(0.85);
                    window.setTimeout(() => setLevel((current) => (current > 0.2 ? current * 0.5 : 0)), 180);
                },
                /* GPT-Live never fires this: it is full duplex and has no
                   interruption event, so a barge-in there is the model simply
                   stopping, and the queue drains on its own. */
                onInterrupted: () => { providerBusyRef.current = 0; awaitingAudioRef.current = 0; player.clear(); },
                onTurnComplete: () => { providerBusyRef.current = 0; awaitingAudioRef.current = 0; },
                /* Extended thinking's one new state: the model has stopped
                   speaking and has not stopped working. Worth a line in the
                   call bar, because the alternative reading of that silence
                   is that the call has died. */
                onThinking: (working) => { if (attempt === attemptRef.current) { providerBusyRef.current = working ? Date.now() : 0; setThinking(working); } },
                onTranscript: (chunk) => {
                    if (attempt !== attemptRef.current) return;
                    setTranscript((lines) => foldTranscript(lines, chunk));
                    if (chunk.text) {
                        const prefix = lastSpeakerRef.current === chunk.speaker ? "" : `\n${chunk.speaker === "them" ? "User" : "Voice"}: `;
                        spokenHistoryRef.current = (spokenHistoryRef.current + prefix + chunk.text).slice(-90000);
                        lastSpeakerRef.current = chunk.speaker;
                        handlersRef.current.observeTranscript(spokenHistoryRef.current);
                    }
                    if (chunk.final) lastSpeakerRef.current = null;
                    if (chunk.speaker === "them") {
                        lastUserRef.current = Date.now();
                        utterancesRef.current?.push(chunk.text, chunk.final);
                    }
                },
                onOpen: () => { if (attempt === attemptRef.current) setStatus("live"); },
                onClose: () => { if (attempt === attemptRef.current) end(); },
                onError: (problem) => {
                    if (attempt !== attemptRef.current) return;
                    setError(problem instanceof Error ? problem.message : "The call dropped.");
                    setStatus("error");
                    end();
                },
            });
            clientRef.current = client;
            utterancesRef.current = new UtteranceBuffer(text => {
                if (attempt !== attemptRef.current) return;
                const id = crypto.randomUUID(); latestRequestRef.current = id;
                for (const context of events.beginRequest(id)) quietUpdates.set(id, context);
                void handlersRef.current.route(id, text, spokenHistoryRef.current);
            });
            const events = new VoiceEvents();
            const quietUpdates = new Map<string, string>();
            const drain = () => {
                eventTimerRef.current = null;
                if (attempt !== attemptRef.current || (!events.waiting && !quietUpdates.size)) return;
                if (events.needsClarification) {
                    // A clarification is the direct response to the caller. It
                    // must not wait behind a speculative voice turn or a stalled
                    // background-response latch. Preserve the user's floor.
                    if (Date.now() - lastUserRef.current < 500) {
                        eventTimerRef.current = setTimeout(drain, 150); return;
                    }
                    player.clear(); awaitingAudioRef.current = 0;
                } else if ((providerBusyRef.current && Date.now() - providerBusyRef.current < 8000) || (awaitingAudioRef.current && Date.now() - awaitingAudioRef.current < 15000) || player.isPlaying() || Date.now() - Math.max(lastUserRef.current, lastVoiceRef.current) < 1200) {
                    eventTimerRef.current = setTimeout(drain, 350); return;
                }
                providerBusyRef.current = 0;
                for (const context of quietUpdates.values()) client.sendHistory(context);
                quietUpdates.clear();
                const update = events.take();
                if (update) { awaitingAudioRef.current = Date.now(); client.sendAgentReply(update); }
                if (events.waiting) eventTimerRef.current = setTimeout(drain, 350);
            };
            const receiveEvent = (event: VoiceEvent) => {
                if (attempt !== attemptRef.current) return;
                if (event.kind === "clarify" && event.id !== latestRequestRef.current) return;
                const quiet = events.receive(event);
                if (quiet) quietUpdates.set(event.conversationId ?? event.id, quiet);
                else if (event.speak) quietUpdates.delete(event.conversationId ?? event.id);
                if ((events.waiting || quietUpdates.size) && !eventTimerRef.current) eventTimerRef.current = setTimeout(drain, 350);
            };
            await client.connect();
            if (attempt !== attemptRef.current) { client.disconnect(); return; }
            unsubscribeRef.current = handlersRef.current.subscribe(receiveEvent);

            /* Before the microphone opens, so the first thing said already
               lands in context rather than to a model that knows nothing. */
            const history = await handlersRef.current.history?.();
            if (attempt !== attemptRef.current) { client.disconnect(); return; }
            if (history) client.sendHistory(history);

            const mic = await startMicPCMStream((chunk) => {
                if (!mutedRef.current) clientRef.current?.sendAudioChunk(chunk);
            }, micSampleRate);
            if (attempt !== attemptRef.current) { mic.stop(); return; }
            micRef.current = mic;
            startingRef.current = false;
        } catch (problem) {
            if (attempt !== attemptRef.current) return;
            setError(problem instanceof Error ? problem.message : "Could not start the call.");
            setStatus("error");
            end();
        }
    }, [teammate, pod, end]);

    const toggleMute = useCallback(() => {
        setMuted((was) => {
            mutedRef.current = !was;
            return !was;
        });
    }, []);

    /** Tell the voice a widget is on the screen it is talking over. */
    const announceWidget = useCallback((label: string) => {
        clientRef.current?.sendAgentWidget(label);
    }, []);

    return { status, error, muted, thinking, transcript, level, start, end, toggleMute, announceWidget, active: status === "live" || status === "connecting" };
}
