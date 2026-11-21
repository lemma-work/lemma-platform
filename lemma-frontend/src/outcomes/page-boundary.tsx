"use client";

import { Component, useEffect, useState, type ReactNode } from "react";
import { captureClientError } from "@/site/analytics/client";
import { Calm } from "./calm";
import { Icon } from "./parts";

/** Something in a page failed while drawing (design.md, "States").
 *
 *  Inside the frame, around the page and not the sidebar: a page that throws
 *  takes itself down and nothing else, so the reader can still go anywhere,
 *  and moving to another address tries again. Without it a throw anywhere
 *  reached the root `error.tsx`, which replaces the whole frame.
 *
 *  React reports what a boundary caught to the console, not to the window, so
 *  error tracking hears of it here or never. */
export class PageBoundary extends Component<
    { at: string; children: ReactNode },
    { error: unknown; failedAt: string | null; when: number }
> {
    state = { error: null as unknown, failedAt: null as string | null, when: 0 };

    static getDerivedStateFromError(error: unknown) {
        return { error: error ?? new Error("Something was thrown"), failedAt: null, when: Date.now() };
    }

    /* Which address broke, written down on the draw after the throw; any
       other address draws the page again. */
    static getDerivedStateFromProps(props: { at: string }, state: { error: unknown; failedAt: string | null }) {
        if (state.error === null) return null;
        if (state.failedAt === null) return { failedAt: props.at };
        if (state.failedAt !== props.at) return { error: null, failedAt: null };
        return null;
    }

    componentDidCatch(error: unknown) {
        captureClientError(error);
    }

    render() {
        if (this.state.error === null) return this.props.children;
        return (
            <div className="ox-page">
                <PageBroke error={this.state.error} when={this.state.when} />
            </div>
        );
    }
}

/** What a page that broke says: that the work is safe, and the two things
 *  worth doing -- copy what happened for whoever looks into it, and reload.
 *  Also the root `error.tsx`, so a page that broke looks the same wherever it
 *  happened. */
export function PageBroke({ error, when, digest }: { error: unknown; when?: number; digest?: string }) {
    const [at] = useState(() => when ?? Date.now());
    const reference = digest ?? (error instanceof Error ? (error as Error & { digest?: string }).digest : undefined);
    const [copied, setCopied] = useState<"no" | "yes" | "failed">("no");
    useEffect(() => {
        if (copied === "no") return;
        const timer = window.setTimeout(() => setCopied("no"), 2400);
        return () => window.clearTimeout(timer);
    }, [copied]);

    const copy = () => {
        const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
        const details = [
            "This page hit a problem",
            "Message: " + message,
            reference ? "Reference: " + reference : null,
            "Address: " + window.location.href,
            "Time: " + new Date(at).toISOString(),
        ].filter(Boolean).join("\n");
        navigator.clipboard.writeText(details).then(() => setCopied("yes"), () => setCopied("failed"));
    };
    const time = new Date(at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

    return (
        <Calm
            tone="warn"
            title="This page hit a problem"
            actions={(
                <>
                    <button type="button" className="b b--m b--secondary" onClick={copy}>
                        {copied === "yes" ? <><Icon name="check" size={16} />Copied</> : copied === "failed" ? "Couldn’t copy" : "Copy details"}
                    </button>
                    <button type="button" className="b b--m b--primary" onClick={() => window.location.reload()}>
                        <Icon name="refresh" size={16} />Reload
                    </button>
                </>
            )}
            note={<span suppressHydrationWarning>{reference ? `Error ${reference} · ` : ""}{time}</span>}
        >
            <p>Something in it failed while drawing. Reloading usually fixes it. Your Lemmas and their work are on the server and weren’t touched.</p>
        </Calm>
    );
}
