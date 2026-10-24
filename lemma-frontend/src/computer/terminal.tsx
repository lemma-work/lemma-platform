"use client";

import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import type { Terminal as XTerm } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import { apiUrl, lemma, sessionToken } from "@/session/client";
import {
    inputMessage, parseStreamMessage, pathRef, processStreamUrl, resizeMessage, type MachineProcess,
} from "./environments";
import { killProcess } from "./environments-queries";

type Phase = "starting" | "live" | "exited" | "failed";

/* xterm draws on a canvas and takes its colours as values, not as CSS. The
   terminal is a device like the browser screen beside it — dark in both
   appearances — so its ground is the stage's own, read from the token. */
const THEME = { foreground: "#e8e6ef", cursor: "#e8e6ef", selectionBackground: "#3a3747" };

/** A shell on one machine.
 *
 *  Starts `bash -l` in a terminal sized to this box, streams it over the
 *  process socket, and stops it again when the tab closes: a shell left
 *  running is a process nobody can see from here holding a run slot.
 *
 *  `session` is bumped by the caller to start a fresh shell after one exits.
 */
export function Terminal({ machineRef, session, onRestart }: {
    machineRef: string;
    session: number;
    onRestart: () => void;
}) {
    const box = useRef<HTMLDivElement>(null);
    const [phase, setPhase] = useState<Phase>("starting");
    const [said, setSaid] = useState<string>("");

    useEffect(() => {
        const host = box.current;
        if (!host) return;
        let cancelled = false;
        let term: XTerm | null = null;
        let fit: FitAddon | null = null;
        let socket: WebSocket | null = null;
        let processId: string | null = null;
        let observer: ResizeObserver | null = null;
        let finished = false;
        setPhase("starting");
        setSaid("");

        const sendResize = () => {
            if (!term || !fit) return;
            try { fit.fit(); } catch { return; }
            if (socket?.readyState === WebSocket.OPEN) socket.send(resizeMessage(term.cols, term.rows));
        };

        void (async () => {
            const [{ Terminal: XTermClass }, { FitAddon: FitClass }] = await Promise.all([
                import("@xterm/xterm"),
                import("@xterm/addon-fit"),
            ]);
            if (cancelled) return;
            term = new XTermClass({
                cursorBlink: true,
                /* A canvas cannot read a custom property, so the app's mono
                   face is looked up once and handed over by name. */
                fontFamily: [getComputedStyle(host).getPropertyValue("--mono").trim(), "ui-monospace", "Menlo", "monospace"].filter(Boolean).join(", "),
                fontSize: 12.5,
                theme: { ...THEME, background: getComputedStyle(host).getPropertyValue("--screen-ground").trim() || "#0e0f12" },
                scrollback: 5_000,
            });
            fit = new FitClass();
            term.loadAddon(fit);
            term.open(host);
            try { fit.fit(); } catch { /* not laid out yet; the observer fits it */ }

            let started: MachineProcess;
            try {
                started = await lemma().request<MachineProcess>("POST", `/machines/${pathRef(machineRef)}/processes`, {
                    body: { command: "bash -l", tty: { cols: Math.max(1, term.cols), rows: Math.max(1, term.rows) } },
                });
            } catch (problem) {
                if (cancelled) return;
                setPhase("failed");
                setSaid(problem instanceof Error ? problem.message : "The shell could not start.");
                return;
            }
            processId = started.id;
            if (cancelled) { void killProcess(machineRef, started.id).catch(() => undefined); return; }

            socket = new WebSocket(processStreamUrl(apiUrl(), machineRef, started.id, { accessToken: sessionToken() }));
            socket.onopen = () => { if (!cancelled) { setPhase("live"); sendResize(); term?.focus(); } };
            socket.onmessage = (event) => {
                const message = parseStreamMessage(event.data);
                if (!message || !term) return;
                if (message.type === "output") term.write(message.data);
                else {
                    finished = true;
                    setPhase("exited");
                    setSaid(message.exit_code === null ? `Shell ended (${message.state}).` : `Shell exited with code ${message.exit_code}.`);
                }
            };
            socket.onclose = () => {
                if (cancelled || finished) return;
                setPhase("failed");
                setSaid("The connection to this shell closed.");
            };
            term.onData((data) => {
                if (socket?.readyState === WebSocket.OPEN) socket.send(inputMessage(data));
            });
            observer = new ResizeObserver(() => sendResize());
            observer.observe(host);
        })();

        return () => {
            cancelled = true;
            observer?.disconnect();
            socket?.close();
            term?.dispose();
            if (processId && !finished) void killProcess(machineRef, processId).catch(() => undefined);
        };
    }, [machineRef, session]);

    return (
        <div className="envterm">
            <div className="envterm__screen" ref={box} aria-label="Terminal" />
            {phase !== "live" && (
                <p className="envterm__state" role={phase === "failed" ? "alert" : "status"}>
                    {phase === "starting" ? "Starting a shell…" : said}
                    {(phase === "exited" || phase === "failed") && (
                        <button className="computer-inline" onClick={onRestart}>New shell</button>
                    )}
                </p>
            )}
        </div>
    );
}
