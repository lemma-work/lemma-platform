"use client";

import { useState } from "react";
import { Transcript } from "@/thread/transcript";
import { Composer } from "@/thread/composer";
import { WorkspaceLoading } from "@/shell/workspace-loading";
import { buildTurns } from "@/thread/turns";

const turns = buildTurns([
    { id: "q", role: "user", kind: "TEXT", text: "Can you help me prepare the launch update?", sequence: 1, created_at: "2026-01-01T10:00:00Z" },
    { id: "a", role: "assistant", kind: "TEXT", text: "I’ll pull together the latest changes and draft an update for your review.", sequence: 2, created_at: "2026-01-01T10:00:01Z" },
]);

const states = { loading: "Loading", ready: "Messages", empty: "New conversation", error: "Load failed", workspace: "Workspace" };
export function LoadingPreview() {
    const [state, setState] = useState<keyof typeof states>("loading");
    return (
        <main className="convo-host" style={{ height: "100dvh", maxWidth: 800, margin: "auto", padding: "0 20px" }}>
            <header style={{ padding: "24px 0", borderBottom: "1px solid var(--line-2)" }}>
                <h1 style={{ fontSize: 18, marginBottom: 16 }}>Conversation loading</h1>
                <nav aria-label="Preview state" style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                    {(Object.keys(states) as (keyof typeof states)[]).map(value => <button className="btn" key={value} aria-pressed={state === value} onClick={() => setState(value)}>{states[value]}</button>)}
                </nav>
            </header>
            {state === "workspace" ? <div style={{ flex: 1, minHeight: 0 }}><WorkspaceLoading embedded /></div> : <>
                <Transcript turns={state === "ready" ? turns : []} teammate={{ name: "Kit", initials: "K", iconUrl: null }} streaming={null} state="idle" error={state === "error" ? "Could not load this conversation. Please try again." : null} loading={state === "loading"} emptyTitle="New conversation" emptyBody="Kit is ready. Send a message to start." podId="preview" onReload={state === "error" ? () => setState("loading") : undefined} />
                <Composer placeholder="Talk to Kit…" busy={state !== "empty" && state !== "ready"} canStop={false} onSend={() => {}} />
            </>}
        </main>
    );
}
