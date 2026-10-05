"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
    /* A failed disconnect. A failed read of the list is the query's own. */
    const [problem, setProblem] = useState<string | null>(null);
    /* Someone else's connection, asked about once before it is ended. */
    const [confirming, setConfirming] = useState<string | null>(null);
    const me = useMe();
    const cache = useQueryClient();

    /* Queries rather than an effect, so opening this tab again reads the
       cache instead of asking twice more. Neither changes behind the
       person's back often enough to need more than five minutes: the link is
       a deployment setting, and the list changes when somebody connects an
       app — which this page cannot see happen anyway — or disconnects one
       here, which reads it back. No retries: a failure is said, with a way
       to try again, as it always was.

       The link is undefined while asking, null when this deployment does
       not serve spaces over MCP — then the section is not shown at all. A
       request that failed is neither: it says so, with a way to try again. */
    const endpoint = useQuery({
        queryKey: ["mcp-endpoint", pod.id],
        queryFn: () => fetchMcpUrl(apiUrl as string, pod.id),
        enabled: Boolean(apiUrl),
        staleTime: 5 * 60_000,
        retry: false,
    });
    const url = endpoint.data;
    const urlProblem = endpoint.isError
        ? (endpoint.error instanceof Error ? endpoint.error.message : "The link could not be loaded.")
        : null;
    /* `loadConnections` keeps its own fallback: everyone's connections, or —
       on a 403 — just the viewer's, and says which it got. */
    const grants = useQuery({
        queryKey: ["mcp-grants", pod.id],
        queryFn: () => loadConnections(apiUrl as string, pod.id),
        enabled: Boolean(apiUrl && url),
        staleTime: 5 * 60_000,
        retry: false,
    });
    const connected: ConnectedClient[] | null = grants.data?.items ?? null;
    const everyone = grants.data?.everyone ?? false;
    const shownProblem = problem ?? (grants.isError && grants.error instanceof Error ? grants.error.message : null);

    if (apiUrl && urlProblem) {
        return (
            <div className="access__card">
                <div className="access__head">
                    <span>
                        <b>Connect with a link</b>
                        <small role="alert">{urlProblem}</small>
                    </span>
                    <button className="access__copy" disabled={endpoint.isFetching} onClick={() => void endpoint.refetch()}>Try again</button>
                </div>
            </div>
        );
    }
    if (!apiUrl || !url) return null;
    const client = MCP_CLIENTS.find(entry => entry.id === clientId) ?? MCP_CLIENTS[0];
    const command = client.command?.(url, pod) ?? null;
    const outOfReach = client.remote && !reachableFromInternet(url);
    /* The viewer first: a space's member list is who else is in it, so the
       person reading this would otherwise be "a former member" on their own
       connections. */
    const whoConnected = (userId: string) =>
        userId === me ? "you"
            : pod.members.find(member => member.userId === userId)?.name ?? "a former member";

    const disconnect = async (grantId: string) => {
        setConfirming(null);
        try {
            await disconnectClient(apiUrl, grantId);
            // Read back rather than dropped locally: a 404 does not prove the
            // connection is gone, and a row removed here would return on reload.
            setProblem(null);
            await cache.invalidateQueries({ queryKey: ["mcp-grants", pod.id] });
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
            {shownProblem && <div className="access__head"><small role="alert">{shownProblem}</small></div>}
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
