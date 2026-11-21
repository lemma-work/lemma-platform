"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { sourceOfInstall } from "@/arrival/install-plan";
import { BUILT, listed, type BuiltPlugin } from "@/site/plugins/catalog";
import { TeammateFace } from "@/space/teammate-face";
import { SkeletonCards } from "@/ui/skeleton";
import { pointAt } from "./add-point";
import { AppMark } from "./app-mark";
import { AppCard, appNameOf, cardKey } from "./apps-card";
import { GoingIn } from "./apps-going";
import { useApps, type PodApp } from "./ask";
import { Calm, Problem } from "./calm";
import { outcomeView, type Go } from "./model";
import { PageHead } from "./page-head";
import { Icon, to } from "./parts";

/** How many ready-made apps sit under the list. */
const READY_SHOWN = 4;

function count(n: number, one: string): string {
    return `${n} ${one}${n === 1 ? "" : "s"}`;
}

/** The line under the title: how many apps, across whose Lemmas -- or,
 *  before that is known, what the page holds. Only what is true: a count
 *  once the apps are read, and none while they are not. */
function headLine(pods: readonly Pod[], orgName: string | null, known: boolean, apps: number): string {
    const only = pods.length === 1 ? pods[0] : null;
    const whose = orgName ? `${orgName}’s ${count(pods.length, "Lemma")}` : `your ${count(pods.length, "Lemma")}`;
    if (pods.length === 0) return "Every app across your Lemmas.";
    if (!known) return only ? `Every app in ${only.name}, newest first.` : `Every app across ${whose}, newest first.`;
    if (apps === 0) return `What people use ${only ? only.name : whose} through. None yet.`;
    const order = apps > 1 ? ", newest first" : "";
    return only ? `${count(apps, "app")} in ${only.name}${order}.` : `${count(apps, "app")} across ${whose}${order}.`;
}

/** The ready-made apps to offer: built plugins that can be added, and that
 *  no Lemma of the reader's already has, in the catalog's own order. */
function readyMade(apps: readonly PodApp[]): BuiltPlugin[] {
    const names = new Set(apps.map(({ app }) => appNameOf(app)));
    return BUILT
        .filter((plugin) => plugin.install && sourceOfInstall(plugin.install))
        .filter((plugin) => !names.has(plugin.slug) && !names.has(plugin.folder))
        .slice(0, READY_SHOWN);
}

/** Every app across the reader's Lemmas, newest first: what people use
 *  their work through, in one place in the sidebar rather than on every
 *  Lemma's page. Over them, the apps still going in, each in its ring; then
 *  filters by Lemma and a search; under them, a few ready-made ones.
 *  Adding opens the first Lemma's Add an app page, whose picker moves it to
 *  any other. */
export function AppsPage({ pods, go }: { pods: readonly Pod[]; go: Go }) {
    const { apps, pending, failed, retry, retrying } = useApps(pods);
    const orgs = useQuery({ queryKey: ["orgs"], queryFn: () => source.listOrgs(), staleTime: 10 * 60_000 });
    const orgName = orgs.data?.find((one) => one.id === pods[0]?.orgId)?.name ?? null;
    const first = pods[0] ?? null;
    const addView = first ? outcomeView(first.id, "add") : null;

    const [only, setOnly] = useState<string | null>(null);
    const [query, setQuery] = useState("");
    const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
    const landed = useCallback((podId: string, app: string) => {
        setFresh((was) => new Set(was).add(cardKey(podId, app)));
    }, []);
    const seen = (key: string) => setFresh((was) => {
        const next = new Set(was);
        next.delete(key);
        return next;
    });

    /* The Lemmas that have apps, in the sidebar's order, each with how many. */
    const holding = useMemo(() => {
        const counts = new Map<string, number>();
        for (const { pod } of apps) counts.set(pod.id, (counts.get(pod.id) ?? 0) + 1);
        return pods.flatMap((pod) => (counts.has(pod.id) ? [{ pod, n: counts.get(pod.id) ?? 0 }] : []));
    }, [apps, pods]);
    const lemma = holding.some(({ pod }) => pod.id === only) ? only : null;
    const words = query.trim().toLowerCase();
    const shown = apps.filter(({ pod, app }) => (!lemma || pod.id === lemma)
        && (!words || app.label.toLowerCase().includes(words) || appNameOf(app).toLowerCase().includes(words)));

    const loading = pending && apps.length === 0;
    const allFailed = !pending && apps.length === 0 && failed.length > 0;
    const empty = !pending && apps.length === 0 && failed.length === 0;
    const known = !loading && failed.length === 0;
    const ready = useMemo(() => readyMade(apps), [apps]);
    const addLink = addView ? to(addView, go) : null;
    /* "Add" on a ready-made app: the Add page, scrolled to that plugin. */
    const pointed = (slug: string) => {
        const link = to(addView ?? { name: "apps" }, go);
        return { ...link, onClick: (event: Parameters<typeof link.onClick>[0]) => { pointAt(slug); link.onClick(event); } };
    };

    return (
        <div className="ox-page appsp">
            <PageHead title="Apps" line={headLine(pods, orgName, known || apps.length > 0, apps.length)}>
                {addLink && <a className="b b--m b--primary" {...addLink}><Icon name="plus" size={15} />Add an app</a>}
            </PageHead>

            <div className="appsp-body">
                <GoingIn pods={pods} go={go} onLanded={landed} />

                {loading ? (
                    <SkeletonCards label="Reading the apps" slow="Still reading the apps. It’s slower than usual." />
                ) : allFailed ? (
                    <Calm
                        tone="warn"
                        title={failed.length < pods.length ? `Couldn’t read the apps in ${listed(failed.map((pod) => pod.name))}` : "Couldn’t read the apps"}
                        actions={<button type="button" className="b b--m b--primary" disabled={retrying} onClick={retry}><Icon name="refresh" size={15} />{retrying ? "Trying…" : "Try again"}</button>}
                    >
                        <p>Nothing in them changed.</p>
                    </Calm>
                ) : empty ? (
                    <Calm
                        title="No apps yet"
                        art={(
                            <span className="appsp-tiles" aria-hidden="true">
                                <AppMark name="inbox" size={52} />
                                <AppMark name="deals" size={52} />
                                <AppMark name="people" size={52} />
                            </span>
                        )}
                        actions={addLink && <a className="b b--m b--primary" {...addLink}>Describe your own</a>}
                    >
                        <p>An app is how people use a Lemma’s work: an inbox it sorts, a tracker it keeps. Start from a ready-made one, or describe your own and a Lemma builds it.</p>
                    </Calm>
                ) : (
                    <>
                        {failed.length > 0 && (
                            <Problem onRetry={retry} retrying={retrying}>Couldn’t read the apps in {listed(failed.map((pod) => pod.name))}.</Problem>
                        )}
                        <div className="ph__tools appsp-tools">
                            <div className="appsp-chips" role="group" aria-label="Whose apps">
                                <button type="button" className="kchip" aria-pressed={lemma === null} onClick={() => setOnly(null)}>
                                    All <span>{apps.length}</span>
                                </button>
                                {holding.length > 1 && holding.map(({ pod, n }) => (
                                    <button key={pod.id} type="button" className="kchip appsp-chip" aria-pressed={lemma === pod.id} onClick={() => setOnly(lemma === pod.id ? null : pod.id)}>
                                        <TeammateFace pod={pod} size={18} />{pod.name} <span className="appsp-chip__n">{n}</span>
                                    </button>
                                ))}
                            </div>
                            <span className="ph__grow" />
                            <label className="ksearch">
                                <Icon name="search" size={15} />
                                <span className="ox-sr">Find an app</span>
                                <input type="search" value={query} placeholder="Find an app" onChange={(event) => setQuery(event.target.value)} />
                            </label>
                        </div>
                        {shown.length > 0 ? (
                            <ul className="appsp-grid">
                                {shown.map(({ pod, app }) => {
                                    const key = cardKey(pod.id, appNameOf(app));
                                    return <AppCard key={key} pod={pod} app={app} go={go} fresh={fresh.has(key)} onSeen={() => seen(key)} />;
                                })}
                            </ul>
                        ) : (
                            <p className="appsp-none">
                                No app is called anything like “{query.trim()}”.
                            </p>
                        )}
                    </>
                )}

                {addView && !loading && !allFailed && ready.length > 0 && (
                    <section className="appsp-ready" aria-labelledby="appsp-ready-t">
                        <div className="appsp-ready__head">
                            <h2 id="appsp-ready-t" className="ksub">Ready-made <span>Add one to any Lemma, then change it to fit</span></h2>
                            <a className="b b--s b--quiet" {...to(addView, go)}>See all<Icon name="next" size={15} /></a>
                        </div>
                        <ul className="appsp-ready__grid">
                            {ready.map((plugin) => (
                                <li key={plugin.slug} className="appsp-rcard">
                                    <AppMark name={plugin.slug} label={plugin.name} size={36} className="appsp-rcard__mk" />
                                    <span className="appsp-rcard__n">{plugin.name}</span>
                                    <span className="appsp-rcard__s">{plugin.job}</span>
                                    <a className="b b--s b--secondary appsp-rcard__add" aria-label={`Add ${plugin.name}`} {...pointed(plugin.slug)}>
                                        <Icon name="plus" size={15} />Add
                                    </a>
                                </li>
                            ))}
                        </ul>
                    </section>
                )}
            </div>
        </div>
    );
}
