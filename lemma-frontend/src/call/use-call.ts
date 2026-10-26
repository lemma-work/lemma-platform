import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl, lemma } from "@/session/client";
import { createPCMPlayer, startMicPCMStream, type MicStream, type PCMPlayer } from "./pcm-audio";
import { foldTranscript, type CallLine } from "./call-transcript";
import { canvasItemFrom, type CanvasItem } from "./canvas";
import { callSocketUrl, openCallSocket, type CallConnection, type CallFrame, type CallStart } from "./call-socket";

/** Times a dropped call is joined again before it is given up as ended. */
const MAX_REJOINS = 2;

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "error";

/** One call, held by the server.
 *
 *  The browser is the microphone, the speaker and the screen. Which voice
 *  talks, what the agents behind it are doing and what goes on the canvas are
 *  all decided on the server, which keeps the call going whatever this tab
 *  does; this hook only moves audio, shows what is said, and draws what the
 *  agents put on the canvas. */
export function useCall({ podId }: { podId: string }) {
    const [status, setStatus] = useState<CallStatus>("idle");
    const [error, setError] = useState<string | null>(null);
    const [muted, setMuted] = useState(false);
    const [transcript, setTranscript] = useState<CallLine[]>([]);
    /** The coordinator has a run going: the call is still working on something. */
    const [working, setWorking] = useState(false);
    const [canvas, setCanvas] = useState<CanvasItem[]>([]);
    const [level, setLevel] = useState(0);
    /** The last call the server has finished with, transcript saved, and a
     *  count that moves each time so its conversation can reload on it. The
     *  transcript is written as the server ends the call, after this tab has
     *  stopped listening; the id goes with it because the next call may
     *  already have started by then. */
    const [settled, setSettled] = useState<{ callId: string; count: number } | null>(null);
    const callIdRef = useRef<string | null>(null);
    const rejoinsRef = useRef(0);
    const startRef = useRef<((callId: string, details: CallStart, rejoin?: boolean) => Promise<void>) | null>(null);
    const settle = useCallback((callId: string | null) => {
        if (callId) setSettled((last) => ({ callId, count: (last?.count ?? 0) + 1 }));
    }, []);

    const attemptRef = useRef(0);
    const connectionRef = useRef<CallConnection | null>(null);
    const micRef = useRef<MicStream | null>(null);
    const playerRef = useRef<PCMPlayer | null>(null);
    const mutedRef = useRef(false);

    const teardown = useCallback(() => {
        micRef.current?.stop(); micRef.current = null;
        playerRef.current?.stop(); playerRef.current = null;
        connectionRef.current = null;
        setLevel(0);
    }, []);

    const end = useCallback(() => {
        ++attemptRef.current;
        const connection = connectionRef.current;
        const callId = callIdRef.current;
        teardown();
        void connection?.close().then((saved) => { if (saved) settle(callId); });
        setMuted(false); mutedRef.current = false;
        setWorking(false);
        setStatus((current) => (current === "error" ? current : "ended"));
    }, [teardown, settle]);

    useEffect(() => end, [end]);

    const onFrame = useCallback((frame: CallFrame) => {
        switch (frame.type) {
            case "ready":
                playerRef.current?.stop();
                playerRef.current = createPCMPlayer(frame.sample_rate);
                setStatus("live");
                break;
            case "transcript":
                setTranscript((lines) => foldTranscript(lines, { speaker: frame.speaker === "caller" ? "them" : "us", text: frame.text, final: frame.final }));
                break;
            case "clear":
                // The caller cut in: what the voice had queued is not said late.
                playerRef.current?.clear();
                break;
            case "work":
                setWorking(frame.running);
                break;
            case "notice":
                // Shown where the call's status goes; the call itself carries on.
                setError(frame.text);
                break;
            case "canvas.item": {
                const item = canvasItemFrom(frame.item);
                if (item) setCanvas((items) => (items.some((one) => one.id === item.id) ? items : [...items, item]));
                break;
            }
        }
    }, []);

    const start = useCallback(async (callId: string, details: CallStart, rejoin = false) => {
        if (connectionRef.current) return;
        if (!rejoin) rejoinsRef.current = 0;
        callIdRef.current = callId;
        const attempt = ++attemptRef.current;
        // A rejoin keeps what is on screen: the server replays the canvas and
        // it lands on the same items, so the person never sees it blank out.
        setError(null); setWorking(false);
        if (!rejoin) { setTranscript([]); setCanvas([]); }
        setStatus("connecting");
        try {
            const token = await lemma(podId).auth.getAccessToken().catch(() => null);
            if (attempt !== attemptRef.current) return;
            const connection = openCallSocket(callSocketUrl(apiUrl(), podId, callId, token || null), details, {
                onFrame: (frame) => { if (attempt === attemptRef.current) onFrame(frame); },
                onAudio: (pcm) => {
                    if (attempt !== attemptRef.current) return;
                    playerRef.current?.push(pcm);
                    setLevel(0.85);
                    window.setTimeout(() => setLevel((current) => (current > 0.2 ? current * 0.5 : 0)), 180);
                },
                onClose: (reason, dropped) => {
                    if (attempt !== attemptRef.current) return;
                    ++attemptRef.current;
                    teardown();
                    /* A drop is not the end of the call: the server still holds
                       its conversation, its agents and its canvas. Join it
                       again, a couple of times at most, so a flaky network
                       does not leave the person on a screen that has nothing
                       to do with what the voice says. */
                    if (dropped && rejoinsRef.current < MAX_REJOINS) {
                        rejoinsRef.current += 1;
                        void startRef.current?.(callId, details, true);
                        return;
                    }
                    if (!reason) settle(callId);
                    if (reason) { setError(reason); setStatus("error"); } else setStatus("ended");
                },
            });
            connectionRef.current = connection;
            const mic = await startMicPCMStream((pcm) => {
                if (!mutedRef.current) connectionRef.current?.sendAudio(pcm);
            });
            if (attempt !== attemptRef.current) { mic.stop(); return; }
            micRef.current = mic;
        } catch (problem) {
            if (attempt !== attemptRef.current) return;
            setError(problem instanceof Error ? problem.message : "Could not start the call.");
            setStatus("error");
            end();
        }
    }, [podId, onFrame, end, teardown, settle]);
    startRef.current = start;

    /* The ref first, outside React's updater: the microphone callback reads it
       on its own schedule, and an updater runs when React gets to it (twice in
       Strict Mode), so audio could still go out after Mute was pressed. */
    const toggleMute = useCallback(() => {
        const next = !mutedRef.current;
        mutedRef.current = next;
        connectionRef.current?.send({ type: "mute", muted: next });
        setMuted(next);
    }, []);

    /** The person paged to this canvas item: the voice should know what they see. */
    const looking = useCallback((item: CanvasItem) => {
        connectionRef.current?.send({ type: "canvas.focus", tool_call_id: item.id });
    }, []);

    /** An item could not draw on this screen; whoever drew it is told, to fix it. */
    const renderFailed = useCallback((item: CanvasItem, problem: string) => {
        connectionRef.current?.send({ type: "canvas.render_failed", tool_call_id: item.id, error: problem });
    }, []);

    return {
        status, error, muted, working, transcript, canvas, level, settled,
        start, end, toggleMute, looking, renderFailed,
        active: status === "live" || status === "connecting",
    };
}
