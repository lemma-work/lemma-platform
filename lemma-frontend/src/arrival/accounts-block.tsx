"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { CONNECTOR_USE } from "@/data/agents";
import { askableOfMe, missingForMe } from "@/data/pod-connectors";
import { asMine, useConnectFlow, usePodMay } from "@/connect/pod-connect";
import { SignInLink } from "@/connect/sign-in-link";
import { useMe } from "@/session/use-me";
import { listed } from "@/site/plugins/catalog";
import { LoadingIndicator } from "@/ui/loading";
import { TickIcon } from "./shell";
import { accountRows } from "./accounts";

/** The accounts the added apps read, each connected once for the whole
 *  Lemma, through the same connect flow as the line over a Lemma that needs
 *  an account. Shown while the apps go in -- something useful to do while
 *  waiting -- and after. Nothing to connect, it draws nothing.
 *
 *  `watch` reads the Lemma's connectors again every few seconds: they appear
 *  part way through an install, as one of its first steps. */
export function AccountsBlock({ pod, added, heading, watch = false }: {
    pod: Pod;
    added: readonly { slug: string; name: string }[];
    heading: string;
    watch?: boolean;
}) {
    const me = useMe() ?? "";
    const connectors = useQuery({ queryKey: ["pod-connectors", pod.id], queryFn: () => source.listPodConnectors(pod.id), refetchInterval: watch ? 4000 : false });
    const list = connectors.data ?? [];
    const unsaid = list.some((one) => one.required && one.mode === "PER_MEMBER" && one.canUse === null);
    const podUse = usePodMay(pod.id, CONNECTOR_USE, unsaid);
    const podMayUse = podUse.answered ? podUse.allowed : null;
    const asked = askableOfMe(list, podMayUse);
    const accounts = useQuery({ queryKey: ["accounts", pod.orgId], queryFn: () => source.listAccounts(pod.orgId), enabled: asked.length > 0 });
    const mine = useMemo(() => asMine(accounts.data ?? []), [accounts.data]);
    const missing = accounts.isSuccess ? missingForMe(asked, mine, me, podMayUse) : [];
    const catalog = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors(), staleTime: 10 * 60_000, enabled: asked.length > 0 });
    const installs = useQuery({ queryKey: ["installs", pod.orgId], queryFn: () => source.listInstallations(pod.orgId), staleTime: 60_000, retry: false, enabled: asked.length > 0 });
    const titles = useMemo(() => new Map((catalog.data ?? []).map((one) => [one.id, one])), [catalog.data]);
    const flow = useConnectFlow(pod, titles, installs.data);
    const titleOf = (id: string) => titles.get(id)?.title ?? "";

    const missingSlugs = new Set(missing.map((one) => one.connector.slug));
    const reconnect = new Set(missing.filter((one) => one.status.standing === "reconnect").map((one) => one.connector.slug));
    const rows = accountRows(asked, missingSlugs, added, titleOf);
    if (rows.length === 0 || !catalog.isSuccess) return null;
    const open = rows.filter((row) => !row.done);

    return (
        <section className="ara" aria-label="Accounts">
            <p className="ara__h">{open.length === 0 ? "Your accounts are connected" : heading}</p>
            <ul className="ar__rows ar__rows--boxed">
                {rows.map((row) => (
                    <li key={row.called} className="ar__row">
                        <span className="ar__said">
                            <span className="ar__name">{row.called}</span>
                            <span className="ar__line">{[row.forApps.length > 0 ? `For ${listed(row.forApps)}` : null, row.why].filter(Boolean).join(". ")}</span>
                            {flow.problem && row.connect.some((one) => one.slug === flow.problem?.slug) && <span className="ar__problem" role="alert">{flow.problem.text}</span>}
                            {flow.link && row.connect.some((one) => one.slug === flow.link?.slug) && <SignInLink url={flow.link.url} app={flow.link.app} opened={flow.link.opened} />}
                        </span>
                        {row.done ? (
                            <span className="ar__done"><TickIcon />Connected</span>
                        ) : (
                            <span className="ar__either">
                                {row.connect.map((one, index) => (
                                    <button key={one.slug} type="button" className={"ar__btn ar__btn--small" + (index === 0 ? " ar__btn--primary" : "")} disabled={flow.pending} onClick={() => flow.connect(one, reconnect.has(one.slug))}>
                                        {flow.busy === one.slug ? <LoadingIndicator inline label="Opening sign-in" /> : `${reconnect.has(one.slug) ? "Reconnect" : index === 0 ? "Connect" : "Use"} ${titleOf(one.connectorId) || one.slug}`}
                                    </button>
                                ))}
                            </span>
                        )}
                    </li>
                ))}
            </ul>
            {flow.waiting && <p className="ar__note" role="status">Finish signing in in the other tab. This page notices when you’re back.</p>}
            {flow.element}
        </section>
    );
}
