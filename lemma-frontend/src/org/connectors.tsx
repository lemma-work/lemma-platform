import { LoadingIndicator } from "@/ui/loading";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { accountName, accountTrouble, type Connector, type ConnectorAccount } from "@/data";
import { CheckCircleIcon, ExternalIcon, PlusIcon, RefreshIcon, SearchIcon, WarningIcon } from "@/ui/icons";
import { ConnectDialog, RotateDialog } from "@/connect/connect-dialog";
import { AddConnector } from "@/connect/add-connector";
import {
    completionPath, hereWith, openAuthorization, outcomeNote, useConnectOutcome, type ConnectOutcome,
} from "@/connect/round-trip";
import {
    useBindInstallation, useConnectorRefresh, useDeleteInstall, useFinishInstall, useInstalls, useMakeDefaultInstall,
    useMayInstall, useRefreshOperations, type InstallationChoice,
} from "@/connect/queries";
import {
    connectorProblem, connectRoute, discoveryNote, isBringYourOwn, kindFor, type CatalogEntry, type Install,
} from "@/connect/install";
import { SetUpOnThisMac } from "@/desktop/set-up-on-this-mac";
import { oauthFormForConnector } from "@/desktop/this-mac";

/** The organization's connected accounts.
 *
 *  Not a grid of logos and a count. That shape wants `name`, `slug`, `logo`
 *  and `connected_accounts_count`, none of which the API sends: every name
 *  falls back to the raw connector id and no logo ever loads, which looks like
 *  an unfinished component and is really a finished one reading a shape that
 *  does not exist.
 *
 *  What an org actually needs to know here is not which connectors exist —
 *  there are hundreds, and the list alone answers nothing. It is *which ones
 *  we have accounts on, and are those accounts working*. So connected ones
 *  come first, with their accounts named and their trouble spelled out, and
 *  the rest of the catalog is behind a search for when you are adding one. */

function Logo({ connector }: { connector: Connector }) {
    if (!connector.icon) {
        return <span className="connector__logo connector__logo--blank">{connector.title.slice(0, 1)}</span>;
    }
    return <img className="connector__logo" src={connector.icon} alt="" aria-hidden="true" />;
}

function AccountRow({
    account,
    connector,
    orgId,
    onGone,
    onRotate,
    onReconnect,
    reconnecting = false,
}: {
    account: ConnectorAccount;
    connector: Connector;
    orgId: string;
    onGone: () => void;
    /** Replace the credential in place. Absent where the connector has no
     *  credential to replace — an OAuth account is re-authorised, not retyped. */
    onRotate?: () => void;
    /** Sign in again on the same install. Only for an OAuth account whose
     *  token stopped working: the backend refreshes the existing row by the
     *  provider's own id, so the account and everything pinned to it stay. */
    onReconnect?: () => void;
    /** Fetching the sign-in address for this account. */
    reconnecting?: boolean;
}) {
    const [confirming, setConfirming] = useState(false);
    const drop = useMutation({
        mutationFn: () => source.disconnectAccount(orgId, account.id),
        onSuccess: onGone,
    });
    const trouble = accountTrouble(account);
    const dead = ["REAUTH_REQUIRED", "DISCONNECTED"].includes(account.status.toUpperCase());
    /* Connected and reaching nothing: a GitHub App authorised but not
       installed, or installed in more than one place. Reconnecting cannot
       help either; installing, or choosing, can. */
    const unfinished = account.status.toUpperCase() === "CONNECTED"
        && ["INSTALL_REQUIRED", "CHOOSE_INSTALL"].includes(account.installState.toUpperCase());
    const finish = useFinishInstall(orgId, () => completionPath(hereWith({ settings: "connectors" })));
    const bind = useBindInstallation(orgId);
    const [choices, setChoices] = useState<InstallationChoice[] | null>(null);
    const [installNote, setInstallNote] = useState<string | null>(null);
    const startFinish = () => {
        setInstallNote(null);
        finish.mutate(account.id, {
            onSuccess: (step) => {
                if ("done" in step) { setInstallNote("Installation found."); onGone(); }
                else if ("choices" in step) setChoices(step.choices);
                else if (!step.url) setInstallNote("GitHub offered no install page.");
                else if (openAuthorization(step.url)) setInstallNote("Finish in the GitHub tab…");
                else window.location.assign(step.url);
            },
            onError: (problem) => setInstallNote(connectorProblem(problem, "The installation could not be started.")),
        });
    };

    return (
        <li className="acct">
            <span className={"acct__dot" + (account.usable ? " acct__dot--ok" : "")} aria-hidden="true" />
            <span className="acct__who">
                {accountName(account, connector.title)}
                {/* The provider id is a disambiguator, not a name — shown
                    beside the name rather than instead of it. */}
                {account.ref && <span className="acct__ref">{account.ref}</span>}
                {account.isDefault && <span className="pill">default</span>}
            </span>
            {/* Four states, not a boolean: each unfinished one needs a
                different thing from the person, and "reconnect" is advice
                that cannot succeed when you are waiting on an owner. */}
            {trouble && !installNote && <span className="acct__trouble"><WarningIcon size={13} /> {trouble}</span>}
            {installNote && <span className="acct__trouble acct__trouble--quiet" role="status">{installNote}</span>}
            {choices ? (
                <span className="acct__confirm">
                    {choices.length === 0 && <span className="acct__trouble acct__trouble--quiet">No installations to choose from.</span>}
                    {choices.map((choice) => (
                        <button key={choice.installation_id} className="btn" disabled={bind.isPending}
                            onClick={() => bind.mutate({ accountId: account.id, installationId: choice.installation_id }, {
                                onSuccess: () => { setChoices(null); onGone(); },
                                onError: (problem) => setInstallNote(connectorProblem(problem, "That installation could not be used.")),
                            })}>
                            {choice.account_login || choice.installation_id}
                        </button>
                    ))}
                    <button className="linkish" onClick={() => setChoices(null)}>Cancel</button>
                </span>
            ) : confirming ? (
                <span className="acct__confirm">
                    <button className="btn reachrow__drop" disabled={drop.isPending} onClick={() => drop.mutate()}>
                        {drop.isPending ? "Removing…" : "Remove"}
                    </button>
                    <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                </span>
            ) : (
                <span className="acct__acts">
                    {unfinished && (
                        <button className="linkish reachrow__quiet" disabled={finish.isPending} onClick={startFinish}>
                            {finish.isPending ? <LoadingIndicator inline label="Checking the installation" />
                                : account.installState.toUpperCase() === "CHOOSE_INSTALL" ? "Choose installation" : "Install"}
                        </button>
                    )}
                    {dead && onReconnect && (
                        <button className="linkish reachrow__quiet" disabled={reconnecting} onClick={onReconnect}>
                            {reconnecting ? <LoadingIndicator inline label="Opening sign-in" /> : "Reconnect"}
                        </button>
                    )}
                    {/* Before Remove, and worded as the smaller act it is: a
                        rotated credential keeps the account, and a removed one
                        takes every schedule and surface pinned to it. */}
                    {onRotate && (
                        <button className="linkish reachrow__quiet" onClick={onRotate}>
                            Replace credential
                        </button>
                    )}
                    <button className="linkish reachrow__quiet" onClick={() => setConfirming(true)}>
                        Remove
                    </button>
                </span>
            )}
        </li>
    );
}

/** One install of a connector — an auth config, in the API's words.
 *
 *  A connector is a catalogue row; an install is this organization's copy of
 *  it, with its own OAuth app or its own address; accounts hang off an install
 *  and never off the connector. `mcp` is one catalogue row for every MCP server
 *  anybody adds, and an organization may hold Gmail twice — on Lemma's app and
 *  on its own — so where there is more than one, or where the install *is* the
 *  thing somebody set up, the installs are the real list.
 */
function InstallRow({ install, orgId, addressed, manage, needsSignIn, signingIn = false, onConnect, onChanged }: {
    install: Install;
    /** Fetching the sign-in address for this install. */
    signingIn?: boolean;
    orgId: string;
    /** May change the install itself — owners and editors. Anybody may
     *  connect an account on it. */
    manage: boolean;
    /** Pointed somewhere by the organization: its operations are discovered
     *  per install, so re-reading them means something. */
    addressed: boolean;
    /** Signed into through a browser, with nobody signed in yet. */
    needsSignIn: boolean;
    /** Connect an account against this install in particular. */
    onConnect?: () => void;
    onChanged: () => void;
}) {
    const [said, setSaid] = useState<string | null>(null);
    const [confirming, setConfirming] = useState(false);
    const reread = useRefreshOperations(orgId);
    const drop = useDeleteInstall(orgId);
    const promote = useMakeDefaultInstall(orgId);
    const off = install.status === "DISABLED";

    return (
        <li className="acct">
            <span className={"acct__dot" + (off || needsSignIn ? "" : " acct__dot--ok")} aria-hidden="true" />
            <span className="acct__who">
                {install.name || install.id}
                <span className="acct__ref">{install.config_source === "ORG_CUSTOM" && !addressed ? "your app" : install.kind}</span>
                {install.is_default && <span className="pill">default</span>}
            </span>
            {needsSignIn && !said && <span className="acct__trouble"><WarningIcon size={13} /> Nobody has signed in yet</span>}
            {said && <span className="acct__trouble acct__trouble--quiet" role="status">{said}</span>}
            {confirming ? (
                <span className="acct__confirm">
                    <button className="btn reachrow__drop" disabled={drop.isPending}
                        onClick={() => drop.mutate(install, { onSuccess: onChanged })}>
                        {drop.isPending ? "Removing…" : "Remove, with its accounts"}
                    </button>
                    <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                </span>
            ) : (
                <span className="acct__acts">
                    {onConnect && !off && (
                        <button className="linkish reachrow__quiet" disabled={signingIn} onClick={onConnect}>
                            {signingIn ? <LoadingIndicator inline label="Opening sign-in" /> : needsSignIn ? "Sign in" : "Connect an account"}
                        </button>
                    )}
                    {manage && !install.is_default && !off && (
                        <button className="linkish reachrow__quiet" disabled={promote.isPending}
                            onClick={() => promote.mutate(install, {
                                onSuccess: onChanged,
                                onError: () => setSaid("It could not be made the default just now."),
                            })}>
                            Make default
                        </button>
                    )}
                    {manage && addressed && (
                        <button className="linkish reachrow__quiet" disabled={reread.isPending}
                            onClick={() => reread.mutate(install, {
                                onSuccess: (answer) => { setSaid(discoveryNote(answer.status, answer.operation_count, answer.error)); onChanged(); },
                                onError: () => setSaid("Its operations could not be read just now."),
                            })}>
                            {reread.isPending ? "Reading…" : "Re-read operations"}
                        </button>
                    )}
                    {manage && <button className="linkish reachrow__quiet" onClick={() => setConfirming(true)}>Remove</button>}
                </span>
            )}
        </li>
    );
}

function ConnectorCard({
    connector,
    accounts,
    installs,
    takenNames,
    orgId,
    returned,
    mayInstall,
    onChanged, onAdd, brief = false }: {
    connector: Connector;
    accounts: ConnectorAccount[];
    /** This organization's installs of this connector. */
    installs: Install[];
    /** Every install name in the organization, for naming a new one. */
    takenNames: string[];
    orgId: string;
    /** Bumped each time a round trip reports back, so a card still waiting
     *  on its provider stops waiting. */
    returned: number;
    /** Owner or editor: may make an install. `null` when that is not known,
     *  which offers everything and lets the backend decide. */
    mayInstall: boolean | null;
    onChanged: () => void;
    /** Opens the add flow. An entry that stands for many servers has nothing
     *  to connect against until one exists, so for those this is the button. */
    onAdd: () => void;
    brief?: boolean }) {
    /* The provider's page, once asked for. `opened` is whether it is already
       open in a tab; when the browser refused, the link is the way there. */
    const [link, setLink] = useState<{ authorizeUrl: string; opened: boolean } | null>(null);
    useEffect(() => { if (returned) setLink(null); }, [returned]);
    const [error, setError] = useState<string | null>(null);
    /* Opening the dialog rather than starting a flow: which of the two routes
       this connector is on is a question about its kinds, and the dialog is
       what reads them. The alternative is for the card to guess — ask for an
       authorize URL, and give up when there is not one. */
    const [connecting, setConnecting] = useState<Install | null | undefined>(undefined);
    const [rotating, setRotating] = useState<ConnectorAccount | null>(null);

    const start = useMutation({
        mutationFn: ({ installId, connectionFields }: { installId: string | null; connectionFields?: Record<string, unknown> }) =>
            source.startAccount(
                orgId, connector.id, installId ?? undefined,
                /* Back to this panel, whichever way the tab comes home. */
                completionPath(hereWith({ settings: "connectors" })),
                connectionFields,
            ),
        onSuccess: (started) => {
            setConnecting(undefined);
            if (!started.authorizeUrl) {
                setLink(null);
                setError("This one offered no way to sign in. It may need an app of your own first.");
            } else {
                /* Straight there. The link it used to show was a second click
                   for the same intent, and it opened with `noreferrer` — so the
                   finished tab could not report back and the app reloaded
                   inside it. */
                setLink({ authorizeUrl: started.authorizeUrl, opened: openAuthorization(started.authorizeUrl) });
            }
            /* An install may have been made on the way. */
            onChanged();
        },
        onError: (problem) => setError(connectorProblem(problem, "That could not be started.")),
    });

    /* The catalogue entry, in the shape the dialog reads. The two models are
       the same row seen by two callers — this view has always wanted a title
       and a logo, and the dialog wants the kinds. */
    const entry: CatalogEntry = {
        id: connector.id, title: connector.title, description: connector.description, icon: connector.icon,
        kinds: connector.kinds,
    };
    /* `mcp` is a catalogue row, not a server. Until this organization has
       pointed it somewhere there is nothing to authorise against, and adding
       another means another server — never a second account on the first,
       which would point it at the first one's address. */
    const addressed = isBringYourOwn(entry);
    const installFor = (account: ConnectorAccount) => installs.find((one) => one.id === account.authConfigId) ?? null;
    const credentialed = (install: Install | null) => connectRoute(install, kindFor(entry, install)) === "credentials";
    /* Grouped by install where the installs are worth naming: several of
       them, or ones the organization set up itself. One Gmail install behind
       one Gmail account is noise. */
    const grouped = addressed || installs.length > 1;

    const accountRow = (account: ConnectorAccount) => {
        const install = installFor(account);
        return (
            <AccountRow
                key={account.id}
                account={account}
                connector={connector}
                orgId={orgId}
                onGone={onChanged}
                onRotate={install && credentialed(install) ? () => setRotating(account) : undefined}
                onReconnect={install && !credentialed(install) ? () => start.mutate({ installId: install.id }) : undefined}
                reconnecting={start.isPending && start.variables?.installId === install?.id}
            />
        );
    };

    return (
        <div className={"connector" + (brief ? " connector--brief" : "")} data-on={accounts.length > 0 || installs.length > 0 ? "" : undefined}>
            <Logo connector={connector} />
            <div className="connector__body">
                <span className="connector__name">{connector.title}</span>
                {connector.description && <span className="connector__blurb">{connector.description}</span>}
                {error && <span className="reachrow__error">{error}</span>}
            </div>
            <div className="connector__acts">
                {link ? (
                    <>
                        {link.opened ? (
                            <span className="acct__trouble acct__trouble--quiet" role="status">
                                Finish in the {connector.title} tab…
                            </span>
                        ) : (
                            <button className="btn btn--primary"
                                onClick={() => setLink({ ...link, opened: openAuthorization(link.authorizeUrl) })}>
                                Authorise <ExternalIcon size={13} />
                            </button>
                        )}
                        <button className="linkish" onClick={() => { setLink(null); onChanged(); }}>
                            <RefreshIcon size={13} /> Done?
                        </button>
                    </>
                ) : mayInstall === false && (addressed || !installs.some((one) => one.status !== "DISABLED")) ? (
                    /* Nothing this person can connect against, and not theirs
                       to make. Said up front rather than as the 404 a click
                       would earn. */
                    <span className="acct__trouble acct__trouble--quiet">An owner or editor has to set this up</span>
                ) : addressed ? (
                    <button className="btn" onClick={onAdd}>{installs.length > 0 ? "Add another" : "Add one"}</button>
                ) : (
                    <>
                        {/* Beside Connect rather than instead of it: an
                            organization's own OAuth app still works without
                            this computer's, so this is the other way in,
                            offered only while that form is empty. */}
                        {accounts.length === 0 && <SetUpOnThisMac form={oauthFormForConnector(connector.id)} compact />}
                        <button className="btn" disabled={start.isPending}
                            onClick={() => { setError(null); setConnecting(installs.find((one) => one.is_default && one.status !== "DISABLED") ?? installs.find((one) => one.status !== "DISABLED") ?? null); }}>
                            {start.isPending ? <LoadingIndicator inline label="Loading" /> : accounts.length > 0 ? "Add another" : "Connect"}
                        </button>
                    </>
                )}
            </div>

            {/* A row of the connector's own grid rather than a child of its
                middle column: nested in the body, an account's actions stopped
                where the description stopped, which is a couple of hundred
                pixels short of the button they sit under. */}
            {grouped ? (
                <>
                    {installs.map((install) => {
                        const on = accounts.filter((account) => account.authConfigId === install.id);
                        const signIn = !credentialed(install) && !on.some((account) => account.usable);
                        return (
                            <ul className="accts" key={install.id}>
                                <InstallRow
                                    install={install}
                                    orgId={orgId}
                                    addressed={addressed}
                                    manage={mayInstall !== false}
                                    needsSignIn={addressed && signIn}
                                    signingIn={start.isPending && start.variables?.installId === install.id && connecting === undefined}
                                    onConnect={
                                        /* An addressed install connects its
                                           one account when it is added; the
                                           only thing left to do is sign in. */
                                        addressed
                                            ? signIn ? () => start.mutate({ installId: install.id }) : undefined
                                            : () => { setError(null); setConnecting(install); }
                                    }
                                    onChanged={onChanged}
                                />
                                {on.map(accountRow)}
                            </ul>
                        );
                    })}
                    {/* Accounts whose install is not in the list — an install
                        read before it was made, most likely. Shown, not
                        dropped: they are real and can be removed. */}
                    {accounts.some((account) => !installFor(account)) && (
                        <ul className="accts">{accounts.filter((account) => !installFor(account)).map(accountRow)}</ul>
                    )}
                </>
            ) : accounts.length > 0 && (
                <ul className="accts">{accounts.map(accountRow)}</ul>
            )}

            {connecting !== undefined && (
                <ConnectDialog
                    orgId={orgId}
                    connector={entry}
                    install={connecting}
                    takenNames={takenNames}
                    mayInstall={mayInstall}
                    authorizing={start.isPending}
                    authorizeFailure={error}
                    onClose={() => setConnecting(undefined)}
                    onDone={() => { setConnecting(undefined); onChanged(); }}
                    onAuthorize={(installId, connectionFields) => start.mutate({ installId, connectionFields })}
                />
            )}
            {rotating && (
                <RotateDialog
                    orgId={orgId}
                    connector={entry}
                    install={installFor(rotating) ?? installs[0] ?? null}
                    accountId={rotating.id}
                    accountName={accountName(rotating, connector.title)}
                    onClose={() => setRotating(null)}
                    onDone={() => { setRotating(null); onChanged(); }}
                />
            )}
        </div>
    );
}

/** Which slice of the catalog is being looked at.
 *
 *  One list with a stated filter, rather than a few suggestions stacked on top
 *  of what is already connected. Two unlabelled lists meant a row saying
 *  "Connect" sat directly above one saying "Add another" with nothing to
 *  explain why — it read as a single list behaving at random.
 */
type Slice = "connected" | "available" | "trouble";

export function ConnectorsSection({ orgId }: { orgId: string }) {
    const queryClient = useQueryClient();
    const [query, setQuery] = useState("");
    /* Null until somebody picks, so the default can depend on what is there. */
    const [slice, setSlice] = useState<Slice | null>(null);

    const [adding, setAdding] = useState(false);
    const [heard, setHeard] = useState<ConnectOutcome | null>(null);
    const [returned, setReturned] = useState(0);

    const connectors = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors() });
    const accounts = useQuery({ queryKey: ["accounts", orgId], queryFn: () => source.listAccounts(orgId) });
    const installs = useInstalls(orgId);
    const mayInstall = useMayInstall(orgId);
    const invalidate = useConnectorRefresh(orgId);

    const refresh = () => {
        invalidate();
        void queryClient.invalidateQueries({ queryKey: ["accounts", orgId] });
        void queryClient.invalidateQueries({ queryKey: ["connectors"] });
    };

    useConnectOutcome(
        (outcome) => { setHeard(outcome); setReturned((n) => n + 1); refresh(); },
        refresh,
    );

    const byInstall = useMemo(() => {
        const map = new Map<string, Install[]>();
        for (const install of installs.data ?? []) {
            map.set(install.connector_id, [...(map.get(install.connector_id) ?? []), install]);
        }
        return map;
    }, [installs.data]);

    const byConnector = useMemo(() => {
        const map = new Map<string, ConnectorAccount[]>();
        for (const account of accounts.data ?? []) {
            map.set(account.connectorId, [...(map.get(account.connectorId) ?? []), account]);
        }
        return map;
    }, [accounts.data]);

    const all = connectors.data ?? [];
    const takenNames = useMemo(() => (installs.data ?? []).map((install) => install.name), [installs.data]);
    /* Connected means an account, with one exception: a server this
       organization added *is* the connection, so for the entries pointed
       somewhere an install counts on its own — counting accounts alone filed
       every MCP server and database under "available".

       Only for those. An install of Canva or Gmail with nobody on it is an app
       that was enabled, not one anybody connected: a sign-in abandoned
       half-way leaves exactly that behind, and counting it filled this list
       with rows whose only button was "Connect". */
    const has = (connector: Connector) =>
        (byConnector.get(connector.id)?.length ?? 0) > 0
        || (isBringYourOwn(connector)
            && (byInstall.get(connector.id) ?? []).some((install) => install.status !== "DISABLED"));
    const connected = all.filter(has);
    const unconnected = all.filter((connector) => !has(connector));
    const ailing = all.filter((connector) =>
        (byConnector.get(connector.id) ?? []).some((account) => !account.usable));
    const trouble = (accounts.data ?? []).filter((account) => !account.usable).length;

    /* What to show before anybody chooses. An organization with nothing
       connected opens on what it could connect, because the list of what it
       has is the empty one. */
    const active: Slice = slice ?? (connected.length ? "connected" : "available");
    const pool = active === "connected" ? connected : active === "trouble" ? ailing : unconnected;

    const term = query.trim().toLowerCase();
    const shown = term
        ? pool.filter(
              (connector) =>
                  connector.title.toLowerCase().includes(term) || connector.id.includes(term),
          )
        : pool;

    return (
        <div className="section">
            <p className="section__meta">
                {accounts.isSuccess
                    ? (accounts.data.length || "No") +
                      (accounts.data.length === 1 ? " account" : " accounts") +
                      (trouble ? " · " + trouble + " needing attention" : "")
                    : ""}
            </p>

            {/* What the provider's tab said on its way back. Said here because
                the tab it happened in has closed. */}
            {heard && (() => {
                const note = outcomeNote(heard);
                const named = all.find((connector) => connector.id === heard.connector)?.title;
                return (
                    <p className={"connect-result" + (note.bad ? " connect-result--warn" : "")} role="status">
                        {note.bad ? <WarningIcon size={16} /> : <CheckCircleIcon size={16} />}
                        {" "}{named ? named + ": " : ""}{note.text}
                        <button className="linkish" onClick={() => setHeard(null)}>Dismiss</button>
                    </p>
                );
            })()}

            {(connectors.isPending || accounts.isPending) && <p className="empty-row">Reading…</p>}
            {(connectors.isError || accounts.isError) && (
                <p className="empty-row">
                    Couldn’t load connectors.{" "}
                    <button className="btn" onClick={() => { void connectors.refetch(); void accounts.refetch(); }}>
                        <RefreshIcon size={14} /> Retry
                    </button>
                </p>
            )}

            {connectors.isSuccess && accounts.isSuccess && (
                <>
                    <div className="connectors__top">
                        <label className="connectors__find">
                            <SearchIcon size={15} />
                            <input
                                value={query}
                                placeholder={"Search " + all.length + " connectors…"}
                                onChange={(event) => setQuery(event.target.value)}
                            />
                        </label>
                        {/* Only where there is something to point at. The
                            kinds are catalogue data, and a deployment without
                            them should not offer a door to nothing. */}
                        {all.some(isBringYourOwn) && mayInstall !== false && (
                            <button className="btn" onClick={() => setAdding(true)}>
                                <PlusIcon size={15} /> Add your own
                            </button>
                        )}
                    </div>

                    {/* The counts are the point of the pills: "69 available"
                        says more about what is possible here than any six of
                        them picked off the top of an alphabet ever did. */}
                    <div className="slices" role="tablist" aria-label="Which connectors">
                        <button
                            role="tab"
                            className="chip"
                            aria-selected={active === "connected"}
                            onClick={() => setSlice("connected")}
                        >Connected <b>{connected.length}</b></button>
                        <button
                            role="tab"
                            className="chip"
                            aria-selected={active === "available"}
                            onClick={() => setSlice("available")}
                        >Available <b>{unconnected.length}</b></button>
                        {trouble > 0 && (
                            <button
                                role="tab"
                                className="chip chip--bad"
                                aria-selected={active === "trouble"}
                                onClick={() => setSlice("trouble")}
                            >Needs attention <b>{ailing.length}</b></button>
                        )}
                    </div>

                    {shown.length === 0 && (
                        <p className="empty-row">
                            {term
                                ? "Nothing matches \u201c" + query + "\u201d."
                                : active === "connected"
                                    ? "Nothing connected yet."
                                    : active === "trouble"
                                        ? "Everything connected is working."
                                        : all.length > 0
                                            ? "Every connector is already connected."
                                            /* Zero of zero is not "all of them": an empty
                                               catalog means its import has not run or
                                               failed, and saying everything is connected
                                               hid that. */
                                            : "No connectors are in the catalog yet. It may still be loading."}
                            {!term && all.length === 0 && (
                                <>
                                    {" "}
                                    <button className="btn" onClick={() => void connectors.refetch()}>
                                        <RefreshIcon size={14} /> Retry
                                    </button>
                                </>
                            )}
                        </p>
                    )}

                    {shown.length > 0 && (
                        <div className="connectors">
                            {shown.map((connector) => (
                                <ConnectorCard
                                    key={connector.id}
                                    connector={connector}
                                    accounts={byConnector.get(connector.id) ?? []}
                                    installs={byInstall.get(connector.id) ?? []}
                                    takenNames={takenNames}
                                    returned={returned}
                                    mayInstall={mayInstall}
                                    onAdd={() => setAdding(true)}
                                    orgId={orgId}
                                    onChanged={refresh}
                                    brief={active === "available"}
                                />
                            ))}
                        </div>
                    )}
                </>
            )}

            {adding && (
                <AddConnector
                    orgId={orgId}
                    entries={all}
                    onClose={() => setAdding(false)}
                    onDone={() => { setAdding(false); refresh(); }}
                />
            )}

            {/* Most of the catalog's breadth arrives through Composio, which a
                local install has no key for until somebody adds one. Hidden
                once it is set, and anywhere but this computer's own window. */}
            {connectors.isSuccess && (
                <SetUpOnThisMac form="composio" compact lead="Want Notion, Linear, HubSpot and more? Add a Composio key on {machine}." />
            )}

            {accounts.isSuccess && connectors.isSuccess && accounts.data.length > 0 && (
                <p className="connectors__note">
                    <CheckCircleIcon size={13} /> A teammate can answer on any of these. Removing one stops
                    everything pinned to it.
                </p>
            )}
        </div>
    );
}
