"use client";

import "@/styles/desktop.css";
import { useState, type ReactNode } from "react";
import { apiUrl, hasApiUrl } from "@/session/client";
import { ComputerIcon, RefreshIcon, TerminalIcon } from "@/ui/icons";
import { agentHost } from "./agent-host";
import { useAutoConnectThisComputer, wasRemoved } from "./auto-connect";
import { capitalised, describeThisComputer, selectWorkspaceTarget, useThisComputer } from "./this-computer";

function workspace(): string | null {
    return hasApiUrl() ? apiUrl() : null;
}

/** Which of the workspace's listed computers is the one this app runs on.
 *
 *  `targets[].host_id` is the id `/me/runtime/agent-hosts` returns, so this is
 *  a join rather than a guess by name — two laptops can both be "My Mac". */
export function useThisHostId(): string | null {
    const { status, userId } = useAutoConnectThisComputer();
    if (!status) return null;
    return selectWorkspaceTarget(status.targets, workspace(), userId)?.host_id ?? null;
}

/** This computer, on the Models page, in the desktop app.
 *
 *  A browser gets "Get the app" in this place, because it has nothing to pair.
 *  Here there is nothing to ask for: the app connects this computer itself
 *  (`auto-connect.ts`), so the card only reports — one ranked state from the
 *  three status planes, the coding agents it found, and the log for when the
 *  state is not the one wanted. "Try again" appears only where a connection
 *  actually failed; everything else is a stage on its way up.
 *
 *  `children` is this computer's agent list as the backend published it,
 *  drawn by the page in its own rows so an agent here and one on another
 *  machine are the same object. */
export function ThisComputerCard({ release, children }: { release?: string; children?: ReactNode }) {
    const noun = useThisComputer();
    const { status, error, connectError, retryConnect, refetch, userId } = useAutoConnectThisComputer();
    const [logProblem, setLogProblem] = useState<string | null>(null);
    const described = describeThisComputer(status, error, workspace(), connectError, noun, userId);

    const openLog = async () => {
        setLogProblem(null);
        try {
            await agentHost.openLog();
        } catch (problem) {
            setLogProblem(problem instanceof Error ? problem.message : "The log could not be opened.");
        }
    };

    return (
        <section className="mgroup thismac" aria-label={capitalised(noun)}>
            <div className="mgroup__head">
                <ComputerIcon size={14} />
                <span className="mgroup__name">{capitalised(noun)}</span>
                <span className="mgroup__meta">{release ? "Lemma app " + release : ""}</span>
                <span className={"mrow__state mrow__state--" + described.tone} role="status">
                    <i aria-hidden="true" />
                    {described.label}
                </span>
            </div>
            <div className="thismac__body">
                <p className="thismac__detail">{described.detail}</p>
                <div className="thismac__acts">
                    {described.retry && (
                        <button className="btn" onClick={() => { retryConnect(); void refetch(); }}>
                            <RefreshIcon size={13} /> {wasRemoved(connectError) ? "Connect again" : "Try again"}
                        </button>
                    )}
                    {status?.available && (
                        <button className="linkish" onClick={() => void openLog()}>
                            <TerminalIcon size={13} /> Open log
                        </button>
                    )}
                </div>
                {logProblem && <p className="thismac__problem" role="alert">{logProblem}</p>}
            </div>
            {children}
        </section>
    );
}
