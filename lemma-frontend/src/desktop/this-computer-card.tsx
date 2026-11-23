"use client";

import "@/styles/desktop.css";
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { agentUpdateCommand, source, stillLooking } from "@/data";
import { apiUrl, hasApiUrl } from "@/session/client";
import { LoadingIndicator } from "@/ui/loading";
import { DownloadIcon, RefreshIcon, TerminalIcon } from "@/ui/icons";
import { agentHost, ownSettingsRow, takesOwnSettings, useAgentHost } from "./agent-host";
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
    await invoke("open_control_center", { page: "updates" });
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

/** "Check again", for a row's end: the button, and what went wrong beside
 *  it when the host could not be asked. */
export function CheckAgainButton() {
    const { check, checking, problem } = useCheckAgain();
    return (
        <>
            {problem && <span className="kset-said kset-said--bad" role="alert">{problem}</span>}
            <button type="button" className="b b--s b--secondary" disabled={checking} onClick={() => void check()}>
                <RefreshIcon className={checking ? "spin" : undefined} /> {checking ? "Checking…" : "Check again"}
            </button>
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
 *  drawn by the page in its own rows (`li.krow`) so an agent here and one on
 *  another machine are the same object. The computer is a subheading with
 *  its state at the far end, and its own row comes first in the list. */
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

    const tone = described.tone === "ok" ? "kstatus" : described.tone === "warn" ? "kstatus kstatus--warn" : "kstatus kstatus--off";

    return (
        <section className="kset-stack" aria-label={capitalised(noun)}>
            <p className="ksub">
                {capitalised(noun)}
                {release && <span>Lemma app {release}</span>}
                <span className={tone} role="status">{described.label}</span>
            </p>
            <ul className="klist">
                <li className="krow">
                    <span className="krow__v kset-wrap">
                        <span>{described.detail}</span>
                        {problem && <span className="kset-said--bad" role="alert">{problem}</span>}
                    </span>
                    <span className="krow__end">
                        {status?.available && (
                            <button type="button" className="b b--s b--quiet" onClick={() => void run(() => agentHost.openLog(), "The log could not be opened.")}>
                                <TerminalIcon /> Open log
                            </button>
                        )}
                        {described.action && (
                            <button type="button" className="b b--s b--secondary" disabled={acting} onClick={act}>
                                {described.action === "update" ? <DownloadIcon /> : <RefreshIcon />} {actionLabel}
                            </button>
                        )}
                    </span>
                </li>
                {children}
            </ul>
        </section>
    );
}

/** Claude Code only. Off by default: it starts with Lemma's instructions,
 *  skills and tools only, so what it does in a conversation is what Lemma
 *  asked of it. On, it also loads the person's own -- as in their terminal.
 *  Other agents always load the person's own setup, so nothing is drawn. */
export function OwnSettingsSwitch({ harness, name }: { harness: string; name: string }) {
    if (!takesOwnSettings(harness)) return null;
    return <OwnSettingsToggle harness={harness} name={name} />;
}

function OwnSettingsToggle({ harness, name }: { harness: string; name: string }) {
    const host = useAgentHost();
    const [problem, setProblem] = useState<string | null>(null);
    const change = useMutation({
        mutationFn: (enabled: boolean) => agentHost.setOwnSettings(harness, enabled),
        onSuccess: () => void host.refetch(),
        onError: () => setProblem("Couldn’t change this. Try again, or restart Lemma."),
    });
    const row = ownSettingsRow(host.status, harness);
    const label = `Let ${name} use my own skills and settings`;
    const checked = change.isPending ? change.variables === true : row.checked;
    return (
        <span className="kset-toggle kset-long" title={row.blocked ?? undefined}>
            <button
                type="button"
                className="kswitch"
                role="switch"
                aria-label={label}
                aria-checked={checked}
                disabled={row.blocked !== null || change.isPending}
                onClick={() => { setProblem(null); change.mutate(!checked); }}
            />
            <span>
                Use my own skills and settings
                {row.blocked ? " · " + row.blocked
                    : row.checked ? " · Loads what you set up for it, as in Terminal."
                        : " · Uses Lemma’s instructions, skills and tools only."}
                {problem && <span className="kset-said--bad" role="alert"> {problem}</span>}
            </span>
        </span>
    );
}

/** The coding agents this computer found, for Settings → This Mac → Coding
 *  agents, as rows: what is installed, which release, how it stands, and
 *  what to type to update it.
 *
 *  Read from the workspace's list of computers, joined on `host_id` — the same
 *  list the Models page draws — so the two places cannot disagree about what
 *  is here. Adding one for Lemmas to pick stays on Models. */
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
            <li className="krow">
                <span className="krow__v"><span><LoadingIndicator label="Finding coding agents" /></span></span>
            </li>
        );
    }
    if (mine.agents.length === 0) {
        return (
            <li className="krow">
                <span className="krow__v kset-wrap">
                    <span>No coding agents found on {noun}. Install Claude Code, Codex, Cursor or OpenCode, then check again.</span>
                </span>
                <span className="krow__end"><CheckAgainButton /></span>
            </li>
        );
    }
    const unsettled = mine.agents.some((agent) => !agent.ready);
    return (
        <>
            {mine.agents.map((agent) => {
                const update = agentUpdateCommand(agent.harness);
                return (
                    <li className="krow" key={agent.id}>
                        <span className="krow__v kset-wrap">
                            <b>{agent.name}{agent.version ? " " + agent.version : ""}</b>
                            {update && <span>To update, run <code>{update}</code> in Terminal.</span>}
                            <OwnSettingsSwitch harness={agent.harness} name={agent.name} />
                        </span>
                        <span className="krow__end">
                            <span className={agent.ready ? "kstatus" : "kstatus kstatus--warn"}>{agent.state}</span>
                        </span>
                    </li>
                );
            })}
            {unsettled && (
                <li className="krow">
                    <span className="krow__v kset-wrap">
                        <span>Signed in or updated one of these? Look again now instead of waiting.</span>
                    </span>
                    <span className="krow__end"><CheckAgainButton /></span>
                </li>
            )}
        </>
    );
}
