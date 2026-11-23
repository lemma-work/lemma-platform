"use client";

import { useMemo, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { accountName, source, type Connector, type Org, type Pod } from "@/data";
import { AddConnector, LOOKS } from "@/connect/add-connector";
import { isBringYourOwn, ownAppKinds, ownKinds, type Install } from "@/connect/install";
import { NeedRow } from "@/connect/need-row";
import { SignInNote, useOrgConnect, type OrgConnect } from "@/connect/org-connect";
import { OwnAppPicker } from "@/connect/own-app-picker";
import { useConnectorRefresh, useInstalls, useMayInstall } from "@/connect/queries";
import { outcomeNote, useConnectOutcome, type ConnectOutcome } from "@/connect/round-trip";
import { ServiceMark } from "@/connect/service-mark";
import { ServicePicker } from "@/connect/service-picker";
import {
    connectAgainst, entryMatches, matches, mayConnect, notYetConnected, servesThrough, servicesOf, statusOf, subtitleOf,
    type Service,
} from "@/connect/services";
import { SetUpOnThisMac } from "@/desktop/set-up-on-this-mac";
import { oauthFormForConnector } from "@/desktop/this-mac";
import { TeammateFace } from "@/space/teammate-face";
import { CheckCircleIcon, ChevronRightIcon, KeyIcon, PlusIcon, RefreshIcon, SearchIcon, WarningIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { ConnectorSheet, type PodConnectorsRead } from "./connector-sheet";
import { PageHead } from "./page-head";
import { Calm } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";

/** Which rows are shown: all of them, the ones working, or the ones that
 *  need somebody. Counted on the chips, so the count is the answer. */
type Filter = "all" | "working" | "needs";

/** Catalogue cards shown before "Show all": enough to see the kind of thing
 *  on offer, not a wall of two hundred logos. */
const FIRST_CARDS = 12;

/** The services the organization's Lemmas work through — Gmail, GitHub, a
 *  database of its own — as a place of its own in the sidebar, no longer a
 *  section of Settings.
 *
 *  It answers three questions in the order a person has them. What needs me?
 *  The warm band at the top, each with its fix. What do we have, and is it
 *  working? One row per service: its accounts, the Lemmas that use it, a dot
 *  and a word; the row opens a sheet with the rest. What else could we
 *  connect? Our own server, API or database, then the catalogue.
 *
 *  A connector's sign-in comes home here: the frame sends `?connect=` to
 *  this page, and `useConnectOutcome` reads it, says how it went and reads
 *  the lists again. */
export function ConnectorsPage({ org }: { org: Org }) {
    const orgId = org.id;
    const cache = useQueryClient();
    const [filter, setFilter] = useState<Filter>("all");
    const [query, setQuery] = useState("");
    const [openKey, setOpenKey] = useState<string | null>(null);
    const [picking, setPicking] = useState(false);
    const [pickingApp, setPickingApp] = useState(false);
    /* Adding a server, a database or an API: the kind its card named. */
    const [adding, setAdding] = useState<string | null>(null);
    const [heard, setHeard] = useState<ConnectOutcome | null>(null);
    const [everyCard, setEveryCard] = useState(false);

    const catalog = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors(), staleTime: 10 * 60_000 });
    const accounts = useQuery({ queryKey: ["accounts", orgId], queryFn: () => source.listAccounts(orgId) });
    const installs = useInstalls(orgId);
    const mayInstall = useMayInstall(orgId);
    /* Under the frame's own key: the sidebar read these already. */
    const pods = useQuery({ queryKey: ["pods", orgId], queryFn: () => source.listPods(orgId), staleTime: 5 * 60_000 });
    const podList = useMemo(() => pods.data ?? [], [pods.data]);
    /* Each Lemma's connectors, under the key its own Connections page reads
       them by, so a switch turned here is the state there. */
    const podReads = useQueries({
        queries: podList.map((pod) => ({
            queryKey: ["pod-connectors", pod.id],
            queryFn: () => source.listPodConnectors(pod.id),
            staleTime: 30_000,
        })),
    });
    const podConnectors = new Map<string, PodConnectorsRead>(podList.map((pod, index) => {
        const read = podReads[index];
        return [pod.id, { data: read?.data, isError: Boolean(read?.isError), refetch: () => void read?.refetch() }];
    }));

    const refresh = useConnectorRefresh(orgId);
    /* What a sign-in round trip can change: this organization's installs and
       accounts. Not the catalogue — no connection adds to it or takes from it. */
    const refreshAfterSignIn = () => {
        void cache.invalidateQueries({ queryKey: ["installs", orgId] });
        void cache.invalidateQueries({ queryKey: ["accounts", orgId] });
    };
    const installList = useMemo(() => installs.data ?? [], [installs.data]);
    const takenNames = useMemo(() => installList.map((install) => install.name), [installList]);
    const flow = useOrgConnect({ orgId, takenNames, mayInstall, onChanged: refresh });
    useConnectOutcome(
        (outcome) => { setHeard(outcome); flow.heard(); refreshAfterSignIn(); },
        refreshAfterSignIn,
    );

    const all = useMemo(() => catalog.data ?? [], [catalog.data]);
    const services = useMemo(() => servicesOf(all, accounts.data ?? [], installList), [all, accounts.data, installList]);
    const needing = services.filter((one) => one.needs.length > 0);
    const term = query.trim().toLowerCase();
    const pool = filter === "working" ? services.filter((one) => one.needs.length === 0) : filter === "needs" ? needing : services;
    const rows = pool.filter((one) => matches(one, term));
    const opened = openKey ? services.find((one) => one.key === openKey) ?? null : null;

    /* What an organization can point at itself, and whether any service
       takes an OAuth app of its own. Both are catalogue data: a deployment
       without them says so rather than drawing dead cards. */
    const own = useMemo(() => ownKinds(all), [all]);
    const anyOwnApp = all.some((one) => ownAppKinds(one).length > 0);
    const more = notYetConnected(all, services).filter((one) => entryMatches(one, term));
    const cards = term || everyCard ? more : more.slice(0, FIRST_CARDS);
    const pickable = all.filter((one) => !isBringYourOwn(one));
    const installsOf = (entry: Connector) => installList.filter((one) => one.connector_id === entry.id);
    const usersOf = (service: Service): Pod[] | null => {
        if (!pods.isSuccess) return null;
        if (podList.some((pod) => !podConnectors.get(pod.id)?.data && !podConnectors.get(pod.id)?.isError)) return null;
        return podList.filter((pod) => (podConnectors.get(pod.id)?.data ?? []).some((one) => servesThrough(service, one)));
    };
    const ready = catalog.isSuccess && accounts.isSuccess;

    return (
        <div className="ox-page kconn">
            <PageHead title="Connectors" line="The accounts your Lemmas work through. Each Lemma uses only the ones it’s given.">
                <button type="button" className="b b--m b--primary" disabled={!ready} onClick={() => setPicking(true)}>
                    <PlusIcon size={16} /> Connect an account
                </button>
            </PageHead>

            <div className="kconn__body">
                {/* What the provider's tab said on its way back. Said here,
                    because the tab it happened in has closed. */}
                {heard && (() => {
                    const note = outcomeNote(heard);
                    const named = all.find((one) => one.id === heard.connector)?.title;
                    return (
                        <p className={"kconn-heard" + (note.bad ? " kconn-heard--bad" : "")} role="status">
                            {note.bad ? <WarningIcon size={16} /> : <CheckCircleIcon size={16} />}
                            <span>{named ? named + ": " : ""}{note.text}</span>
                            <button type="button" className="b b--s b--quiet" onClick={() => setHeard(null)}>Dismiss</button>
                        </p>
                    );
                })()}
                {!opened && <SignInNote flow={flow} />}

                {(catalog.isPending || accounts.isPending) && !(catalog.isError || accounts.isError) && (
                    <SkeletonRows label="Reading your connectors" rows={5} slow="Still reading your connectors. It’s slower than usual." />
                )}
                {(catalog.isError || accounts.isError) && (
                    <Calm
                        tone="warn"
                        title="Couldn’t read your connectors"
                        note={codeOf(catalog.error ?? accounts.error)}
                        actions={<TryAgain busy={catalog.isFetching || accounts.isFetching} onClick={() => { void catalog.refetch(); void accounts.refetch(); }} />}
                    >
                        Nothing in them changed. Your Lemmas still work through the accounts they were given.
                    </Calm>
                )}

                {ready && (
                    <>
                        {needing.length > 0 && (
                            <div className="kneed" role="region" aria-label="Needs you">
                                <span className="kneed__t">{needing.length === 1 ? "1 needs you" : needing.length + " need you"}</span>
                                {needing.flatMap((service) => service.needs.map((need, index) => (
                                    <NeedRow key={service.key + ":" + (need.account?.id ?? need.install?.id ?? "") + index}
                                        orgId={orgId} service={service} need={need} flow={flow} onChanged={refresh} />
                                )))}
                            </div>
                        )}

                        <div className="ph__tools kconn-tools">
                            {([["all", "All", services.length], ["working", "Working", services.length - needing.length], ["needs", "Need you", needing.length]] as const).map(([key, word, count]) => (
                                <button key={key} type="button" className="kchip" aria-pressed={filter === key} onClick={() => setFilter(key)}>
                                    {word} <span>{count}</span>
                                </button>
                            ))}
                            <span className="ph__grow" />
                            <label className="ksearch kconn-find">
                                <SearchIcon size={15} />
                                <input value={query} placeholder="Find a connector" aria-label="Find a connector" onChange={(event) => setQuery(event.target.value)} />
                            </label>
                        </div>

                        {rows.length > 0 ? (
                            <div className="klist">
                                {rows.map((service) => (
                                    <ServiceRow key={service.key} service={service} users={usersOf(service)} onOpen={() => setOpenKey(service.key)} />
                                ))}
                            </div>
                        ) : (
                            <p className="kconn-empty">
                                {term ? "Nothing connected matches “" + query + "”."
                                    : filter === "needs" ? "Everything connected is working."
                                        : filter === "working" ? "Nothing connected is working yet."
                                            : "Nothing is connected yet. Connect an account, or add one of your own below."}
                            </p>
                        )}

                        <section className="kconn-sec" aria-labelledby="kconn-own">
                            <div className="kconn-sec__h">
                                <h2 id="kconn-own">Your own</h2>
                                <p>A server, an API or a database of your organization’s, for your Lemmas to work through.</p>
                            </div>
                            {own.length === 0 ? (
                                <p className="kconn-quiet">Your own servers, APIs and databases can’t be added on this deployment.</p>
                            ) : (
                                <div className="kconn-cards">
                                    {own.map((choice) => {
                                        const look = LOOKS[choice.kind];
                                        return (
                                            <div key={choice.kind} className="kconn-card">
                                                <ServiceMark entry={choice.entry} kind={choice.kind} />
                                                <b>{look?.short ?? choice.entry.title}</b>
                                                <span>{look?.tagline ?? choice.entry.description}</span>
                                                {mayInstall !== false && (
                                                    <span className="kconn-card__acts">
                                                        <button type="button" className="b b--s b--secondary" onClick={() => setAdding(choice.kind)}>
                                                            <PlusIcon size={15} /> Add
                                                        </button>
                                                    </span>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                            {own.length > 0 && mayInstall === false && (
                                <p className="kconn-quiet">An organization owner or editor adds these. Once one is added, it is listed above for everyone.</p>
                            )}
                            {anyOwnApp && mayInstall !== false && (
                                <p className="kconn-quiet kconn-ownapp">
                                    Signing people in through an OAuth app your organization registered?
                                    <button type="button" className="b b--s b--quiet" onClick={() => setPickingApp(true)}>
                                        <KeyIcon size={15} /> Use your own app
                                    </button>
                                </p>
                            )}
                        </section>

                        <section className="kconn-sec" aria-labelledby="kconn-more">
                            <div className="kconn-sec__h">
                                <h2 id="kconn-more">More you can connect</h2>
                                <p>Ready for any Lemma to use once it’s connected.</p>
                            </div>
                            {cards.length > 0 ? (
                                <div className="kconn-cards">
                                    {cards.map((entry) => (
                                        <MoreCard key={entry.id} entry={entry} installs={installsOf(entry)} mayInstall={mayInstall} flow={flow} />
                                    ))}
                                </div>
                            ) : (
                                <p className="kconn-quiet">
                                    {term ? "Nothing else matches “" + query + "”."
                                        : all.length > 0 ? "Everything in the catalogue is connected."
                                            /* Zero of zero is not "all of them": an empty
                                               catalogue means its import has not run, or failed. */
                                            : "There’s nothing in the catalogue yet. It may still be loading."}
                                    {!term && all.length === 0 && (
                                        <>
                                            {" "}
                                            <button type="button" className="b b--s b--secondary" onClick={() => void catalog.refetch()}>
                                                <RefreshIcon size={14} /> Try again
                                            </button>
                                        </>
                                    )}
                                </p>
                            )}
                            {!term && more.length > FIRST_CARDS && (
                                <div>
                                    <button type="button" className="b b--s b--secondary" onClick={() => setEveryCard((was) => !was)}>
                                        {everyCard ? "Show fewer" : "Show all " + more.length}
                                    </button>
                                </div>
                            )}
                            {/* Most of the catalogue's breadth arrives through
                                Composio, which a local install has no key for
                                until somebody adds one. Hidden once it is set,
                                and anywhere but this computer's own window. */}
                            <SetUpOnThisMac form="composio" compact lead="Want Notion, Linear, HubSpot and more? Add a Composio key on {machine}." />
                        </section>
                    </>
                )}
            </div>

            {opened && (
                <ConnectorSheet
                    orgId={orgId}
                    service={opened}
                    pods={podList}
                    podsRead={{ pending: pods.isPending, failed: pods.isError, retry: () => void pods.refetch() }}
                    podConnectors={podConnectors}
                    mayInstall={mayInstall}
                    takenNames={takenNames}
                    flow={flow}
                    onChanged={refresh}
                    onClose={() => setOpenKey(null)}
                />
            )}
            {picking && (
                <ServicePicker
                    title="Connect an account"
                    subtitle="Choose the service to sign in to"
                    services={pickable}
                    why={(entry) => mayConnect(installsOf(entry), mayInstall) ? null : "An organization owner or editor sets this one up first"}
                    onPick={(entry) => { setPicking(false); flow.open(entry, connectAgainst(installsOf(entry))); }}
                    onClose={() => setPicking(false)}
                />
            )}
            {pickingApp && (
                <OwnAppPicker
                    connectors={all}
                    onClose={() => setPickingApp(false)}
                    onPick={(entry) => { setPickingApp(false); flow.open(entry, null, true); }}
                />
            )}
            {adding && (
                <AddConnector
                    orgId={orgId}
                    entries={all}
                    initialKind={adding}
                    onClose={() => setAdding(null)}
                    onDone={() => { setAdding(null); refresh(); }}
                />
            )}
            {flow.dialog}
        </div>
    );
}

/** One connected service: its mark, its name and accounts, the Lemmas that
 *  use it, and how it is doing. The whole row opens its sheet. `users` is
 *  null while the Lemmas' connectors are still being read. */
function ServiceRow({ service, users, onOpen }: { service: Service; users: Pod[] | null; onOpen: () => void }) {
    const status = statusOf(service);
    const sub = subtitleOf(service, (account) => accountName(account, service.entry.title))
        || (service.kind ? LOOKS[service.kind]?.short ?? "" : "");
    return (
        <button type="button" className="krow krow--open kconn-row" aria-haspopup="dialog" onClick={onOpen}>
            <span className="krow__lead">
                <ServiceMark entry={service.entry} kind={service.kind} />
                <span className="krow__v">
                    <b>{service.title}</b>
                    {sub && <span>{sub}</span>}
                </span>
            </span>
            <span className="kconn-uses">
                {users && users.length > 0 && (
                    <span className="kconn-faces" aria-hidden="true">
                        {users.slice(0, 3).map((pod) => <TeammateFace key={pod.id} pod={pod} size={24} className="kconn-face" />)}
                    </span>
                )}
                {users && <span className="kconn-names">{users.length ? users.map((pod) => pod.name).join(", ") : "No Lemma yet"}</span>}
            </span>
            <span className="krow__end">
                <span className={"kstatus" + (status.tone === "warn" ? " kstatus--warn" : "")}>{status.word}</span>
                <ChevronRightIcon size={16} className="kconn-chev" aria-hidden="true" />
            </span>
        </button>
    );
}

/** A catalogue service nobody here has connected: what it is, and Connect.
 *  Where this person cannot connect it — nothing installed, and making the
 *  install is an owner's or editor's — the card says who can, instead of a
 *  button that would only be refused. */
function MoreCard({ entry, installs, mayInstall, flow }: {
    entry: Connector;
    installs: Install[];
    mayInstall: boolean | null;
    flow: OrgConnect;
}) {
    const out = flow.sent?.entryId === entry.id;
    const starting = flow.starting(entry.id);
    return (
        <div className="kconn-card">
            <ServiceMark entry={entry} />
            <b>{entry.title}</b>
            {entry.description && <span>{entry.description}</span>}
            <span className="kconn-card__acts">
                {out ? (
                    <span className="kconn-quiet" role="status">Finishing in the {entry.title} tab</span>
                ) : mayConnect(installs, mayInstall) ? (
                    <button type="button" className="b b--s b--secondary" disabled={starting}
                        onClick={() => flow.open(entry, connectAgainst(installs))}>
                        {starting ? <LoadingIndicator inline label="Opening" /> : "Connect"}
                    </button>
                ) : (
                    <span className="kconn-quiet">An owner or editor sets this up</span>
                )}
                {/* Beside Connect rather than instead of it: an organization's
                    own OAuth app still works without this computer's. */}
                <SetUpOnThisMac form={oauthFormForConnector(entry.id)} compact />
            </span>
        </div>
    );
}
