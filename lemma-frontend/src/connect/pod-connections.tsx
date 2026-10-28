"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Connector, type Pod, type PodConnector } from "@/data";
import {
    askedLine, connectorName, coverageLine, freshSlug, mayUseOnceConnected, modeBadge, myStatus, slugProblem, statusNote, truncatedLine, waitingLine, wantsCoverage,
    type PodConnectorMode, type PodConnectorPatch,
} from "@/data/pod-connectors";
import {
    ownedBy, shareableFor, suggestedSlug, visibilityWord,
    type Grantee, type PodAccount, type PodAccountPatch,
} from "@/data/pod-accounts";
import { connectorChanges } from "@/data/pod-connectors";
import { CONNECTOR_USE } from "@/data/agents";
import { useMe } from "@/session/use-me";
import { Modal } from "@/shell/modal";
import { CheckCircleIcon, PlusIcon, WarningIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { ProblemLine } from "./connection-problem";
import { addChoices, focusTarget, type FocusTarget } from "./choices";
import { useConnectFlow, useConnectionsData, usePodMay, type SignIn } from "./pod-connect";
import { SignInLink } from "./sign-in-link";
import type { Install } from "./install";

/** `pod.connector.manage`: add, change and remove the pod's connectors, and
 *  take any account out of the pod. Never sharing one — that is its owner's. */
export const MANAGE_CONNECTORS = "pod.connector.manage";

/** A teammate's connections: what it works through, and whose account each
 *  runs as.
 *
 *  This replaced the organization's Connectors page embedded under About,
 *  which said a teammate "can use" every account in the organization — never
 *  true, and less so now: a pod declares the connectors it uses, each by a
 *  slug, and the connector decides whose account a call runs as. So this is
 *  the pod's list, with my own standing on each one, and — for the people
 *  who may change it — the way to add, point and remove them. Below it, the
 *  accounts of mine in use here, which only I can share or withdraw.
 *
 *  `focus` is the connector a link named (`?connector=gmail`), which is
 *  where every typed refusal's `connect_url` sends a person. */
export function PodConnections({ pod, focus }: { pod: Pod; focus: string | null }) {
    const me = useMe() ?? "";
    const data = useConnectionsData(pod);
    const accounts = useQuery({ queryKey: ["pod-accounts", pod.id], queryFn: () => source.listPodAccounts(pod.id), staleTime: 30_000 });
    const may = usePodMay(pod.id, MANAGE_CONNECTORS).allowed;
    /* Asked only of a server that does not say `can_use` on each connector. */
    const unsaid = (data.connectors.data ?? []).some((one) => one.canUse === null);
    const podUse = usePodMay(pod.id, CONNECTOR_USE, unsaid);
    const podMayUse = podUse.answered ? podUse.allowed : null;
    const flow = useConnectFlow(pod, data.titles, data.installs.data);
    const [editing, setEditing] = useState<PodConnector | "new" | null>(null);
    /* The connector just added, drawn as if a link had named it: the row
       its Connect is on, in view. */
    const [added, setAdded] = useState<string | null>(null);

    const list = data.connectors.data ?? [];
    const podAccounts = accounts.data ?? [];
    const ownerName = (userId: string) =>
        userId && userId === me ? "you" : pod.members.find((one) => one.userId === userId)?.name ?? "";
    const titleOf = (connectorId: string) => data.titles.get(connectorId)?.title ?? connectorId;
    const usableInstalls = (data.installs.data ?? []).filter((one) => one.status !== "DISABLED");
    /* Read only once the lists it is judged against are in: until then a
       catalog id reads as a name that matches nothing. The installations
       may be refused to a member, which leaves the catalog to match. */
    const settled = data.connectors.isSuccess && !data.catalog.isPending && !data.installs.isPending;
    const target = settled ? focusTarget(focus, list, usableInstalls, data.catalog.data ?? []) : { kind: "none" as const };
    const focusedSlug = target.kind === "connector" ? target.slug : added;

    return (
        <div className="podconn">
            <section className="podconn__section" aria-labelledby="podconn-used">
                <header className="podconn__head">
                    <h2 id="podconn-used">What {pod.name} works through</h2>
                    {may && (
                        <button className="aboutpage__action" onClick={() => setEditing("new")}>
                            <PlusIcon size={14} /> Add a connector
                        </button>
                    )}
                </header>
                {data.connectors.isPending && <p className="aboutpage__quiet" role="status">Reading its connectors…</p>}
                {data.connectors.isError && (
                    <p className="aboutpage__quiet" role="alert">
                        Couldn’t read {pod.name}’s connectors.{" "}
                        <button className="linkish" onClick={() => void data.connectors.refetch()}>Try again</button>
                    </p>
                )}
                {data.connectors.isSuccess && list.length === 0 && (
                    <p className="aboutpage__quiet">
                        None yet. {may ? "Add one, and say whose account it runs as." : "Someone who can change " + pod.name + " adds them."}
                    </p>
                )}
                {list.length > 0 && (
                    <ul className="podconn__list">
                        {list.map((connector) => (
                            <ConnectorRow
                                key={connector.id}
                                pod={pod}
                                connector={connector}
                                title={titleOf(connector.connectorId)}
                                owner={ownerName(connector.sharedOwnerId)}
                                status={myStatus(connector, data.mine, me, ownedBy(podAccounts, me).find((one) => one.authConfigId === connector.authConfigId && one.isMemberDefault)?.accountId)}
                                focused={focusedSlug === connector.slug}
                                may={may}
                                usable={mayUseOnceConnected(connector, podMayUse)}
                                busy={flow.busy === connector.slug}
                                pending={flow.pending}
                                waiting={flow.waiting === connector.slug}
                                signIn={flow.link?.slug === connector.slug ? flow.link : null}
                                problem={flow.problem?.slug === connector.slug ? flow.problem.text : ""}
                                onConnect={(reconnect) => flow.connect(connector, reconnect)}
                                onEdit={() => setEditing(connector)}
                            />
                        ))}
                    </ul>
                )}
                {target.kind === "connect" && focus && (
                    <ConnectOffer pod={pod} name={focus} target={target} flow={flow} />
                )}
                {target.kind === "unknown" && (
                    <p className="aboutpage__quiet" role="alert">{pod.name} has no connector called {focus} any more.</p>
                )}
            </section>

            <MyAccountsHere pod={pod} me={me} may={may} accounts={podAccounts} reading={accounts} connectors={list} titleOf={titleOf}
                orgAccounts={data.accounts.data ?? []} installs={data.installs.data ?? []} />

            {editing && (
                <ConnectorForm
                    pod={pod}
                    editing={editing === "new" ? null : editing}
                    connectors={list}
                    installs={usableInstalls}
                    catalog={data.catalog.data ?? []}
                    accounts={podAccounts}
                    titleOf={titleOf}
                    ownerName={ownerName}
                    onClose={() => setEditing(null)}
                    onConnect={(made) => { setEditing(null); setAdded(made.slug); flow.connect(made); }}
                    onAdded={(made) => setAdded(made.slug)}
                />
            )}
            {flow.element}
        </div>
    );
}

/** What a link named that this pod has no connector for: an app from the
 *  catalog, or an installation of the organization's. Agents send people
 *  here for exactly that, so it is said as the thing to do, with the button
 *  that does it — connecting the person's own account, which the server
 *  installs the app for on the way where it may. */
function ConnectOffer({ pod, name, target, flow }: {
    pod: Pod;
    name: string;
    target: Extract<FocusTarget, { kind: "connect" }>;
    flow: ReturnType<typeof useConnectFlow>;
}) {
    /* A connector in shape only, so the flow that connects the pod's own can
       connect this one: keyed by the name the link used, which no pod
       connector has. */
    const stand: PodConnector = useMemo(() => ({
        id: "", slug: name, connectorId: target.connectorId, authConfigId: target.installId, installationName: "",
        mode: "PER_MEMBER", status: "READY", purpose: "", required: false, sharedAccount: "", sharedPodAccountId: "", sharedOwnerId: "",
        visibility: "POD", canUse: null,
    }), [name, target.connectorId, target.installId]);
    const offer = useRef<HTMLDivElement>(null);
    useEffect(() => { offer.current?.scrollIntoView({ block: "center" }); }, []);
    return (
        <div ref={offer} className="podconn__offer" role="region" aria-label={"Connect " + target.title}>
            <span className="podconn__what">
                <b>Connect {target.title} for {pod.name}</b>
                <span>Sign in with your own {target.title} account, so {pod.name} can work with it for you.</span>
                {flow.waiting === name && <span className="podconn__mine" role="status">Finish in the other tab…</span>}
                {flow.link?.slug === name && <SignInLink url={flow.link.url} app={flow.link.app} opened={flow.link.opened} />}
                {flow.problem?.slug === name && <span className="reachrow__error" role="alert">{flow.problem.text}</span>}
            </span>
            <button className="btn btn--primary" disabled={flow.pending} onClick={() => flow.connect(stand)}>
                {flow.busy === name ? <LoadingIndicator inline label="Opening sign-in" /> : "Connect"}
            </button>
        </div>
    );
}

function ConnectorRow({ pod, connector, title, owner, status, focused, may, usable, busy, pending, waiting, signIn, problem, onConnect, onEdit }: {
    pod: Pod;
    connector: PodConnector;
    title: string;
    owner: string;
    status: ReturnType<typeof myStatus>;
    focused: boolean;
    may: boolean;
    /** I could run calls through it once connected. A per-member one I
     *  could not use offers no Connect: an account nothing would use. */
    usable: boolean;
    busy: boolean;
    /** A sign-in is being fetched for some connector on this page. */
    pending: boolean;
    waiting: boolean;
    signIn: SignIn | null;
    problem: string;
    onConnect: (reconnect: boolean) => void;
    onEdit: () => void;
}) {
    const cache = useQueryClient();
    const row = useRef<HTMLLIElement>(null);
    const [confirming, setConfirming] = useState(false);
    useEffect(() => { if (focused) row.current?.scrollIntoView({ block: "center" }); }, [focused]);
    const remove = useMutation({
        mutationFn: () => source.removePodConnector(pod.id, connector.slug),
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: ["pod-connectors", pod.id] });
            void cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] });
        },
    });
    /* Asking is once a day per person on the server's side, so pressing it
       again is harmless: it says who was asked already. */
    const ask = useMutation({ mutationFn: () => source.askMembersToConnect(pod.id, connector.slug) });
    /* Who it waits on is its own request, per connector and only an editor's
       (it refuses anyone else), so it is asked for only where it is drawn.
       Keyed under the pod's connectors: whatever reads those again — a
       connect, a change — reads this again too. */
    const counted = wantsCoverage(connector, may);
    const covering = useQuery({
        queryKey: ["pod-connectors", pod.id, "coverage", connector.slug],
        queryFn: () => source.podConnectorCoverage(pod.id, connector.slug),
        enabled: counted,
        staleTime: 30_000,
    });
    const coverage = counted ? covering.data ?? null : null;
    const ok = status.standing === "connected";
    const asks = status.standing === "connect" || status.standing === "reconnect";
    const shut = asks && connector.mode === "PER_MEMBER" && !usable;
    return (
        <li ref={row} className="podconn__row" data-focused={focused || undefined}>
            <span className={"acct__dot" + (ok ? " acct__dot--ok" : "")} aria-hidden="true" />
            <span className="podconn__what">
                <span className="podconn__name">
                    {/* "support-inbox (Gmail)": the slug apps and agents call it
                        by, and its app when the slug does not already say it. */}
                    <b>{connector.slug}</b>
                    {connectorName(connector.slug, title) !== connector.slug && <span className="podconn__app">({title})</span>}
                    <span className="pill">{modeBadge(connector, owner)}</span>
                    {connector.required && <span className="pill">Everyone connects</span>}
                </span>
                {connector.purpose && <span className="podconn__purpose">{connector.purpose}</span>}
                {coverage && (
                    /* An editor's view: who of the people allowed to use it has
                       connected. Names, never their accounts. */
                    <span className="podconn__coverage">
                        <b>{coverageLine(coverage)}</b>
                        {waitingLine(coverage) && <span> · {waitingLine(coverage)}</span>}
                        {truncatedLine(coverage) && <span> · {truncatedLine(coverage)}</span>}
                        {coverage.waitingOn.length > 0 && (
                            <button className="linkish" disabled={ask.isPending} onClick={() => ask.mutate()}>
                                {ask.isPending ? "Asking…" : "Ask members to connect"}
                            </button>
                        )}
                        {ask.data && <span role="status"> {askedLine(ask.data)}</span>}
                        {ask.isError && <ProblemLine as="small" className="reachrow__error" error={ask.error} fallback="They could not be asked." />}
                    </span>
                )}
                {counted && covering.isPending && <span className="podconn__coverage" role="status">Counting who has connected…</span>}
                {counted && covering.isError && (
                    <span className="podconn__coverage">
                        <ProblemLine as="small" className="reachrow__error" error={covering.error} fallback="Couldn’t count who has connected." />
                        <button className="linkish" onClick={() => void covering.refetch()}>Try again</button>
                    </span>
                )}
                <span className={"podconn__mine" + (ok ? "" : " podconn__mine--needs")}>
                    {ok ? <CheckCircleIcon size={13} /> : <WarningIcon size={13} />} {shut ? "Not one you can use in " + pod.name : status.note || statusNote(connector)}
                </span>
                {waiting && <span className="podconn__mine" role="status">Finish in the other tab…</span>}
                {signIn && <SignInLink url={signIn.url} app={signIn.app} opened={signIn.opened} />}
                {problem && <span className="reachrow__error" role="alert">{problem}</span>}
                {remove.isError && <ProblemLine as="small" className="reachrow__error" error={remove.error} fallback="It could not be removed." />}
            </span>
            <span className="acct__acts">
                {asks && !shut && (
                    <button className="btn btn--primary" disabled={busy || pending} onClick={() => onConnect(status.standing === "reconnect")}>
                        {busy ? <LoadingIndicator inline label="Opening sign-in" /> : status.standing === "reconnect" ? "Reconnect" : "Connect"}
                    </button>
                )}
                {may && !confirming && <button className="linkish reachrow__quiet" onClick={onEdit}>Change</button>}
                {may && (confirming ? (
                    <span className="acct__confirm">
                        <span>Agents, apps and channels using {connector.slug} stop working.</span>
                        <button className="btn reachrow__drop" disabled={remove.isPending} onClick={() => remove.mutate()}>
                            {remove.isPending ? "Removing…" : "Remove"}
                        </button>
                        <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                    </span>
                ) : <button className="linkish reachrow__quiet" onClick={() => setConfirming(true)}>Remove</button>)}
            </span>
        </li>
    );
}

/** Adding a connector, or changing one. The installation is fixed once it
 *  exists — a different installation is a different connector — and so is
 *  the slug, which apps and agents already call it by.
 *
 *  Adding picks from the organization's installations, and from the
 *  catalog's apps it has none of yet: in a new organization the first list
 *  is empty, and a picker of only that was a dead end. One from the catalog
 *  is added by its id, and the server installs Lemma's own app for it — or,
 *  for somebody who may not install, says who can. A connector that runs as
 *  each person's own account then offers to connect mine at once, so one
 *  sitting ends with something that works. */
function ConnectorForm({ pod, editing, connectors, installs, catalog, accounts, titleOf, ownerName, onClose, onConnect, onAdded }: {
    pod: Pod;
    editing: PodConnector | null;
    connectors: PodConnector[];
    installs: Install[];
    catalog: Connector[];
    accounts: PodAccount[];
    titleOf: (connectorId: string) => string;
    ownerName: (userId: string) => string;
    onClose: () => void;
    /** Close, and start connecting my own account on the one just added. */
    onConnect: (made: PodConnector) => void;
    onAdded: (made: PodConnector) => void;
}) {
    const cache = useQueryClient();
    const choices = addChoices(installs, catalog, titleOf);
    const installed = choices.filter((one) => one.from === "install");
    const fromCatalog = choices.filter((one) => one.from === "catalog");
    /* Nothing picked until somebody picks: a slug suggested for an
       installation nobody chose is one they have to notice and undo. */
    const [choiceKey, setChoiceKey] = useState("");
    const picked = choices.find((one) => one.key === choiceKey) ?? null;
    const installId = editing?.authConfigId ?? picked?.installId ?? "";
    const [slug, setSlug] = useState(editing?.slug ?? "");
    const [slugTouched, setSlugTouched] = useState(Boolean(editing));
    const [mode, setMode] = useState<PodConnectorMode>(editing?.mode ?? "PER_MEMBER");
    const [purpose, setPurpose] = useState(editing?.purpose ?? "");
    const [required, setRequired] = useState(editing?.required ?? false);
    const [shared, setShared] = useState(editing?.sharedPodAccountId ?? "");
    const [made, setMade] = useState<PodConnector | null>(null);
    const taken = connectors.filter((one) => one.slug !== editing?.slug).map((one) => one.slug);
    /* Named for its app until somebody names it -- and nothing to suggest
       until there is an app to name it for. */
    const shownSlug = slugTouched ? slug : picked ? freshSlug(picked.connectorId, taken) : "";
    /* An app nobody installed has no accounts to share yet. */
    const shareable = installId ? shareableFor(accounts, installId) : [];
    const problem = editing || !picked ? "" : slugProblem(shownSlug, taken);
    const ready = Boolean(editing || picked);

    const save = useMutation({
        mutationFn: async (): Promise<PodConnector | null> => {
            if (editing) {
                const patch: PodConnectorPatch = connectorChanges(editing, { mode, purpose, required: mode === "PER_MEMBER" && required, sharedPodAccountId: mode === "SHARED" ? shared : "" });
                if (Object.keys(patch).length === 0) return null;
                await source.changePodConnector(pod.id, editing.slug, patch);
                return null;
            }
            if (!picked) return null;
            const saved = await source.addPodConnector(pod.id, {
                slug: shownSlug, authConfigId: picked.installId, connectorId: picked.connectorId, mode, sharedPodAccountId: shared, required, purpose,
            });
            return { ...saved, connectorId: saved.connectorId || picked.connectorId };
        },
        onSuccess: (saved) => {
            void cache.invalidateQueries({ queryKey: ["pod-connectors", pod.id] });
            void cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] });
            /* An app installed on the way is now one of the organization's. */
            if (picked?.from === "catalog") void cache.invalidateQueries({ queryKey: ["installs", pod.orgId] });
            if (saved) onAdded(saved);
            if (saved && saved.mode === "PER_MEMBER") { setMade(saved); return; }
            onClose();
        },
    });

    if (made) {
        const app = titleOf(made.connectorId);
        return (
            <Modal title={"Added " + made.slug} narrow onClose={onClose}>
                <div className="podconn__added">
                    <p className="connect-result" role="status">
                        <CheckCircleIcon size={18} /> {pod.name} can use {app} now, as each person’s own account.
                    </p>
                    <p>Connect your {app} account so it works for you. Everyone else connects theirs the first time {pod.name} needs it.</p>
                    <div className="record-form__actions">
                        <button className="btn btn--primary" onClick={() => onConnect(made)}>Connect yours</button>
                        <button className="btn" onClick={onClose}>Later</button>
                    </div>
                </div>
            </Modal>
        );
    }

    return (
        <Modal title={editing ? "Change " + editing.slug : "Add a connector to " + pod.name} narrow onClose={onClose}>
            <form className="record-form podconn__form" onSubmit={(event) => { event.preventDefault(); if (!problem && ready) save.mutate(); }}>
                <fieldset disabled={save.isPending} className="surface-setup__fieldset">
                    <label className="record-form__field">
                        <span>App</span>
                        {editing ? (
                            <select value={editing.authConfigId} disabled>
                                <option value={editing.authConfigId}>
                                    {installs.find((one) => one.id === editing.authConfigId)
                                        ? choices.find((one) => one.installId === editing.authConfigId)?.label
                                        : editing.installationName || titleOf(editing.connectorId)}
                                </option>
                            </select>
                        ) : (
                            <select value={choiceKey} onChange={(event) => { setChoiceKey(event.target.value); setShared(""); }}>
                                <option value="">{choices.length ? "Choose one…" : "Nothing to add yet"}</option>
                                {installed.length > 0 && (
                                    <optgroup label="Set up in the organization">
                                        {installed.map((one) => <option key={one.key} value={one.key}>{one.label}</option>)}
                                    </optgroup>
                                )}
                                {fromCatalog.length > 0 && (
                                    <optgroup label="More apps">
                                        {fromCatalog.map((one) => <option key={one.key} value={one.key}>{one.label}</option>)}
                                    </optgroup>
                                )}
                            </select>
                        )}
                        {!editing && (
                            <small>
                                {picked?.from === "catalog"
                                    ? "Set up for the organization as it is added, signing in with Lemma’s own app."
                                    : choices.length
                                        ? "Not here? An organization owner or editor sets other apps up from the organization menu → Connectors."
                                        : "An organization owner or editor sets apps up from the organization menu → Connectors."}
                            </small>
                        )}
                    </label>
                    <label className="record-form__field">
                        <span>Name apps and agents call it by</span>
                        <input value={shownSlug} disabled={Boolean(editing)} spellCheck={false}
                            onChange={(event) => { setSlugTouched(true); setSlug(event.target.value.toLowerCase()); }} />
                        {problem ? <small role="alert">{problem}</small> : <small>Fixed once it is made: apps, agents and bundles name it by this.</small>}
                    </label>
                    <div className="record-form__field" role="radiogroup" aria-label="Whose account it runs as">
                        <span>Whose account it runs as</span>
                        <label className="smanage__check">
                            <input type="radio" name="podconn-mode" checked={mode === "PER_MEMBER"} onChange={() => setMode("PER_MEMBER")} />
                            <span><span className="smanage__label">Each person’s own</span><small>Whoever is asking, or the person an agent works for.</small></span>
                        </label>
                        <label className="smanage__check">
                            <input type="radio" name="podconn-mode" checked={mode === "SHARED"} onChange={() => setMode("SHARED")} />
                            <span><span className="smanage__label">One shared account</span><small>The same account for everyone, an account its owner shared here.</small></span>
                        </label>
                    </div>
                    {mode === "SHARED" && (
                        <label className="record-form__field">
                            <span>Shared account</span>
                            <select value={shared} onChange={(event) => setShared(event.target.value)}>
                                <option value="">None yet — it waits for one</option>
                                {shareable.map((one) => (
                                    <option key={one.id} value={one.id}>
                                        {(one.label || titleOf(one.connectorId)) + (ownerName(one.ownerId) ? " · " + ownerName(one.ownerId) : "")}
                                    </option>
                                ))}
                            </select>
                            <small>
                                {shareable.length
                                    ? "Only accounts their owners shared with " + pod.name + " are here."
                                    : "Nobody has shared an account on this connector with " + pod.name + " yet. Its owner shares it, under “Your accounts in " + pod.name + "” on this page."}
                            </small>
                        </label>
                    )}
                    {mode === "PER_MEMBER" && (
                        <label className="smanage__check">
                            <input type="checkbox" checked={required} onChange={(event) => setRequired(event.target.checked)} />
                            <span><span className="smanage__label">Ask everyone to connect theirs</span><small>People without one see a line asking them, when they open {pod.name}.</small></span>
                        </label>
                    )}
                    <label className="record-form__field">
                        <span>What it is for</span>
                        <textarea rows={2} maxLength={500} value={purpose} placeholder="Shown when someone is asked to connect"
                            onChange={(event) => setPurpose(event.target.value)} />
                    </label>
                </fieldset>
                {save.isError && <ProblemLine className="library-problem" error={save.error} fallback="That could not be saved." />}
                <div className="record-form__actions">
                    <button className="btn btn--primary" type="submit" disabled={save.isPending || Boolean(problem) || !ready}>
                        {save.isPending ? "Saving…" : editing ? "Save" : "Add"}
                    </button>
                    <button className="btn" type="button" onClick={onClose}>Cancel</button>
                </div>
            </form>
        </Modal>
    );
}

/** The accounts of mine in use here, and what only I can do with them:
 *  share with everyone or some people, withdraw, share as a connector in one
 *  step, take one out — and bring one of my other accounts in. */
function MyAccountsHere({ pod, me, may, accounts, reading, connectors, titleOf, orgAccounts, installs }: {
    pod: Pod;
    me: string;
    /** `pod.connector.manage`: declaring or pointing a connector, which
     *  using an account for everyone needs. */
    may: boolean;
    accounts: PodAccount[];
    reading: { isPending: boolean; isError: boolean; refetch: () => unknown };
    connectors: PodConnector[];
    titleOf: (connectorId: string) => string;
    orgAccounts: { id: string; connectorId: string; label: string; usable: boolean }[];
    installs: Install[];
}) {
    const cache = useQueryClient();
    const mine = ownedBy(accounts, me);
    const [adding, setAdding] = useState("");
    const outside = orgAccounts.filter((one) => !accounts.some((held) => held.accountId === one.id));
    const add = useMutation({
        mutationFn: (accountId: string) => source.addPodAccount(pod.id, accountId),
        onSuccess: () => { setAdding(""); void cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] }); },
    });
    return (
        <section className="podconn__section" aria-labelledby="podconn-mine">
            <header className="podconn__head">
                <h2 id="podconn-mine">Your accounts in {pod.name}</h2>
            </header>
            <p className="aboutpage__quiet">
                Only your own calls use them unless you share one. Sharing is yours alone: nobody else can put your account in front of {pod.name}.
            </p>
            {reading.isPending && <p className="aboutpage__quiet" role="status">Reading your accounts here…</p>}
            {reading.isError && (
                <p className="aboutpage__quiet" role="alert">
                    Couldn’t read your accounts here. <button className="linkish" onClick={() => void reading.refetch()}>Try again</button>
                </p>
            )}
            {!reading.isPending && !reading.isError && mine.length === 0 && (
                <p className="aboutpage__quiet">None yet. One is added the first time {pod.name} uses your account for you.</p>
            )}
            {mine.length > 0 && (
                <ul className="podconn__list">
                    {mine.map((account) => (
                        <MyAccountRow key={account.id} pod={pod} may={may} account={account} connectors={connectors} title={titleOf(account.connectorId)}
                            installName={installs.find((one) => one.id === account.authConfigId)?.name ?? ""} />
                    ))}
                </ul>
            )}
            {outside.length > 0 && (
                <form className="podconn__bring" onSubmit={(event) => { event.preventDefault(); if (adding) add.mutate(adding); }}>
                    <label>
                        <span>Bring another of yours in</span>
                        <select value={adding} onChange={(event) => setAdding(event.target.value)}>
                            <option value="">Choose an account…</option>
                            {outside.map((one) => (
                                <option key={one.id} value={one.id} disabled={!one.usable}>
                                    {titleOf(one.connectorId)}{one.label ? " — " + one.label : ""}{one.usable ? "" : " (not usable yet)"}
                                </option>
                            ))}
                        </select>
                    </label>
                    <button className="btn" type="submit" disabled={!adding || add.isPending}>{add.isPending ? "Adding…" : "Add"}</button>
                    {add.isError && <ProblemLine as="small" className="reachrow__error" error={add.error} fallback="It could not be added." />}
                </form>
            )}
        </section>
    );
}

function MyAccountRow({ pod, may, account, connectors, title, installName }: {
    pod: Pod; may: boolean; account: PodAccount; connectors: PodConnector[]; title: string;
    /** Two accounts of one app are told apart by the installation each is on. */
    installName: string;
}) {
    const cache = useQueryClient();
    const me = useMe() ?? "";
    const [mode, setMode] = useState<"idle" | "some" | "as" | "remove" | "withdraw">("idle");
    /* Grantees, as `TYPE:id`: a member, or everyone holding one of the
       pod's roles. */
    const [picked, setPicked] = useState<string[]>([]);
    const [slug, setSlug] = useState(() => suggestedSlug(account, connectors));
    const people = useMemo(() => pod.members.filter((one) => one.kind === "person" && one.userId !== me), [pod.members, me]);
    const roles = useQuery({
        queryKey: ["share-roles", pod.id],
        queryFn: () => source.listShareRoles(pod.id),
        enabled: mode === "some",
        staleTime: 5 * 60_000,
    });
    const toggle = (key: string, on: boolean) => setPicked((was) => (on ? [...was, key] : was.filter((one) => one !== key)));
    const grantees = (): Grantee[] => picked.map((key) => {
        const [type, ...rest] = key.split(":");
        return { type: type === "ROLE" ? "ROLE" : "POD_MEMBER", id: rest.join(":") };
    });
    const refresh = () => {
        void cache.invalidateQueries({ queryKey: ["pod-accounts", pod.id] });
        void cache.invalidateQueries({ queryKey: ["pod-connectors", pod.id] });
    };
    const change = useMutation({
        mutationFn: (patch: PodAccountPatch) => source.changePodAccount(pod.id, account.id, patch),
        onSuccess: (saved) => {
            setMode("idle");
            /* The answer is the account as saved: drawn at once, so the
               sandbox switch does not spring back until the list is read
               again. */
            if (saved) cache.setQueryData<PodAccount[]>(["pod-accounts", pod.id], (was) => was?.map((one) => (one.id === saved.id ? saved : one)));
            refresh();
        },
    });
    /* What the change could not do at once — turning sandbox use off leaves
       a copy in the sandboxes already given one, until they are swept. Kept
       from the answer, since a listing never carries it. */
    const notice = change.data?.notice ?? "";
    /* One request: the server checks it all before it changes anything, and
       its refusals are typed, with a connect link when one helps. */
    const shareAs = useMutation({
        mutationFn: () => source.shareAccountAs(pod.id, account.id, slug.trim(), "Shared by its owner"),
        onSuccess: () => { setMode("idle"); refresh(); },
    });
    const remove = useMutation({
        mutationFn: () => source.removePodAccount(pod.id, account.id),
        onSuccess: refresh,
    });
    /* Only the slug's spelling is checked here. Whether it is free, or a
       SHARED connector on this installation, is the server's to say. */
    const slugSaid = slugProblem(slug.trim(), []);
    const busy = change.isPending || shareAs.isPending || remove.isPending;
    const failed = change.error ?? remove.error;

    return (
        <li className="podconn__row">
            <span className={"acct__dot" + (account.visibility !== "PERSONAL" ? " acct__dot--ok" : "")} aria-hidden="true" />
            <span className="podconn__what">
                <span className="podconn__name">
                    <b>{account.label || "Your " + title + " account"}</b>
                    <span className="podconn__app">{installName ? title + " · " + installName : title}</span>
                    <span className="pill">{visibilityWord(account.visibility)}</span>
                    {account.isMemberDefault && <span className="pill">your default here</span>}
                </span>
                {account.usedBy.length > 0 && <span className="podconn__purpose">Runs {account.usedBy.join(", ")} for everyone here</span>}
                {/* Only a shared account can be lent to a sandbox; the server
                    refuses it on one only its owner uses. */}
                {account.sandboxes !== null && account.visibility !== "PERSONAL" && (
                    <label className="smanage__check podconn__sandbox">
                        <input type="checkbox" role="switch" checked={account.sandboxes} disabled={busy}
                            onChange={(event) => change.mutate({ sandboxes: event.target.checked })} />
                        <span><span className="smanage__label">Allow use inside sandboxes</span>
                            <small>Agents of other members can then use a copy of this account’s token inside their sandboxes, for git and gh. Off until you turn it on; withdrawing turns it off.</small></span>
                    </label>
                )}
                {notice && <span className="podconn__purpose" role="status">{notice}</span>}
                {mode === "some" && (
                    <fieldset className="podconn__people">
                        <legend>Who may use it</legend>
                        {people.length === 0 && <small>Nobody else is in {pod.name} yet.</small>}
                        {people.map((person) => (
                            <label key={person.id} className="smanage__check">
                                <input type="checkbox" checked={picked.includes("POD_MEMBER:" + person.id)}
                                    onChange={(event) => toggle("POD_MEMBER:" + person.id, event.target.checked)} />
                                <span>{person.name}</span>
                            </label>
                        ))}
                        {(roles.data ?? []).length > 0 && <small>Or everyone who holds a role here, now and later:</small>}
                        {(roles.data ?? []).map((role) => (
                            <label key={role.id} className="smanage__check">
                                <input type="checkbox" checked={picked.includes("ROLE:" + role.id)}
                                    onChange={(event) => toggle("ROLE:" + role.id, event.target.checked)} />
                                <span>{role.label}</span>
                            </label>
                        ))}
                        {roles.isError && <small>The pod’s roles could not be read; you can still pick people.</small>}
                        <span className="acct__confirm">
                            <button className="btn btn--primary" disabled={busy || picked.length === 0}
                                onClick={() => change.mutate({ visibility: "RESTRICTED", shareWith: grantees() })}>
                                Share with {picked.length || "them"}
                            </button>
                            <button className="linkish" onClick={() => setMode("idle")}>Cancel</button>
                        </span>
                    </fieldset>
                )}
                {mode === "as" && (
                    <form className="podconn__as" onSubmit={(event) => { event.preventDefault(); if (!slugSaid) shareAs.mutate(); }}>
                        <label>
                            <span>Use this for everyone in {pod.name} as</span>
                            <input value={slug} spellCheck={false} onChange={(event) => setSlug(event.target.value.toLowerCase())} />
                        </label>
                        <button className="btn btn--primary" type="submit" disabled={busy || Boolean(slugSaid)}>{shareAs.isPending ? "Saving…" : "Use it"}</button>
                        <button className="linkish" type="button" onClick={() => setMode("idle")}>Cancel</button>
                        <small>{slugSaid || "Everyone in " + pod.name + " can use this account, and every call through " + slug.trim() + " runs as it."}</small>
                        {shareAs.isError && <ProblemLine as="small" className="reachrow__error" error={shareAs.error} fallback="It could not be shared." />}
                    </form>
                )}
                {mode === "withdraw" && (
                    <span className="acct__confirm">
                        <span>{account.usedBy.join(", ")} stop working until someone shares another account.</span>
                        <button className="btn reachrow__drop" disabled={busy} onClick={() => change.mutate({ visibility: "PERSONAL" })}>{change.isPending ? "Withdrawing…" : "Withdraw"}</button>
                        <button className="linkish" onClick={() => setMode("idle")}>Keep sharing</button>
                    </span>
                )}
                {mode === "remove" && (
                    <span className="acct__confirm">
                        <span>{account.usedBy.length ? account.usedBy.join(", ") + " stop working until someone shares another. " : ""}Your own account stays connected to the organization.</span>
                        <button className="btn reachrow__drop" disabled={busy} onClick={() => remove.mutate()}>{remove.isPending ? "Removing…" : "Take it out"}</button>
                        <button className="linkish" onClick={() => setMode("idle")}>Keep</button>
                    </span>
                )}
                {failed && <ProblemLine as="small" className="reachrow__error" error={failed} fallback="That could not be changed." />}
            </span>
            {mode === "idle" && (
                <span className="acct__acts">
                    {account.visibility !== "POD" && <button className="linkish reachrow__quiet" disabled={busy} onClick={() => change.mutate({ visibility: "POD" })}>Share with everyone</button>}
                    {account.visibility !== "RESTRICTED" && <button className="linkish reachrow__quiet" disabled={busy} onClick={() => { setPicked([]); setMode("some"); }}>Share with some people</button>}
                    {may && <button className="linkish reachrow__quiet" disabled={busy} onClick={() => setMode("as")}>Use for everyone as…</button>}
                    {account.visibility !== "PERSONAL" && <button className="linkish reachrow__quiet" disabled={busy} onClick={() => account.usedBy.length ? setMode("withdraw") : change.mutate({ visibility: "PERSONAL" })}>Withdraw</button>}
                    <button className="linkish reachrow__quiet" disabled={busy} onClick={() => setMode("remove")}>Remove</button>
                </span>
            )}
        </li>
    );
}
