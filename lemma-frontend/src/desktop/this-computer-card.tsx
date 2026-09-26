"use client";

import "@/styles/desktop.css";
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { agentUpdateCommand, source, stillLooking } from "@/data";
import { apiUrl, hasApiUrl } from "@/session/client";
import { LoadingIndicator } from "@/ui/loading";
import { ComputerIcon, DownloadIcon, RefreshIcon, TerminalIcon } from "@/ui/icons";
import { agentHost, ownSettingsRow, useAgentHost } from "./agent-host";
import { useAutoConnectThisComputer, wasRemoved } from "./auto-connect";
import { invoke } from "./bridge";
import { openSettings } from "./open-settings";
import { thisMacReachable } from "./this-mac";
import {
    STALLED_AFTER_MS,
    capitalised,
    describeThisComputer,
    plainHostError,
    selectWorkspaceTarget,
    useThisComputer,
} from "./this-computer";

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

/** Whether `stage` has been the state for longer than a stage should last.
 *
 *  Keyed by the stage, so moving from connecting to starting starts the clock
 *  again, and a person's click — which changes the key — does too: pressing
 *  "Connect again" must not be answered at once by the verdict it was
 *  pressed to overturn. */
function useStalled(stage: string | null): boolean {
    const [stalled, setStalled] = useState<string | null>(null);
    useEffect(() => {
        if (!stage) return;
        const timer = setTimeout(() => setStalled(stage), STALLED_AFTER_MS);
        return () => clearTimeout(timer);
    }, [stage]);
    return stage !== null && stalled === stage;
}

/** Where updating Lemma happens from this page.
 *
 *  Settings → This Mac → Updates on a local install's own window; Local
 *  settings everywhere else in the app, which is the one place a hosted
 *  workspace can reach the updater from. */
async function checkForUpdates(): Promise<void> {
    if (thisMacReachable()) {
        openSettings("this-mac-updates");
        return;
    }
    await invoke("open_control_center", { page: "overview" });
}

/** Make this computer look for its agents now and republish them, then read
 *  the workspace's list again. The host otherwise looks on its own every
 *  fifteen minutes, which is a long time to wait after signing in. */
export function useCheckAgain() {
    const queryClient = useQueryClient();
    const [checking, setChecking] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const noun = useThisComputer();
    const check = async () => {
        setChecking(true);
        setProblem(null);
        try {
            await agentHost.refresh();
        } catch (cause) {
            setProblem(plainHostError(cause instanceof Error ? cause.message : String(cause), noun));
        } finally {
            setChecking(false);
            void queryClient.invalidateQueries({ queryKey: ["computers"] });
        }
    };
    return { check, checking, problem };
}

export function CheckAgainButton() {
    const { check, checking, problem } = useCheckAgain();
    return (
        <>
            <button className="btn" disabled={checking} onClick={() => void check()}>
                <RefreshIcon size={13} className={checking ? "spin" : undefined} /> {checking ? "Checking…" : "Check again"}
            </button>
            {problem && <p className="thismac__problem" role="alert">{problem}</p>}
        </>
    );
}

/** This computer, on the Models page, in the desktop app.
 *
 *  A browser gets "Get the app" in this place, because it has nothing to pair.
 *  Here there is nothing to ask for: the app connects this computer itself
 *  (`auto-connect.ts`), so the card only reports — one ranked state from the
 *  three status planes, the coding agents it found, and the log for when the
 *  state is not the one wanted. It offers an action only where one exists:
 *  "Try again" or "Connect again" where a connection failed or never came,
 *  "Restart" where the service stopped for good, "Check for updates" where
 *  this copy of Lemma is the problem. Everything else is a stage on its way
 *  up.
 *
 *  `children` is this computer's agent list as the backend published it,
 *  drawn by the page in its own rows so an agent here and one on another
 *  machine are the same object. */
export function ThisComputerCard({ release, children }: { release?: string; children?: ReactNode }) {
    const noun = useThisComputer();
    const { status, error, connectError, retryConnect, refetch, userId } = useAutoConnectThisComputer();
    const [problem, setProblem] = useState<string | null>(null);
    const [attempt, setAttempt] = useState(0);
    const [acting, setActing] = useState(false);
    const hopeful = describeThisComputer(status, error, workspace(), connectError, noun, userId);
    const stage = hopeful.label === "Connecting" || hopeful.label === "Starting" ? `${hopeful.label}:${attempt}` : null;
    const stalled = useStalled(stage);
    const described = stalled
        ? describeThisComputer(status, error, workspace(), connectError, noun, userId, true)
        : hopeful;

    const run = async (work: () => Promise<unknown>, fallback: string) => {
        setProblem(null);
        setActing(true);
        try {
            await work();
        } catch (cause) {
            setProblem(cause instanceof Error ? plainHostError(cause.message, noun) : fallback);
        } finally {
            setActing(false);
        }
    };

    const act = () => {
        setAttempt((was) => was + 1);
        switch (described.action) {
            case "retry":
            case "reconnect":
                retryConnect();
                void refetch();
                return;
            case "restart":
                void run(async () => {
                    await agentHost.start();
                    await refetch();
                }, "Lemma couldn’t restart it.");
                return;
            case "update":
                void run(checkForUpdates, "Lemma couldn’t open its updates.");
                return;
        }
    };

    const actionLabel = described.action === "restart"
        ? acting ? "Restarting…" : "Restart"
        : described.action === "update"
            ? "Check for updates"
            : described.action === "reconnect" || wasRemoved(connectError)
                ? "Connect again"
                : "Try again";

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
                    {described.action && (
                        <button className="btn" disabled={acting} onClick={act}>
                            {described.action === "update" ? <DownloadIcon size={13} /> : <RefreshIcon size={13} />} {actionLabel}
                        </button>
                    )}
                    {status?.available && (
                        <button className="linkish" onClick={() => void run(() => agentHost.openLog(), "The log could not be opened.")}>
                            <TerminalIcon size={13} /> Open log
                        </button>
                    )}
                </div>
                {problem && <p className="thismac__problem" role="alert">{problem}</p>}
            </div>
            {children}
        </section>
    );
}

/** The coding agents this computer found, for Settings → This Mac → Coding
 *  agents: what is installed, which release, and what to type to update it.
 *
 *  Read from the workspace's list of computers, joined on `host_id` — the same
 *  list the Models page draws — so the two places cannot disagree about what
 *  is here. Adding one for teammates to pick stays on Models. */
/** Off by default: the agent starts with Lemma's instructions, skills and
 *  tools only, so what it does in a conversation is what Lemma asked of it.
 *  On, it also loads the person's own -- as in their terminal. */
function OwnSettingsSwitch({ harness, name }: { harness: string; name: string }) {
    const host = useAgentHost();
    const [problem, setProblem] = useState<string | null>(null);
    const change = useMutation({
        mutationFn: (enabled: boolean) => agentHost.setOwnSettings(harness, enabled),
        onSuccess: () => void host.refetch(),
        onError: () => setProblem("Couldn’t change this. Try again, or restart Lemma."),
    });
    const row = ownSettingsRow(host.status, harness);
    const label = `Let ${name} use my own skills and settings`;
    return (
        <>
            <label className="thismac-row__said" title={row.blocked ?? undefined}>
                <input
                    type="checkbox"
                    className="thismac-switch"
                    role="switch"
                    aria-label={label}
                    checked={change.isPending ? change.variables === true : row.checked}
                    disabled={row.blocked !== null || change.isPending}
                    onChange={(event) => { setProblem(null); change.mutate(event.target.checked); }}
                />{" "}
                Use my own skills and settings
                {row.blocked ? " · " + row.blocked
                    : row.checked ? " · Loads what you set up for it, as in Terminal."
                        : " · Uses Lemma’s instructions, skills and tools only."}
            </label>
            {problem && <span className="thismac-said thismac-said--bad" role="alert">{problem}</span>}
        </>
    );
}

export function ThisComputerAgents() {
    const noun = useThisComputer();
    const hostId = useThisHostId();
    const computers = useQuery({
        queryKey: ["computers"],
        queryFn: () => source.listComputers(),
        refetchInterval: 20_000,
    });
    const mine = computers.data?.find((computer) => computer.id === hostId) ?? null;
    if (!hostId || !mine) return null;
    if (stillLooking(mine)) {
        return (
            <p className="mgroup__empty">
                <LoadingIndicator label="Finding coding agents" />
            </p>
        );
    }
    if (mine.agents.length === 0) {
        return (
            <div className="thismac-row">
                <div className="thismac-row__text">
                    <span className="thismac-row__said">
                        No coding agents found on {noun}. Install Claude Code, Codex, Cursor or OpenCode, then press Check again.
                    </span>
                </div>
                <div className="thismac-row__control"><CheckAgainButton /></div>
            </div>
        );
    }
    const unsettled = mine.agents.some((agent) => !agent.ready);
    return (
        <>
            {mine.agents.map((agent) => {
                const update = agentUpdateCommand(agent.harness);
                return (
                    <div className="thismac-row" key={agent.id}>
                        <div className="thismac-row__text">
                            <span className="thismac-row__name">
                                {agent.name}{agent.version ? " " + agent.version : ""}
                            </span>
                            <span className="thismac-row__said">
                                {agent.state}
                                {update ? <> · To update, run <code>{update}</code> in Terminal.</> : null}
                            </span>
                            <OwnSettingsSwitch harness={agent.harness} name={agent.name} />
                        </div>
                    </div>
                );
            })}
            {unsettled && (
                <div className="thismac-row">
                    <div className="thismac-row__text">
                        <span className="thismac-row__said">Signed in or updated one of these? Look again now instead of waiting.</span>
                    </div>
                    <div className="thismac-row__control"><CheckAgainButton /></div>
                </div>
            )}
        </>
    );
}
