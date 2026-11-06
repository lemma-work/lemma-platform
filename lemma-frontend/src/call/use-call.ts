import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl, lemma } from "@/session/client";
import { createPCMPlayer, startMicPCMStream, type MicStream, type PCMPlayer } from "./pcm-audio";
import { foldTranscript, type CallLine } from "./call-transcript";
import { canvasItemFrom, type CanvasItem } from "./canvas";
import { callSocketUrl, openCallSocket, type CallConnection, type CallFrame, type CallStart } from "./call-socket";

/** Times a dropped call is joined again before it is given up as ended. */
const MAX_REJOINS = 2;
/** The wait before the first rejoin, doubled for the next: a server
 *  restarting or a network coming back is not there the instant it went. */
const REJOIN_DELAY_MS = 1_000;
/** How long a notice stays up: it says something once, it is not a state. */
const NOTICE_MS = 12_000;

export type CallStatus = "idle" | "connecting" | "live" | "ended" | "error";

/** One call, held by the server.
 *
 *  The browser is the microphone, the speaker and the screen. Which voice
 *  talks, what the agents behind it are doing and what goes on the canvas are
 *  all decided on the server, which keeps the call going whatever this tab
 *  does; this hook only moves audio, shows what is said, and draws what the
 *  agents put on the canvas.
 *
 *  The slide on screen lives here, not in the call screen: the screen
 *  unmounts when the call is collapsed, and the voice was told which slide was
 *  up -- so the screen must come back on that slide, not on the newest. */
export function useCall() {
    const [status, setStatus] = useState<CallStatus>("idle");
    const [error, setError] = useState<string | null>(null);
    /** Something the person should see, said once; it clears itself. */
    const [notice, setNotice] = useState<string | null>(null);
    const [muted, setMuted] = useState(false);
    const [transcript, setTranscript] = useState<CallLine[]>([]);
    /** Someone in the call -- the teammate or a sub-agent -- has a run going. */
    const [working, setWorking] = useState(false);
    /** Moves each time the call connects and each time work starts, so the
     *  conversation on screen reattaches to the run that is now going. */
    const [liveEpoch, setLiveEpoch] = useState(0);
    const [canvas, setCanvas] = useState<CanvasItem[]>([]);
    /** The canvas item on screen, by id; null shows the newest. */
    const [showing, setShowing] = useState<string | null>(null);
    const [level, setLevel] = useState(0);
    /** The last call the server has finished with, transcript saved, and a
     *  count that moves each time so its conversation can reload on it. The
     *  transcript is written as the server ends the call, after this tab has
     *  stopped listening; the id goes with it because the next call may
     *  already have started by then. */
    const [settled, setSettled] = useState<{ callId: string; count: number } | null>(null);
    const callIdRef = useRef<string | null>(null);
    const rejoinsRef = useRef(0);
    const startRef = useRef<((podId: string, callId: string, details: CallStart, rejoin?: boolean) => Promise<void>) | null>(null);
    const settle = useCallback((callId: string | null) => {
        if (callId) setSettled((last) => ({ callId, count: (last?.count ?? 0) + 1 }));
    }, []);

    const attemptRef = useRef(0);
    const connectionRef = useRef<CallConnection | null>(null);
    const micRef = useRef<MicStream | null>(null);
    const playerRef = useRef<PCMPlayer | null>(null);
    const mutedRef = useRef(false);
    const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    /** Only the line to the server: a rejoin keeps the microphone. */
    const dropSocket = useCallback(() => {
        playerRef.current?.stop(); playerRef.current = null;
        connectionRef.current = null;
        setLevel(0);
    }, []);

    const teardown = useCallback(() => {
        micRef.current?.stop(); micRef.current = null;
        dropSocket();
    }, [dropSocket]);

    const end = useCallback(() => {
        ++attemptRef.current;
        const connection = connectionRef.current;
        const callId = callIdRef.current;
        teardown();
        void connection?.close().then((saved) => { if (saved) settle(callId); });
        setMuted(false); mutedRef.current = false;
        setWorking(false);
        setNotice(null);
        setStatus((current) => (current === "error" ? current : "ended"));
    }, [teardown, settle]);

    useEffect(() => end, [end]);
    useEffect(() => () => { if (noticeTimer.current) clearTimeout(noticeTimer.current); }, []);

    const showNotice = useCallback((text: string) => {
        setNotice(text);
        if (noticeTimer.current) clearTimeout(noticeTimer.current);
        noticeTimer.current = setTimeout(() => setNotice(null), NOTICE_MS);
    }, []);

    const onFrame = useCallback((frame: CallFrame) => {
        switch (frame.type) {
            case "ready":
                playerRef.current?.stop();
                playerRef.current = createPCMPlayer(frame.sample_rate);
                setStatus("live");
                setLiveEpoch((epoch) => epoch + 1);
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
                if (frame.running) setLiveEpoch((epoch) => epoch + 1);
                break;
            case "notice":
                // Beside the call's status, for a while; the call carries on.
                showNotice(frame.text);
                break;
            case "canvas.item": {
                const item = canvasItemFrom(frame.item);
                if (!item) break;
                setCanvas((items) => (items.some((one) => one.id === item.id) ? items : [...items, item]));
                /* The newest takes the screen, replayed or new: after a rejoin
                   the server tells the voice the newest is up, so the screen
                   has to agree. */
                setShowing(null);
                break;
            }
        }
    }, [showNotice]);

    const start = useCallback(async (podId: string, callId: string, details: CallStart, rejoin = false) => {
        if (connectionRef.current) return;
        if (!rejoin) rejoinsRef.current = 0;
        callIdRef.current = callId;
        const attempt = ++attemptRef.current;
        // A rejoin keeps what is on screen: the server replays the canvas and
        // it lands on the same items, so the person never sees it blank out.
        setError(null); setWorking(false);
        if (!rejoin) { setTranscript([]); setCanvas([]); setShowing(null); setNotice(null); }
        setStatus("connecting");
        try {
            /* The microphone first, and once per call: asking after the socket
               let the voice start talking before the person could be heard,
               and asking again on every rejoin flickered the mic (and may
               prompt again in Safari). */
            if (!micRef.current) {
                const mic = await startMicPCMStream((pcm) => {
                    if (!mutedRef.current) connectionRef.current?.sendAudio(pcm);
                });
                if (attempt !== attemptRef.current) { mic.stop(); return; }
                micRef.current = mic;
            }
            const token = await lemma(podId).auth.getAccessToken().catch(() => null);
            if (attempt !== attemptRef.current) return;
            const connection = openCallSocket(
                callSocketUrl(apiUrl(), podId, callId, token || null),
                // Muted carries over a rejoin: the server enforces it too.
                { ...details, muted: mutedRef.current },
                {
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
                        /* A drop is not the end of the call: the server still
                           holds its conversation, its agents and its canvas.
                           Join it again, a couple of times at most, so a flaky
                           network does not leave the person on a screen that
                           has nothing to do with what the voice says. */
                        if (dropped && rejoinsRef.current < MAX_REJOINS) {
                            rejoinsRef.current += 1;
                            dropSocket();
                            setStatus("connecting");
                            const again = attemptRef.current;
                            window.setTimeout(() => {
                                // Not if the person ended the call meanwhile.
                                if (again !== attemptRef.current) return;
                                void startRef.current?.(podId, callId, details, true);
                            }, REJOIN_DELAY_MS * 2 ** (rejoinsRef.current - 1));
                            return;
                        }
                        teardown();
                        if (!reason) settle(callId);
                        if (reason) { setError(reason); setStatus("error"); } else setStatus("ended");
                    },
                },
            );
            connectionRef.current = connection;
        } catch (problem) {
            if (attempt !== attemptRef.current) return;
            setError(problem instanceof Error ? problem.message : "Could not start the call.");
            setStatus("error");
            end();
        }
    }, [onFrame, end, teardown, dropSocket, settle]);
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

    /** The person paged to this item: shown, and the voice told what they see. */
    const show = useCallback((item: CanvasItem) => {
        setShowing(item.id);
        connectionRef.current?.send({ type: "canvas.focus", tool_call_id: item.id });
    }, []);

    /** An item could not draw on this screen; whoever drew it is told, to fix it. */
    const renderFailed = useCallback((item: CanvasItem, problem: string) => {
        connectionRef.current?.send({ type: "canvas.render_failed", tool_call_id: item.id, error: problem });
    }, []);

    return {
        status, error, notice, muted, working, liveEpoch, transcript, canvas, showing, level, settled,
        start, end, toggleMute, show, renderFailed,
        active: status === "live" || status === "connecting",
    };
}
