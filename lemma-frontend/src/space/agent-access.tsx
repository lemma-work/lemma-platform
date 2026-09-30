"use client";

import { useState } from "react";
import type { Pod } from "@/data";
import { TOOLS, serverSteps, setupCommands, setupPrompt, starterPrompts } from "./agent-access-model";
import { configuredApiUrl } from "@/session/origins";
import { copyText } from "@/desktop/clipboard";
import { CheckIcon, CopyIcon } from "@/ui/icons";

/** Using a space from the coding agent somebody already works in.
 *
 *  Everything here goes through the `lemma` CLI, because that is the path
 *  that works today: it signs in once, keeps its session fresh, and ships the
 *  skills that teach Claude Code or Codex what a space is. The first card is
 *  one prompt that does the whole setup — the agent runs the commands and
 *  you finish sign-in in the browser — so there is a zero-typing way in; the
 *  commands are underneath for anyone who would rather run them. */

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
        </div>
    );
}
