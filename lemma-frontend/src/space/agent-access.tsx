"use client";

import { useEffect, useState } from "react";
import type { Pod } from "@/data";
import {
    MCP_CLIENTS,
    TOOLS,
    accessLabel,
    disconnectClient,
    fetchMcpUrl,
    loadConnections,
    reachableFromInternet,
    verifiedHost,
    serverSteps,
    setupCommands,
    setupPrompt,
    starterPrompts,
    type ConnectedClient,
} from "./agent-access-model";
import { agoOf } from "@/schedule/schedules";
import { configuredApiUrl } from "@/session/origins";
import { useMe } from "@/session/use-me";
import { copyText } from "@/desktop/clipboard";
import { CheckIcon, CopyIcon } from "@/ui/icons";

/** Using a space from the AI tools somebody already works in.
 *
 *  The link comes first because it is the easiest way in: copy it, paste it
 *  into Claude or ChatGPT, allow Lemma — no install, no terminal. Each
 *  connection asks the person first, acts as them, and is listed right below
 *  to be disconnected.
 *
 *  The `lemma` CLI is folded underneath. It is still the fuller path for a
 *  coding agent — it ships the skills that teach Codex or Cursor what a space
 *  is — so it keeps its one-prompt setup, where the agent runs the commands
 *  and the person only finishes sign-in in the browser. */

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <button className="access__copy" onClick={() => {
            copyText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); }).catch(() => undefined);
        }}>
            {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />} {copied ? "Copied" : label}
        </button>
    );
}

export function AgentAccess({ pod }: { pod: Pod }) {
    const [toolId, setToolId] = useState("claude");
    const tool = TOOLS.find(entry => entry.id === toolId) ?? TOOLS[0];
    const servers = serverSteps(configuredApiUrl(), typeof window === "undefined" ? "" : window.location.origin);
    const setup = setupPrompt(pod, tool, servers);
    const commands = setupCommands(pod, tool, servers).join("\n");

    return (
        <div className="access">
            <McpAccess pod={pod} />

            <details className="access__manual">
                <summary>Use the Lemma CLI instead — for Codex, Cursor and OpenCode</summary>
                <div className="access__tools" role="tablist" aria-label="Coding agent">
                    {TOOLS.map(entry => (
                        <button key={entry.id} role="tab" aria-selected={entry.id === tool.id} onClick={() => setToolId(entry.id)}>{entry.label}</button>
                    ))}
                </div>

                <div className="access__card">
                    <div className="access__head">
                        <span>
                            <b>Set up</b>
                            <small>Paste this into {tool.label}. It installs the Lemma CLI and connects it to {pod.name}; you finish sign-in in the browser.</small>
                        </span>
                        <CopyButton text={setup} label="Copy prompt" />
                    </div>
                    <pre className="access__text">{setup}</pre>
                    <details className="access__manual">
                        <summary>Or run the commands yourself</summary>
                        <div className="access__head">
                            <small>In a terminal, in the folder you work in.</small>
                            <CopyButton text={commands} />
                        </div>
                        <pre className="access__text access__text--code">{commands}</pre>
                    </details>
                </div>

                <div className="access__label">Then ask it</div>
                <ul className="access__prompts">
                    {starterPrompts(pod).map(item => (
                        <li key={item.title}>
                            <span>
                                <b>{item.title}</b>
                                <small>{item.prompt}</small>
                            </span>
                            <span className="access__actions">
                                <CopyButton text={item.prompt} />
                                {tool.launch && <CopyButton text={tool.launch(item.prompt)} label="Command" />}
                            </span>
                        </li>
                    ))}
                </ul>
            </details>
        </div>
    );
}

function McpAccess({ pod }: { pod: Pod }) {
    const apiUrl = configuredApiUrl();
    const [clientId, setClientId] = useState("claude");
    /* Undefined while asking, null when this deployment does not serve spaces
       over MCP — then the section is not shown at all. A request that failed
       is neither: it says so, with a way to try again. */
    const [url, setUrl] = useState<string | null | undefined>(undefined);
    const [urlProblem, setUrlProblem] = useState<string | null>(null);
    const [attempt, setAttempt] = useState(0);
    const [connected, setConnected] = useState<ConnectedClient[] | null>(null);
    const [everyone, setEveryone] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    /* Someone else's connection, asked about once before it is ended. */
    const [confirming, setConfirming] = useState<string | null>(null);
    const me = useMe();

    useEffect(() => {
        if (!apiUrl) return;
        let cancelled = false;
        void (async () => {
            let stated: string | null;
            try {
                stated = await fetchMcpUrl(apiUrl, pod.id);
            } catch (error) {
                if (!cancelled) setUrlProblem(error instanceof Error ? error.message : "The link could not be loaded.");
                return;
            }
            if (cancelled) return;
            setUrlProblem(null);
            setUrl(stated);
            if (!stated) return;
            try {
                const loaded = await loadConnections(apiUrl, pod.id);
                if (cancelled) return;
                setConnected(loaded.items);
                setEveryone(loaded.everyone);
            } catch (error) {
                if (!cancelled) setProblem(error instanceof Error ? error.message : null);
            }
        })();
        return () => { cancelled = true; };
    }, [apiUrl, pod.id, attempt]);

    if (apiUrl && urlProblem) {
        return (
            <div className="access__card">
                <div className="access__head">
                    <span>
                        <b>Connect with a link</b>
                        <small role="alert">{urlProblem}</small>
                    </span>
                    <button className="access__copy" onClick={() => { setUrlProblem(null); setAttempt(count => count + 1); }}>Try again</button>
                </div>
            </div>
        );
    }
    if (!apiUrl || !url) return null;
    const client = MCP_CLIENTS.find(entry => entry.id === clientId) ?? MCP_CLIENTS[0];
    const command = client.command?.(url, pod) ?? null;
    const outOfReach = client.remote && !reachableFromInternet(url);
    const whoConnected = (userId: string) =>
        pod.members.find(member => member.userId === userId)?.name ?? "a former member";

    const disconnect = async (grantId: string) => {
        setConfirming(null);
        try {
            await disconnectClient(apiUrl, grantId);
            // Read back rather than dropped locally: a 404 does not prove the
            // connection is gone, and a row removed here would return on reload.
            const loaded = await loadConnections(apiUrl, pod.id);
            setConnected(loaded.items);
            setEveryone(loaded.everyone);
            setProblem(null);
        } catch (error) {
            setProblem(error instanceof Error ? error.message : null);
        }
    };

    return (
        <>
            <div className="access__card">
                <div className="access__head">
                    <span>
                        <b>Connect with a link</b>
                        <small>Paste this link into Claude, ChatGPT or any AI tool that supports MCP, and allow Lemma when it asks. It works in {pod.name} as you, and sees only what you can.</small>
                    </span>
                    <CopyButton text={url} label="Copy link" />
                </div>
                <pre className="access__text access__text--code">{url}</pre>
                <div className="access__tools" role="tablist" aria-label="AI tool">
                    {MCP_CLIENTS.map(entry => (
                        <button key={entry.id} role="tab" aria-selected={entry.id === client.id} onClick={() => setClientId(entry.id)}>{entry.label}</button>
                    ))}
                </div>
                {outOfReach ? (
                    <div className="access__head">
                        <small>{client.label} connects from its own servers, and this Lemma is only reachable from this computer. Use Claude Code, or run Lemma where the internet can reach it.</small>
                    </div>
                ) : (
                    <>
                        {command && (
                            <div className="access__head">
                                <small>In a terminal:</small>
                                <CopyButton text={command} label="Copy command" />
                            </div>
                        )}
                        {command && <pre className="access__text access__text--code">{command}</pre>}
                        <pre className="access__text">{client.steps(url, pod).map((step, index) => (index + 1) + ". " + step).join("\n")}</pre>
                    </>
                )}
            </div>

            <div className="access__label">{everyone ? "Connected to " + pod.name : "Connected by you"}</div>
            {problem && <div className="access__head"><small role="alert">{problem}</small></div>}
            {connected !== null && connected.length === 0 && (
                <div className="access__head"><small>Nothing is connected to {pod.name} yet.</small></div>
            )}
            {connected !== null && connected.length > 0 && (
                <ul className="access__prompts">
                    {connected.map(item => {
                        const host = verifiedHost(item);
                        const someoneElses = me !== null && item.user_id !== me;
                        const asking = confirming === item.grant_id;
                        return (
                            <li key={item.grant_id}>
                                <span>
                                    <b>{item.client_name}</b>
                                    <small>
                                        {host ? "from " + host : "unverified app"} ·{" "}
                                        {everyone ? "by " + whoConnected(item.user_id) + " · " : ""}
                                        {accessLabel(item.scopes)} · {item.last_used_at ? "used " + agoOf(item.last_used_at) : "connected " + agoOf(item.connected_at)}
                                    </small>
                                </span>
                                <span className="access__actions">
                                    {asking ? (
                                        <>
                                            <small>End it for {whoConnected(item.user_id)}?</small>
                                            <button className="access__copy" onClick={() => void disconnect(item.grant_id)}>Disconnect</button>
                                            <button className="access__copy" onClick={() => setConfirming(null)}>Keep</button>
                                        </>
                                    ) : (
                                        <button
                                            className="access__copy"
                                            onClick={() => someoneElses ? setConfirming(item.grant_id) : void disconnect(item.grant_id)}
                                        >
                                            Disconnect
                                        </button>
                                    )}
                                </span>
                            </li>
                        );
                    })}
                </ul>
            )}
        </>
    );
}
