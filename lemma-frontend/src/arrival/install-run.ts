"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { lemma } from "@/session/client";
import { captureEvent } from "@/site/analytics/client";
import type { BuiltPlugin } from "@/site/plugins/catalog";
import { sourceOfInstall } from "./install-plan";
import { runOf, type ServerInstall } from "./install-state";
import type { RunKind } from "./stage-model";

export type { ServerInstall } from "./install-state";

/** An app the way in added, for the steps after the one that added it. */
export interface AddedApp {
    slug: string;
    name: string;
    /** The app it put in the Lemma, where they land. */
    app: string | null;
}

/** One picked app, as the way in tells it. */
export interface InstallRow {
    kind: RunKind;
    /** How far along, 0 to 1. */
    progress: number;
    /** The plan step running now, by kind: `APP`, `TABLE`, `FUNCTION`… */
    step: string | null;
    /** The app it put in the Lemma, once in. */
    app: string | null;
    /** Why it stopped, in the server's words. */
    reason: string | null;
}

export interface InstallRun {
    /** Each picked app by slug. */
    rows: Record<string, InstallRow>;
    /** Still going in, somewhere: the server keeps going if the page closes. */
    running: boolean;
    /** The apps in so far, in the order picked. */
    added: AddedApp[];
    /** Every pick is in. */
    done: boolean;
    /** Carry on with the ones that stopped, each from where it stopped. */
    retry: () => void;
    /** For the stage: each pick, how far along. */
    stage: { slug: string; kind: RunKind; progress: number }[];
}

/** The picked apps going in, queued on the server in one request the first
 *  time the way in gets that far. The server works through them in order
 *  whether or not this page is open, and the Lemma tells the person in their
 *  chat with it when they are all settled; this only reads where each one
 *  is. It lives with the way in rather than any one step of it, so the
 *  steps after can show them arriving. */
export function useInstallRun(pod: Pod | null, plugins: readonly BuiltPlugin[], go: boolean): InstallRun {
    const [installId, setInstallId] = useState<string | null>(null);
    const [refused, setRefused] = useState<string | null>(null);
    const started = useRef(false);
    useEffect(() => {
        if (started.current || !go || !pod || plugins.length === 0) return;
        started.current = true;
        const sources = plugins.map((one) => (one.install ? sourceOfInstall(one.install) : null));
        const wanted = sources.filter((one): one is NonNullable<typeof one> => one !== null);
        if (wanted.length === 0) return;
        lemma(pod.id)
            .request<ServerInstall>("POST", `/pods/${encodeURIComponent(pod.id)}/bundle/installs`, {
                body: { plugins: wanted.map((one) => ({ owner: one.owner, repo: one.repo, path: one.path })) },
            })
            .then((made) => {
                setInstallId(made.install_id);
                for (const one of plugins) captureEvent("onboarding.plugin_added", { plugin: one.slug });
            })
            .catch((failure: unknown) => setRefused(failure instanceof Error ? failure.message : "They didn’t start."));
    }, [go, pod, plugins]);

    const status = useQuery({
        queryKey: ["arrival-install", pod?.id, installId],
        queryFn: () => lemma(pod?.id).request<ServerInstall>("GET", `/pods/${encodeURIComponent(pod?.id ?? "")}/bundle/installs/${encodeURIComponent(installId ?? "")}`),
        enabled: Boolean(pod && installId),
        refetchInterval: (query) => (query.state.data?.finished ? false : 2500),
    });

    const retry = useCallback(() => {
        if (!pod || !installId) return;
        void lemma(pod.id)
            .request("POST", `/pods/${encodeURIComponent(pod.id)}/bundle/installs/${encodeURIComponent(installId)}/retry`)
            .then(() => status.refetch());
    }, [pod, installId, status]);

    const rows = runOf(plugins, status.data ?? null, refused);
    const added: AddedApp[] = plugins
        .filter((one) => rows[one.slug]?.kind === "added")
        .map((one) => ({ slug: one.slug, name: one.name, app: rows[one.slug]?.app ?? null }));
    const done = plugins.length > 0 && plugins.every((one) => rows[one.slug]?.kind === "added");
    const running = plugins.some((one) => rows[one.slug]?.kind === "waiting" || rows[one.slug]?.kind === "adding") && !refused;
    const stage = plugins.map((one) => ({ slug: one.slug, kind: rows[one.slug]?.kind ?? "waiting", progress: rows[one.slug]?.progress ?? 0 }));
    return { rows, running, added, done, retry, stage };
}
