"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { SessionReport } from "./beacon";
import { loadLastFrame, pictureIn, saveLastFrame } from "./last-frame";
import { retryDelay, type LiveState } from "./live";
import { LiveScreen } from "./live-screen";
import { LiveScreenRtc } from "./live-screen-rtc";
import { newViewerId, presetNamed, type PresetName } from "./presets";
import { sendStreamBeacon, useBrowserResize } from "./queries";
import {
    RTC_FAILURES_BEFORE_VNC, chooseTransport, connectMode, rtcFailureGoesToVnc, rtcSupported,
    type FallbackReason, type StreamSetting, type Transport,
} from "./stream";

/** The machine's screen, over whichever transport this person gets.
 *
 *  WebRTC where the server has rolled it out to them and this browser can
 *  hold a peer connection; VNC everywhere else, and VNC the moment WebRTC
 *  fails to connect within five seconds of the machine sending its ICE
 *  config (a cold browser start before that is not counted) — a person sees
 *  "Connecting…" a little longer, never an error, and the beacon records why.
 *
 *  Three things live here rather than in either transport, because they are
 *  the same for both:
 *
 *  - **The screen's size.** A preset, asked for once when a driving pane
 *    connects or somebody picks another; the pane rescales the picture
 *    locally whatever its own shape.
 *  - **The last picture**, shown dimmed while the stream comes up.
 *  - **The beacon**, sent once when the pane goes away.
 */
export function LiveView({
    mode, origin, conversationId, reopen = 0, setting, density = 1, preset, wantControl,
    onState, onControl, onAgentDriving, onResizeRefused, onTransport, onRevoked, onMachineClipboard,
}: {
    /** What the pane is for: watching or, for a sign-in, typing. */
    mode: "view" | "control";
    origin?: string | null;
    conversationId?: string | null;
    reopen?: number;
    /** The server's word on the transport (`BROWSER_STREAM`). `undefined`
     *  while it is still being asked: nothing connects until it is known, so
     *  a pane never opens VNC only to drop it a moment later for WebRTC.
     *  `null` is known-and-unsaid, which is VNC. */
    setting?: StreamSetting | null;
    density?: number;
    /** The preset to hold the screen at while this pane drives. */
    preset?: PresetName | null;
    /** Whether this pane wants to drive. Defaults to `mode === "control"`. */
    wantControl?: boolean;
    onState: (state: LiveState) => void;
    onControl?: (driving: boolean, mayDrive: boolean) => void;
    onAgentDriving?: (driving: boolean) => void;
    onResizeRefused?: (reason: string) => void;
    onTransport?: (transport: Transport, fallback: FallbackReason | null) => void;
    /** Somebody else took control from this pane (WebRTC only). */
    onRevoked?: () => void;
    /** The machine's latest clipboard, for a "Copy from machine" action. */
    onMachineClipboard?: (text: string) => void;
}) {
    const viewer = useMemo(() => newViewerId(), []);
    const choice = useMemo(
        () => chooseTransport(setting, { rtcSupported: rtcSupported(), override: overrideFromUrl() }),
        [setting],
    );
    const [transport, setTransport] = useState<Transport>(choice.first);
    const [retry, setRetry] = useState(0);
    const [state, setState] = useState<LiveState>("connecting");
    const wantsControl = wantControl ?? mode === "control";
    /* VNC's mode is fixed per socket, so for VNC "driving" is the mode the
       pane connected in; WebRTC says whether control was granted. */
    const [rtcDriving, setRtcDriving] = useState(false);
    const driving = transport === "vnc" ? wantsControl : rtcDriving;
    const holder = useRef<HTMLDivElement>(null);
    const report = useRef<SessionReport | null>(null);
    const handlers = useRef({ onState, onControl, onTransport, onResizeRefused });
    handlers.current = { onState, onControl, onTransport, onResizeRefused };
    const [lastFrame] = useState(loadLastFrame);

    useEffect(() => { setTransport(choice.first); }, [choice.first]);
    useEffect(() => {
        handlers.current.onTransport?.(transport, choice.reason ?? null);
    }, [transport, choice.reason]);

    /* One report per mounted pane, sent when it goes away. */
    useEffect(() => {
        const session = new SessionReport(choice.first, density);
        if (choice.reason) session.fellBack(choice.reason);
        report.current = session;
        return () => {
            saveLastFrame(pictureIn(holder.current));
            const beacon = session.beacon();
            if (beacon) sendStreamBeacon({ ...beacon });
        };
    }, [choice, density]);

    /* WebRTC asked for alone retries in place; this counts how often in a
       row, so it too ends on VNC rather than retrying forever. */
    const forcedFailures = useRef(0);

    const reportState = (next: LiveState) => {
        if (next === "live") {
            report.current?.firstFrame();
            forcedFailures.current = 0;
        }
        setState(next);
        handlers.current.onState(next);
    };

    const fellBack = (reason: FallbackReason, gaveUp = false) => {
        const done = gaveUp || (!choice.fallback && ++forcedFailures.current >= RTC_FAILURES_BEFORE_VNC);
        if (rtcFailureGoesToVnc(reason, { fallback: choice.fallback, gaveUp: done })) {
            report.current?.fellBack(reason);
            setTransport("vnc");
            return;
        }
        /* WebRTC was asked for alone and may still work: try it again. */
        reportState("lost");
        setTimeout(() => setRetry((n) => n + 1), retryDelay(1));
    };

    /* Keep a picture of the page now and then, so the next open starts on it. */
    useEffect(() => {
        if (state !== "live") return;
        const timer = setInterval(() => saveLastFrame(pictureIn(holder.current)), 15_000);
        return () => clearInterval(timer);
    }, [state]);

    useSize({ preset, live: state === "live", driving, viewer, onRefused: (reason) => handlers.current.onResizeRefused?.(reason) });

    return (
        <div className="screen-view" ref={holder}>
            {state !== "live" && lastFrame && <img className="screen-last" src={lastFrame} alt="" />}
            {setting === undefined ? null : transport === "webrtc" ? (
                <LiveScreenRtc
                    key={retry}
                    origin={origin}
                    conversationId={conversationId}
                    reopen={reopen}
                    viewer={viewer}
                    wantControl={wantsControl}
                    onState={reportState}
                    onFallback={fellBack}
                    onControl={(isDriving, mayDrive) => {
                        setRtcDriving(isDriving);
                        handlers.current.onControl?.(isDriving, mayDrive);
                    }}
                    onAgentDriving={onAgentDriving}
                    onStats={(stats) => report.current?.sample(stats)}
                    onRevoked={onRevoked}
                    onMachineClipboard={onMachineClipboard}
                />
            ) : (
                <LiveScreen
                    mode={connectMode(wantsControl)}
                    origin={origin}
                    conversationId={conversationId}
                    reopen={reopen}
                    viewer={viewer}
                    onMachineClipboard={onMachineClipboard}
                    onState={(next) => {
                        reportState(next);
                        if (next === "live") handlers.current.onControl?.(wantsControl, true);
                    }}
                />
            )}
        </div>
    );
}

/** Hold the screen at `preset` while this pane drives a live connection.
 *
 *  Never in response to the pane changing shape — the reason this exists.
 *  Asked again on each new connection, because a display that restarted came
 *  back at its resting size.
 */
function useSize({ preset, live, driving, viewer, onRefused }: {
    preset: PresetName | null | undefined;
    live: boolean;
    driving: boolean;
    viewer: string;
    onRefused: (reason: string) => void;
}) {
    const resize = useBrowserResize();
    const ask = useRef(resize.mutate);
    ask.current = resize.mutate;
    const refused = useRef(onRefused);
    refused.current = onRefused;
    useEffect(() => {
        if (!preset || !live || !driving) return;
        const { width, height } = presetNamed(preset);
        ask.current({ width, height, viewer }, {
            onSuccess: (answer) => {
                if (!answer?.size && answer?.reason) refused.current(answer.reason);
            },
        });
    }, [preset, live, driving, viewer]);
}

function overrideFromUrl(): string | null {
    try {
        return new URLSearchParams(globalThis.location?.search ?? "").get("stream");
    } catch {
        return null;
    }
}
