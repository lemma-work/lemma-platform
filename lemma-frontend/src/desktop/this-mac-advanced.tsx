"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friendlyError, thisMac, type ThisMacSnapshot } from "./this-mac";
import { SettingRow } from "./this-mac-settings";

/** Diagnostics: where Lemma keeps its state, its addresses, the last of each
 *  log, and the install-health switch. The Advanced part of Server setup;
 *  the credentials that used to share this page are Server setup's cards. */

/* ── diagnostics ───────────────────────────────────────────────────── */

const LOG_POLL_MS = 2_000;

function Logs() {
    const [source, setSource] = useState<string | null>(null);
    const [body, setBody] = useState("");
    const [sources, setSources] = useState<{ id: string; label: string }[]>([]);
    const [problem, setProblem] = useState<string | null>(null);
    const cursor = useRef<string | null>(null);

    useEffect(() => {
        let stopped = false;
        cursor.current = null;
        setBody("");
        const read = async () => {
            try {
                const answer = await thisMac.diagnosticLogs(source, cursor.current);
                if (stopped) return;
                const entries = answer?.entries ?? "";
                /* The shell says "No …" rather than nothing when a log is
                   empty; appended, that sentence would repeat every tick. */
                setBody((was) => (cursor.current && entries.startsWith("No ") ? was : cursor.current ? was + entries : entries));
                cursor.current = answer?.nextCursor ?? null;
                if (answer?.sources) setSources(answer.sources);
                setProblem(null);
            } catch (cause) {
                if (!stopped) setProblem(friendlyError(cause));
            }
        };
        void read();
        const timer = window.setInterval(() => void read(), LOG_POLL_MS);
        return () => { stopped = true; window.clearInterval(timer); };
    }, [source]);

    return (
        <div className="thismac-logs">
            <div className="theme__modes" role="tablist" aria-label="Log">
                {sources.map((one) => (
                    <button key={one.id} type="button" role="tab" className="theme__mode"
                        aria-selected={(source ?? sources[0]?.id) === one.id} aria-pressed={(source ?? sources[0]?.id) === one.id}
                        onClick={() => setSource(one.id)}>{one.label}</button>
                ))}
            </div>
            {/* Text, never markup: these lines carry error strings, URLs and
                tool output the app did not write. Redacted by the shell. */}
            <pre className="thismac-log" tabIndex={0}>{problem ?? (body.trim() || "No entries yet.")}</pre>
        </div>
    );
}

function Telemetry() {
    const status = useQuery({ queryKey: ["this-mac-telemetry"], queryFn: () => thisMac.telemetryStatus(), retry: 0 });
    const queryClient = useQueryClient();
    const change = useMutation({
        mutationFn: (enabled: boolean) => thisMac.setTelemetry(enabled),
        onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["this-mac-telemetry"] }),
    });
    /* A build that cannot send anything offers nothing, rather than a switch
       that does not do anything. */
    if (!status.data?.available) return null;
    return (
        <SettingRow
            name="Anonymous install health"
            consequence={`Whether Lemma started and its runtime installed, sent to ${status.data.host ?? "Lemma"} under a random id for this installation. Nothing about your pods, files or account.`}
        >
            <input type="checkbox" role="switch" className="thismac-switch" aria-label="Send anonymous install health"
                checked={Boolean(status.data.enabled)} disabled={change.isPending}
                onChange={(event) => change.mutate(event.target.checked)} />
            {change.isError && <span className="thismac-said thismac-said--bad" role="alert">{friendlyError(change.error)}</span>}
        </SettingRow>
    );
}

export function ThisMacDiagnostics({ snapshot }: { snapshot: ThisMacSnapshot }) {
    const [showLogs, setShowLogs] = useState(false);
    return (
        <>
            {snapshot.paths && (
                <SettingRow name="Where Lemma keeps its state" consequence={snapshot.paths.locald}>
                    <button className="linkish" onClick={() => void thisMac.openLogs()}>Open logs folder</button>
                </SettingRow>
            )}
            {snapshot.state.url && (
                <SettingRow name="Addresses" consequence={`Workspace ${snapshot.state.url} · API ${snapshot.state.api_url}`} />
            )}
            <SettingRow name="Logs" consequence="The last of each log, redacted, refreshed while open.">
                <button className="btn" aria-expanded={showLogs} onClick={() => setShowLogs((was) => !was)}>{showLogs ? "Hide" : "Show"}</button>
            </SettingRow>
            {showLogs && <Logs />}
            <Telemetry />
        </>
    );
}
