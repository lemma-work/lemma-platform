"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { Pod } from "@/data";
import { stepWords } from "@/arrival/install-state";
import { addHref } from "@/arrival/suggest";
import { getBuilt } from "@/site/plugins/catalog";
import { TeammateFace } from "@/space/teammate-face";
import { AppMark } from "./app-mark";
import { useAddPlugin, type AddRow } from "./installs";
import { outcomeView, type Go } from "./model";
import { to } from "./parts";

/** Apps on their way into any of the reader's Lemmas, one row each: the
 *  app's mark in a ring that fills as it goes in, the step under way in the
 *  install's own words, and how far along. One that stopped says so in red,
 *  with its reason, and Try again -- the Add page's own retry. When one is
 *  in it leaves, and `onLanded` names it so its card can light once.
 *
 *  Installs are kept per Lemma, so each Lemma reads its own; the list is
 *  hidden while none of them has anything going in. */
export function GoingIn({ pods, go, onLanded }: {
    pods: readonly Pod[];
    go: Go;
    onLanded: (podId: string, app: string) => void;
}) {
    return (
        <ul className="appsp-going" aria-label="Going in">
            {pods.map((pod) => <GoingFor key={pod.id} pod={pod} go={go} onLanded={onLanded} />)}
        </ul>
    );
}

function GoingFor({ pod, go, onLanded }: { pod: Pod; go: Go; onLanded: (podId: string, app: string) => void }) {
    const { rows, retry } = useAddPlugin(pod.id);
    const list = [...rows.values()];
    /* What each install was when last seen here, so only one seen to land
       while the page is open lights its card -- not one that went in before
       the reader came. */
    const was = useRef(new Map<string, AddRow["kind"]>());
    const kinds = list.map((row) => `${row.slug}:${row.kind}:${row.app ?? ""}`).join(",");
    useEffect(() => {
        for (const row of rows.values()) {
            const before = was.current.get(row.slug);
            if (row.kind === "added" && before && before !== "added" && row.app) onLanded(pod.id, row.app);
            was.current.set(row.slug, row.kind);
        }
        /* `rows` is read through `kinds`, which changes when it does. */
    }, [kinds, pod.id, onLanded]);
    return list
        .filter((row) => row.kind !== "added")
        .map((row) => <GoingRow key={row.slug} pod={pod} row={row} go={go} onRetry={() => retry(row.slug)} />);
}

function GoingRow({ pod, row, go, onRetry }: { pod: Pod; row: AddRow; go: Go; onRetry: () => Promise<void> }) {
    const [retrying, setRetrying] = useState(false);
    const state = row.kind === "failed" ? "stopped" : row.kind === "asks" ? "asks" : "going";
    const progress = row.kind === "adding" ? Math.max(0, Math.min(1, row.progress)) : 0;

    let said: ReactNode;
    let end: ReactNode;
    if (state === "stopped") {
        said = <span className="appsp-go__s appsp-go__s--bad">Stopped before it was in · {pod.name}</span>;
        end = (
            <button
                type="button"
                className="b b--s b--primary"
                disabled={retrying}
                onClick={() => {
                    setRetrying(true);
                    void onRetry().finally(() => setRetrying(false));
                }}
            >
                {retrying ? "Trying…" : "Try again"}
            </button>
        );
    } else if (state === "asks") {
        /* It waits on an answer only its own page can take. */
        const plugin = getBuilt(row.slug);
        const href = plugin ? addHref(plugin) : null;
        said = <span className="appsp-go__s appsp-go__s--warn">Waiting on an answer · {pod.name}</span>;
        end = href
            ? <a className="b b--s b--secondary" href={href}>Answer on its page</a>
            : <a className="b b--s b--secondary" {...to(outcomeView(pod.id, "add"), go)}>See it</a>;
    } else {
        said = (
            <span className="appsp-go__s">
                Going into <TeammateFace pod={pod} size={16} className="appsp-go__face" />
                <span className="appsp-go__l">{pod.name}</span>
                <span className="appsp-go__step">· {stepWords(row.kind === "adding" ? row.step : null)}</span>
            </span>
        );
        end = <span className="appsp-go__pct ox-num">{row.kind === "adding" ? `${Math.round(progress * 100)}%` : "Next"}</span>;
    }

    return (
        <li className="appsp-go" data-state={state}>
            <span className="appsp-ring" style={{ "--p": progress } as CSSProperties} aria-hidden="true">
                <AppMark name={row.slug} label={row.name} size={32} />
            </span>
            <span className="appsp-go__v">
                <span className="appsp-go__n">{row.name}</span>
                {said}
                {state !== "going" && row.reason && <span className="appsp-go__why">{row.reason}</span>}
            </span>
            <span className="appsp-go__end">{end}</span>
        </li>
    );
}
