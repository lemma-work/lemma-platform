"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { accountName, accountTrouble, freshSlug, impactLines, source, type ConnectorAccount, type Pod, type PodConnector } from "@/data";
import { usageLine } from "@/data/pod-accounts";
import { LOOKS } from "@/connect/add-connector";
import { RotateDialog } from "@/connect/connect-dialog";
import { EditInstall } from "@/connect/edit-install";
import { connectorProblem, connectRoute, discoveryNote, installTarget, isBringYourOwn, kindFor, type Install } from "@/connect/install";
import { NeedRow } from "@/connect/need-row";
import { SignInNote, type OrgConnect } from "@/connect/org-connect";
import { usePodMay } from "@/connect/pod-connect";
import { MANAGE_CONNECTORS } from "@/connect/pod-connections";
import { useDeleteInstall, useMakeDefaultInstall, useRefreshOperations } from "@/connect/queries";
import { ServiceMark } from "@/connect/service-mark";
import { connectAgainst, mayConnect, servesThrough, statusOf, type Service } from "@/connect/services";
import { ConfirmDialog } from "@/shell/confirm-dialog";
import { TeammateFace } from "@/space/teammate-face";
import { LoadingIndicator } from "@/ui/loading";
import { CloseIcon, DeleteIcon, MoreIcon, PlusIcon } from "@/ui/icons";
import { writeView } from "./address";

/** One Lemma's connectors as the page read them: absent while they are
 *  being read, and a failure with its way back. */
export interface PodConnectorsRead {
    data?: PodConnector[];
    isError: boolean;
    refetch: () => void;
}

/** A connected service, from the side: its accounts, which Lemmas use it,
 *  what it can do, and the way to remove it.
 *
 *  A sheet rather than a page, because it is a look at one row of a list the
 *  person is in the middle of — the list stays where it was underneath, and
 *  closing it puts them back on the row. Everything a row of the old
 *  Connectors section did is here: reconnecting and installing (in the band
 *  at the top, which is the same row the page's band draws), replacing a
 *  credential, an account's uses, an app of the organization's own, editing
 *  and re-reading a server, removing it all with what that stops said first.
 *  What is new is the Lemmas: which of them work through it, turned on and off
 *  with the same calls a Lemma's own Connections page makes. */
export function ConnectorSheet({ orgId, service, pods, podsRead, podConnectors, mayInstall, takenNames, flow, onChanged, onClose }: {
    orgId: string;
    service: Service;
    pods: Pod[];
    /** How reading the organization's Lemmas went. */
    podsRead: { pending: boolean; failed: boolean; retry: () => void };
    podConnectors: Map<string, PodConnectorsRead>;
    /** Owner or editor: may change and remove the organization's installs.
     *  `null` when that cannot be told — offered, and the server decides. */
    mayInstall: boolean | null;
    takenNames: string[];
    flow: OrgConnect;
    onChanged: () => void;
    onClose: () => void;
}) {
    const panel = useRef<HTMLElement>(null);
    const titleId = useId();
    const closeRef = useRef(onClose);
    closeRef.current = onClose;
    const manage = mayInstall !== false;
    const { entry, own } = service;
    const look = service.kind ? LOOKS[service.kind] : undefined;
    const [editing, setEditing] = useState<Install | null>(null);

    /* Focus moves in, the keyboard stays in while it is open, Escape closes
       it, and focus goes back to the row that opened it. A dialog over it
       (connecting, a confirmation) answers its own keys first. */
    useEffect(() => {
        const previous = document.activeElement as HTMLElement | null;
        panel.current?.querySelector<HTMLElement>(".ksheet__x")?.focus();
        const onKey = (event: KeyboardEvent) => {
            if (event.defaultPrevented || document.querySelector(".modal")) return;
            if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
            if (event.key !== "Tab" || !panel.current) return;
            const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), summary')).filter((el) => el.getClientRects().length > 0);
            const first = focusable[0], last = focusable.at(-1);
            if (!first) { event.preventDefault(); return; }
            if (!panel.current.contains(document.activeElement)) { event.preventDefault(); first.focus(); return; }
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("keydown", onKey);
            if (previous?.isConnected) previous.focus();
        };
    }, []);

    /* Several installs of one service, or one the organization set up
       itself, are worth naming: that is where Make default and Edit live.
       One install of Lemma's own app behind its accounts is noise. */
    const grouped = !own && (service.installs.length > 1 || service.installs.some((one) => one.config_source === "ORG_CUSTOM"));
    const canAdd = !own && !isBringYourOwn(entry) && mayConnect(service.installs, mayInstall);
    /* Who it is at a glance; what it does is its own part below. */
    const count = service.accounts.length;
    const said = own
        ? look?.title ?? entry.title
        : [count === 1 ? "1 account" : count + " accounts", statusOf(service).word].join(" · ");

    return createPortal(
        <>
            <div className="ksheet-veil" aria-hidden="true" onMouseDown={() => { if (!document.querySelector(".modal")) onClose(); }} />
            <aside className="ksheet kconn-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel}>
                <header className="ksheet__head">
                    <ServiceMark entry={entry} kind={service.kind} large />
                    <h2 id={titleId}>{service.title}</h2>
                    {said && <p>{said}</p>}
                    <button type="button" className="ksheet__x b b--m b--quiet b--icon" aria-label="Close" onClick={onClose}>
                        <CloseIcon size={18} />
                    </button>
                </header>

                <div className="ksheet__body">
                    {service.needs.length > 0 && (
                        <div className="kneed">
                            <span className="kneed__t">{service.needs.length === 1 ? "Needs you" : service.needs.length + " things need you"}</span>
                            {service.needs.map((need, index) => (
                                <NeedRow key={(need.account?.id ?? need.install?.id ?? "") + index} orgId={orgId} service={service}
                                    need={need} flow={flow} onChanged={onChanged} mark={false} />
                            ))}
                        </div>
                    )}
                    <SignInNote flow={flow} entryId={entry.id} />

                    {(service.accounts.length > 0 || canAdd) && (
                        <section className="kconn-part" aria-label="Accounts">
                            <h3 className="kconn-part__h">Accounts</h3>
                            {service.accounts.length > 0 ? (
                                <div className="klist kconn-inner">
                                    {service.accounts.map((account) => (
                                        <AccountRow key={account.id} orgId={orgId} service={service} account={account} pods={pods} onChanged={onChanged} />
                                    ))}
                                </div>
                            ) : (
                                <p className="kconn-quiet">No account yet.</p>
                            )}
                            {canAdd && (
                                <div>
                                    <button type="button" className="b b--s b--secondary" disabled={flow.starting(entry.id)}
                                        onClick={() => flow.open(entry, connectAgainst(service.installs))}>
                                        <PlusIcon size={15} /> {service.accounts.length ? "Add another account" : "Connect an account"}
                                    </button>
                                </div>
                            )}
                        </section>
                    )}

                    {grouped && (
                        <section className="kconn-part" aria-label="Signs in through">
                            <h3 className="kconn-part__h">Signs in through</h3>
                            <div className="klist kconn-inner">
                                {service.installs.map((install) => (
                                    <InstallRow key={install.id} orgId={orgId} service={service} install={install} manage={manage} pods={pods}
                                        flow={flow} onEdit={() => setEditing(install)} onChanged={onChanged} />
                                ))}
                            </div>
                        </section>
                    )}

                    <section className="kconn-part" aria-label="Lemmas that use it">
                        <h3 className="kconn-part__h">Lemmas that use it</h3>
                        {podsRead.failed ? (
                            <p className="kconn-quiet" role="alert">
                                Couldn’t read your Lemmas.{" "}
                                <button type="button" className="b b--s b--quiet" onClick={podsRead.retry}>Try again</button>
                            </p>
                        ) : podsRead.pending ? (
                            <p className="kconn-quiet" role="status"><LoadingIndicator inline label="Reading your Lemmas" /></p>
                        ) : pods.length === 0 ? (
                            <p className="kconn-quiet">There are no Lemmas here yet.</p>
                        ) : (
                            <div className="klist kconn-inner">
                                {pods.map((pod) => (
                                    <LemmaUse key={pod.id} pod={pod} service={service} read={podConnectors.get(pod.id)} />
                                ))}
                            </div>
                        )}
                        <p className="kconn-quiet">
                            Turned on here, a Lemma uses each person’s own account. Sharing one account with everyone in a Lemma is set in that Lemma’s connections.
                        </p>
                    </section>

                    <WhatItCanDo orgId={orgId} service={service} manage={manage} onEdit={() => own && setEditing(own)} onChanged={onChanged} />
                </div>

                <footer className="ksheet__foot">
                    <RemoveService orgId={orgId} service={service} manage={manage} pods={pods} onGone={() => { onChanged(); onClose(); }} />
                </footer>
            </aside>

            {editing && (
                <EditInstall
                    orgId={orgId}
                    install={editing}
                    connector={entry}
                    takenNames={takenNames}
                    onClose={() => setEditing(null)}
                    onDone={() => { setEditing(null); onChanged(); }}
                />
            )}
        </>,
        document.body,
    );
}

/** A row's "more": the acts that are not its one main thing, behind ⋯ so a
 *  row stays one name and one status. Closes on a pick, on a click outside
 *  and on Escape. */
function RowMenu({ label, children }: { label: string; children: ReactNode }) {
    const [open, setOpen] = useState(false);
    const box = useRef<HTMLDetailsElement>(null);
    useEffect(() => {
        if (!open) return;
        const away = (event: PointerEvent) => {
            if (!box.current?.contains(event.target as Node)) setOpen(false);
        };
        const key = (event: KeyboardEvent) => {
            if (event.key === "Escape") { event.preventDefault(); setOpen(false); box.current?.querySelector("summary")?.focus(); }
        };
        document.addEventListener("pointerdown", away);
        document.addEventListener("keydown", key);
        return () => {
            document.removeEventListener("pointerdown", away);
            document.removeEventListener("keydown", key);
        };
    }, [open]);
    return (
        <details className="kconn-more" ref={box} open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
            <summary className="b b--s b--quiet b--icon" aria-label={label}><MoreIcon size={16} /></summary>
            <div className="kconn-menu" role="menu" onClick={(event) => { if ((event.target as HTMLElement).closest("button")) setOpen(false); }}>
                {children}
            </div>
        </details>
    );
}

/** One account on the service: who it is, whether it is the default, and
 *  behind ⋯ where it is used, replacing its credential and removing it. */
function AccountRow({ orgId, service, account, pods, onChanged }: {
    orgId: string;
    service: Service;
    account: ConnectorAccount;
    pods: Pod[];
    onChanged: () => void;
}) {
    const [showUse, setShowUse] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [rotating, setRotating] = useState(false);
    /* Where it is used, asked for when somebody asks — or when they are
       about to remove it, which is when it matters most. */
    const usage = useQuery({
        queryKey: ["account-usage", account.id],
        queryFn: () => source.accountUsage(account.id),
        enabled: showUse || confirming,
        staleTime: 60_000,
        retry: false,
    });
    const drop = useMutation({
        mutationFn: () => source.disconnectAccount(orgId, account.id),
        onSuccess: () => { setConfirming(false); onChanged(); },
    });
    const install = service.installs.find((one) => one.id === account.authConfigId) ?? null;
    /* An OAuth account is signed into again, not retyped; only a pasted
       credential is replaced in place, keeping everything pinned to it. */
    const credentialed = install !== null && connectRoute(install, kindFor(service.entry, install)) === "credentials";
    const name = accountName(account, service.entry.title);
    const podName = (podId: string) => pods.find((pod) => pod.id === podId)?.name ?? "";
    const lines = usage.data?.map((one) => usageLine(one, podName)) ?? [];

    return (
        <div className="krow kconn-acct">
            <span className="krow__v">
                <b>{name}</b>
                <span>{[account.ref && account.ref !== account.label ? account.ref : "", account.usable ? "" : accountTrouble(account)].filter(Boolean).join(" · ") || (account.usable ? "Working" : "")}</span>
            </span>
            <span className="krow__end">
                {account.isDefault && <span className="ktag">Default</span>}
                <RowMenu label={"More for " + name}>
                    <button type="button" role="menuitem" aria-expanded={showUse} onClick={() => setShowUse((was) => !was)}>
                        {showUse ? "Hide where it is used" : "Where it is used"}
                    </button>
                    {credentialed && <button type="button" role="menuitem" onClick={() => setRotating(true)}>Replace credential</button>}
                    <button type="button" role="menuitem" className="kconn-menu__bad" onClick={() => setConfirming(true)}>Remove</button>
                </RowMenu>
            </span>
            {showUse && (
                <ul className="kconn-usage" aria-label={"Where " + name + " is used"}>
                    {usage.isPending && <li role="status">Looking…</li>}
                    {usage.isError && (
                        <li role="alert">Couldn’t tell where it is used. <button type="button" className="b b--s b--quiet" onClick={() => void usage.refetch()}>Try again</button></li>
                    )}
                    {usage.isSuccess && lines.length === 0 && <li>Not in use in any Lemma yet.</li>}
                    {lines.map((line, index) => <li key={index}>{line}</li>)}
                </ul>
            )}
            {rotating && (
                <RotateDialog
                    orgId={orgId}
                    connector={service.entry}
                    install={install ?? service.installs[0] ?? null}
                    accountId={account.id}
                    accountName={name}
                    onClose={() => setRotating(false)}
                    onDone={() => { setRotating(false); onChanged(); }}
                />
            )}
            {confirming && (
                <ConfirmDialog
                    title={"Remove " + name + "?"}
                    line={usage.isPending ? "Checking where it is used…"
                        : lines.length ? <span className="kconn-impact">{["Anything pinned to it stops:", ...lines].map((line, index) => <span key={index}>{line}</span>)}</span>
                            : "Anything pinned to it stops."}
                    confirm="Remove"
                    working="Removing…"
                    danger
                    busy={drop.isPending}
                    problem={drop.isError ? connectorProblem(drop.error, "It could not be removed just now.") : null}
                    onConfirm={() => drop.mutate()}
                    onClose={() => { setConfirming(false); drop.reset(); }}
                />
            )}
        </div>
    );
}

/** One install a service signs in through — Lemma's own app, or the
 *  organization's. Where an organization holds two, the default is the one a
 *  bare service name resolves to, and the person who manages them picks it. */
function InstallRow({ orgId, service, install, manage, pods, flow, onEdit, onChanged }: {
    orgId: string;
    service: Service;
    install: Install;
    manage: boolean;
    pods: Pod[];
    flow: OrgConnect;
    onEdit: () => void;
    onChanged: () => void;
}) {
    const promote = useMakeDefaultInstall(orgId);
    const [said, setSaid] = useState<string | null>(null);
    const [removing, setRemoving] = useState(false);
    const off = install.status === "DISABLED";
    const yours = install.config_source === "ORG_CUSTOM";
    const onIt = service.accounts.filter((one) => one.authConfigId === install.id).length;
    const sub = [yours ? "Your organization’s app" : "Lemma’s app", off ? "Turned off" : "", onIt ? onIt + (onIt === 1 ? " account" : " accounts") : ""]
        .filter(Boolean).join(" · ");
    return (
        <div className="krow kconn-acct">
            <span className="krow__v">
                <b>{install.name || install.id}</b>
                <span>{said ?? sub}</span>
            </span>
            <span className="krow__end">
                {install.is_default ? <span className="ktag">Default</span>
                    : manage && !off && (
                        <button type="button" className="b b--s b--quiet" disabled={promote.isPending}
                            onClick={() => promote.mutate(install, {
                                onSuccess: () => { setSaid(null); onChanged(); },
                                onError: () => setSaid("It could not be made the default just now."),
                            })}>
                            {promote.isPending ? <LoadingIndicator inline label="Saving" /> : "Make default"}
                        </button>
                    )}
                {(!off || manage) && (
                    <RowMenu label={"More for " + (install.name || install.id)}>
                        {!off && <button type="button" role="menuitem" onClick={() => flow.open(service.entry, install)}>Connect an account on it</button>}
                        {manage && yours && <button type="button" role="menuitem" onClick={onEdit}>Edit</button>}
                        {manage && <button type="button" role="menuitem" className="kconn-menu__bad" onClick={() => setRemoving(true)}>Remove</button>}
                    </RowMenu>
                )}
            </span>
            {removing && (
                <RemoveInstalls orgId={orgId} title={install.name || install.id} installs={[install]} pods={pods}
                    onClose={() => setRemoving(false)} onGone={() => { setRemoving(false); onChanged(); }} />
            )}
        </div>
    );
}

/** One Lemma and whether it works through this service.
 *
 *  On is the Lemma declaring a connector on it, off is taking that away —
 *  `POST` and `DELETE /pods/{pod}/connectors`, the calls the Lemma's own
 *  Connections page makes, under the same keys, so both pages agree. Only
 *  for somebody who may change that Lemma's connectors; anybody else sees
 *  the state and where it is changed, never a switch that can only fail. */
function LemmaUse({ pod, service, read }: { pod: Pod; service: Service; read: PodConnectorsRead | undefined }) {
    const cache = useQueryClient();
    const may = usePodMay(pod.id, MANAGE_CONNECTORS);
    const [asking, setAsking] = useState(false);
    const list = read?.data ?? [];
    const on = list.filter((one) => servesThrough(service, one));
    /* Awaited by the change, so the switch holds its new position until
       the list it is read from says the same — not off for a moment while
       the read is on its way. */
    const refresh = () => Promise.all([
        cache.invalidateQueries({ queryKey: ["pod-connectors", pod.id] }),
        cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] }),
    ]);
    const add = useMutation({
        mutationFn: () => source.addPodConnector(pod.id, {
            slug: freshSlug(service.own ? service.own.name || service.entry.id : service.entry.id, list.map((one) => one.slug)),
            authConfigId: service.own?.id ?? connectAgainst(service.installs)?.id ?? "",
            connectorId: service.entry.id,
            mode: "PER_MEMBER",
            sharedPodAccountId: "",
            required: false,
            purpose: "",
        }),
        onSuccess: () => refresh(),
    });
    const drop = useMutation({
        mutationFn: async () => {
            for (const one of on) await source.removePodConnector(pod.id, one.slug);
        },
        onSuccess: async () => { await refresh(); setAsking(false); },
    });
    const busy = add.isPending || drop.isPending;
    const where = writeView({ name: "outcome", podId: pod.id, tab: "settings", section: "connections" });
    const how = on.map((one) => one.mode === "SHARED"
        ? one.sharedAccount ? "Runs as " + one.sharedAccount : "Waiting for a shared account"
        : "Each person’s own account");
    const readOnly = Boolean(read?.data) && may.answered && !may.allowed;
    const sub = add.isError ? connectorProblem(add.error, "It could not be turned on just now.")
        : read?.isError ? "Couldn’t read its connectors."
            : Array.from(new Set(how)).join(" · ");

    return (
        <div className="krow kconn-use">
            <span className="krow__lead">
                <TeammateFace pod={pod} size={28} />
                <span className="krow__v">
                    <b>{pod.name}</b>
                    {(sub || readOnly) && (
                        <span>
                            {sub}{sub && readOnly ? " · " : ""}
                            {/* Not theirs to change: where it is changed, and
                                by whom, rather than a switch that can only fail. */}
                            {readOnly && <a className="kconn-link" href={where}>Changed in its connections</a>}
                        </span>
                    )}
                </span>
            </span>
            <span className="krow__end">
                {read?.isError ? (
                    <button type="button" className="b b--s b--quiet" onClick={read.refetch}>Try again</button>
                ) : !read?.data ? (
                    <LoadingIndicator inline label={"Reading " + pod.name + "’s connectors"} />
                ) : may.allowed ? (
                    <button type="button" role="switch" className="kswitch" aria-checked={add.isPending ? true : drop.isPending ? false : on.length > 0} aria-busy={busy || undefined}
                        aria-label={pod.name + " uses " + service.title} disabled={busy}
                        onClick={() => { if (on.length) setAsking(true); else { add.reset(); add.mutate(); } }} />
                ) : may.answered ? (
                    <span className="ktag">{on.length ? "On" : "Off"}</span>
                ) : (
                    <LoadingIndicator inline label="Checking what you can change" />
                )}
            </span>
            {asking && (
                <ConfirmDialog
                    title={"Stop " + pod.name + " using " + service.title + "?"}
                    line={"Anything in " + pod.name + " that works through " + on.map((one) => one.slug).join(", ") + " stops until it is turned back on."}
                    confirm="Turn off"
                    working="Turning off…"
                    danger
                    busy={drop.isPending}
                    problem={drop.isError ? connectorProblem(drop.error, "It could not be turned off just now.") : null}
                    onConfirm={() => drop.mutate()}
                    onClose={() => { setAsking(false); drop.reset(); }}
                />
            )}
        </div>
    );
}

/** What the service can do, in its catalogue's own words — and for a
 *  server, database or API the organization added, where it points and the
 *  way to read again what it offers. */
function WhatItCanDo({ orgId, service, manage, onEdit, onChanged }: {
    orgId: string;
    service: Service;
    manage: boolean;
    onEdit: () => void;
    onChanged: () => void;
}) {
    const reread = useRefreshOperations(orgId);
    const [said, setSaid] = useState<string | null>(null);
    const { own, entry } = service;
    const look = service.kind ? LOOKS[service.kind] : undefined;
    const points = own ? installTarget(own) : null;
    const words = own ? look?.blurb ?? entry.description : entry.description;
    if (!words && !own) return null;
    return (
        <section className="kconn-part" aria-label="What it can do">
            <h3 className="kconn-part__h">What it can do</h3>
            <ul className="kconn-can">
                {words && <li>{words}</li>}
                {points && <li>Points at {points}</li>}
            </ul>
            {own && manage && (
                <div className="kconn-acts">
                    <button type="button" className="b b--s b--secondary" disabled={reread.isPending}
                        onClick={() => reread.mutate(own, {
                            onSuccess: (answer) => { setSaid(discoveryNote(answer.status, answer.operation_count, answer.error)); onChanged(); },
                            onError: () => setSaid("What it offers could not be read just now."),
                        })}>
                        {reread.isPending ? <LoadingIndicator inline label="Reading" /> : "Read what it offers again"}
                    </button>
                    <button type="button" className="b b--s b--quiet" onClick={onEdit}>Edit</button>
                </div>
            )}
            {said && <p className="kconn-quiet" role="status">{said}</p>}
        </section>
    );
}

/** The sheet's foot: removing the service from the organization.
 *
 *  For whoever manages installs that is removing them, which takes their
 *  accounts and every Lemma's connector on them — so what it stops is read
 *  and said before the button that does it. Anybody else can only remove the
 *  accounts, and the button says that rather than something it cannot do. */
function RemoveService({ orgId, service, manage, pods, onGone }: {
    orgId: string;
    service: Service;
    manage: boolean;
    pods: Pod[];
    onGone: () => void;
}) {
    const [asking, setAsking] = useState(false);
    const drop = useMutation({
        mutationFn: async () => {
            for (const account of service.accounts) await source.disconnectAccount(orgId, account.id);
        },
        onSuccess: () => { setAsking(false); onGone(); },
    });
    const installs = service.own ? [service.own] : service.installs;
    const wholly = manage && installs.length > 0;
    if (!wholly && service.accounts.length === 0) {
        return <p className="kconn-quiet">An organization owner or editor can remove {service.title}.</p>;
    }
    return (
        <>
            <button type="button" className="b b--m b--secondary b--red" onClick={() => setAsking(true)}>
                <DeleteIcon size={16} /> {wholly ? "Remove " + service.title : service.accounts.length === 1 ? "Remove the account" : "Remove its accounts"}
            </button>
            {asking && wholly && (
                <RemoveInstalls orgId={orgId} title={service.title} installs={installs} pods={pods}
                    onClose={() => setAsking(false)} onGone={() => { setAsking(false); onGone(); }} />
            )}
            {asking && !wholly && (
                <ConfirmDialog
                    title={"Remove " + (service.accounts.length === 1 ? accountName(service.accounts[0], service.entry.title) : "its accounts") + "?"}
                    line={"Anything pinned to " + (service.accounts.length === 1 ? "it" : "them") + " stops. " + service.title + " stays set up for the organization."}
                    confirm="Remove"
                    working="Removing…"
                    danger
                    busy={drop.isPending}
                    problem={drop.isError ? connectorProblem(drop.error, "It could not be removed just now.") : null}
                    onConfirm={() => drop.mutate()}
                    onClose={() => { setAsking(false); drop.reset(); }}
                />
            )}
        </>
    );
}

/** Removing installs, with what that stops read first. A preview that failed
 *  is said, not a lock: removing stays the manager's call, made knowing less. */
function RemoveInstalls({ orgId, title, installs, pods, onClose, onGone }: {
    orgId: string;
    title: string;
    installs: Install[];
    pods: Pod[];
    onClose: () => void;
    onGone: () => void;
}) {
    const remove = useDeleteInstall(orgId);
    const [failure, setFailure] = useState<string | null>(null);
    const impacts = useQueries({
        queries: installs.map((install) => ({
            queryKey: ["install-impact", orgId, install.id],
            queryFn: () => source.installationImpact(orgId, install.id),
            staleTime: 0,
            retry: false,
        })),
    });
    const podName = (podId: string) => pods.find((pod) => pod.id === podId)?.name ?? "";
    const reading = impacts.some((one) => one.isPending);
    const failed = impacts.some((one) => one.isError);
    const lines = impacts.flatMap((one) => one.data ? impactLines(one.data, podName) : []);
    const run = async () => {
        setFailure(null);
        try {
            for (const install of installs) await remove.mutateAsync(install);
            onGone();
        } catch (problem) {
            setFailure(connectorProblem(problem, "It could not be removed just now."));
        }
    };
    return (
        <ConfirmDialog
            title={"Remove " + title + "?"}
            line={reading ? "Checking what uses it…" : (
                <span className="kconn-impact">
                    <span>It goes for everyone in the organization.</span>
                    {lines.map((line) => <span key={line}>{line}</span>)}
                    {failed && <span>Couldn’t read everything this affects. Removing it still removes its accounts, and anything using them stops.</span>}
                </span>
            )}
            confirm="Remove"
            working="Removing…"
            danger
            busy={remove.isPending}
            problem={failure}
            onConfirm={() => void run()}
            onClose={onClose}
        />
    );
}
