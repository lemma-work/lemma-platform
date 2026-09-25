import { LoadingIndicator } from "@/ui/loading";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { accountName, accountTrouble, type Connector, type ConnectorAccount } from "@/data";
import { CheckCircleIcon, ExternalIcon, PlusIcon, RefreshIcon, SearchIcon, WarningIcon } from "@/ui/icons";
import { ConnectDialog, RotateDialog } from "@/connect/connect-dialog";
import { AddConnector } from "@/connect/add-connector";
import { useConnectorRefresh, useDeleteInstall, useInstalls, useRefreshOperations } from "@/connect/queries";
import { discoveryNote, isBringYourOwn, type CatalogEntry, type Install } from "@/connect/install";

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
}: {
    account: ConnectorAccount;
    connector: Connector;
    orgId: string;
    onGone: () => void;
    /** Replace the credential in place. Absent where the connector has no
     *  credential to replace — an OAuth account is re-authorised, not retyped. */
    onRotate?: () => void;
}) {
    const [confirming, setConfirming] = useState(false);
    const drop = useMutation({
        mutationFn: () => source.disconnectAccount(orgId, account.id),
        onSuccess: onGone,
    });
    const trouble = accountTrouble(account);

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
            {trouble && <span className="acct__trouble"><WarningIcon size={13} /> {trouble}</span>}
            {confirming ? (
                <span className="acct__confirm">
                    <button className="btn reachrow__drop" disabled={drop.isPending} onClick={() => drop.mutate()}>
                        {drop.isPending ? "Removing…" : "Remove"}
                    </button>
                    <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                </span>
            ) : (
                <span className="acct__acts">
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

/** One install of a connector this organization points somewhere itself.
 *
 *  Only shown for the entries that stand for many servers. `mcp` is one
 *  catalogue row for every MCP server anybody adds, so the card alone says
 *  nothing about what is actually connected — the installs under it are the
 *  real list, and their names are the only thing telling one from another.
 */
function InstallRow({ install, orgId, onChanged }: { install: Install; orgId: string; onChanged: () => void }) {
    const [said, setSaid] = useState<string | null>(null);
    const [confirming, setConfirming] = useState(false);
    const reread = useRefreshOperations(orgId);
    const drop = useDeleteInstall(orgId);

    return (
        <li className="acct">
            <span className={"acct__dot" + (install.status === "DISABLED" ? "" : " acct__dot--ok")} aria-hidden="true" />
            <span className="acct__who">
                {install.name || install.id}
                <span className="acct__ref">{install.kind}</span>
                {install.is_default && <span className="pill">default</span>}
            </span>
            {said && <span className="acct__trouble acct__trouble--quiet" role="status">{said}</span>}
            {confirming ? (
                <span className="acct__confirm">
                    <button className="btn reachrow__drop" disabled={drop.isPending}
                        onClick={() => drop.mutate(install, { onSuccess: onChanged })}>
                        {drop.isPending ? "Removing…" : "Remove"}
                    </button>
                    <button className="linkish" onClick={() => setConfirming(false)}>Keep</button>
                </span>
            ) : (
                <span className="acct__acts">
                    <button className="linkish reachrow__quiet" disabled={reread.isPending}
                        onClick={() => reread.mutate(install, {
                            onSuccess: (answer) => { setSaid(discoveryNote(answer.status, answer.operation_count, answer.error)); onChanged(); },
                            onError: () => setSaid("Its operations could not be read just now."),
                        })}>
                        {reread.isPending ? "Reading…" : "Re-read operations"}
                    </button>
                    <button className="linkish reachrow__quiet" onClick={() => setConfirming(true)}>Remove</button>
                </span>
            )}
        </li>
    );
}

function ConnectorCard({
    connector,
    accounts,
    installs,
    orgId,
    onChanged, onAdd, brief = false }: {
    connector: Connector;
    accounts: ConnectorAccount[];
    /** This organization's installs of this connector. */
    installs: Install[];
    orgId: string;
    onChanged: () => void;
    /** Opens the add flow. An entry that stands for many servers has nothing
     *  to connect against until one exists, so for those this is the button. */
    onAdd: () => void;
    brief?: boolean }) {
    const [link, setLink] = useState<{ authorizeUrl: string; before: string[] } | null>(null);
    const [error, setError] = useState<string | null>(null);
    /* Opening the dialog rather than starting a flow: which of the two routes
       this connector is on is a question about its kinds, and the dialog is
       what reads them. The alternative is for the card to guess — ask for an
       authorize URL, and give up when there is not one. */
    const [connecting, setConnecting] = useState<Install | null | undefined>(undefined);
    const [rotating, setRotating] = useState<ConnectorAccount | null>(null);

    const start = useMutation({
        mutationFn: ({ installId, connectionFields }: { installId: string | null; connectionFields?: Record<string, unknown> }) =>
            source.startAccount(orgId, connector.id, installId ?? undefined, connectionFields),
        onSuccess: (started) => {
            setConnecting(undefined);
            setLink(started);
            if (!started.authorizeUrl) setError("This one offered no way to sign in. It may need an app of your own first.");
        },
        onError: (problem) => setError(problem instanceof Error ? problem.message : "That could not be started."),
    });

    /* The catalogue entry, in the shape the dialog reads. The two models are
       the same row seen by two callers — this view has always wanted a title
       and a logo, and the dialog wants the kinds. */
    const entry: CatalogEntry = {
        id: connector.id, title: connector.title, description: connector.description, icon: connector.icon,
    };
    /* Installs are worth listing where the catalogue entry stands for many
       servers, and noise where it stands for one: nobody needs to be told
       their Gmail account has a Gmail install behind it. */
    const showInstalls = installs.length > 0 && accounts.length === 0;
    /* `mcp` is a catalogue row, not a server. Until this organization has
       pointed it somewhere there is nothing to authorise against, and
       "Connect" would open a dialog whose only honest answer is that the
       connector has not described what it needs. */
    const needsAdding = isBringYourOwn(connector) && installs.length === 0;

    return (
        <div className={"connector" + (brief ? " connector--brief" : "")} data-on={accounts.length > 0 || installs.length > 0 ? "" : undefined}>
            <Logo connector={connector} />
            <div className="connector__body">
                <span className="connector__name">{connector.title}</span>
                {connector.description && <span className="connector__blurb">{connector.description}</span>}
                {error && <span className="reachrow__error">{error}</span>}
            </div>
            <div className="connector__acts">
                {link?.authorizeUrl ? (
                    <>
                        <a
                            className="btn btn--primary"
                            href={link.authorizeUrl}
                            target="_blank"
                            rel="noreferrer"
                            onClick={() => window.setTimeout(onChanged, 4000)}
                        >
                            Authorise <ExternalIcon size={13} />
                        </a>
                        <button className="linkish" onClick={() => { setLink(null); onChanged(); }}>
                            <RefreshIcon size={13} /> Done?
                        </button>
                    </>
                ) : needsAdding ? (
                    <button className="btn" onClick={onAdd}>Add one</button>
                ) : (
                    <button className="btn" disabled={start.isPending}
                        onClick={() => { setError(null); setConnecting(installs.find((one) => one.is_default) ?? installs[0] ?? null); }}>
                        {start.isPending ? <LoadingIndicator inline label="Loading" /> : accounts.length > 0 ? "Add another" : "Connect"}
                    </button>
                )}
            </div>

            {/* A row of the connector's own grid rather than a child of its
                middle column: nested in the body, an account's actions stopped
                where the description stopped, which is a couple of hundred
                pixels short of the button they sit under. */}
            {accounts.length > 0 && (
                <ul className="accts">
                    {accounts.map((account) => (
                        <AccountRow
                            key={account.id}
                            account={account}
                            connector={connector}
                            orgId={orgId}
                            onGone={onChanged}
                            onRotate={
                                installs.find((one) => one.id === account.authConfigId)?.auth_scheme === "API_KEY"
                                    ? () => setRotating(account)
                                    : undefined
                            }
                        />
                    ))}
                </ul>
            )}
            {showInstalls && (
                <ul className="accts">
                    {installs.map((install) => (
                        <InstallRow key={install.id} install={install} orgId={orgId} onChanged={onChanged} />
                    ))}
                </ul>
            )}

            {connecting !== undefined && (
                <ConnectDialog
                    orgId={orgId}
                    connector={entry}
                    install={connecting}
                    onClose={() => setConnecting(undefined)}
                    onDone={() => { setConnecting(undefined); onChanged(); }}
                    onAuthorize={(installId, connectionFields) => start.mutate({ installId, connectionFields })}
                />
            )}
            {rotating && (
                <RotateDialog
                    orgId={orgId}
                    connector={entry}
                    install={installs.find((one) => one.id === rotating.authConfigId) ?? installs[0] ?? null}
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

    const connectors = useQuery({ queryKey: ["connectors"], queryFn: () => source.listConnectors() });
    const accounts = useQuery({ queryKey: ["accounts", orgId], queryFn: () => source.listAccounts(orgId) });
    const installs = useInstalls(orgId);
    const invalidate = useConnectorRefresh(orgId);

    const refresh = () => {
        invalidate();
        void queryClient.invalidateQueries({ queryKey: ["accounts", orgId] });
        void queryClient.invalidateQueries({ queryKey: ["connectors"] });
    };

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
    /* An install counts as connected even with no account behind it. A server
       this organization added *is* the connection — there is no second act of
       signing in, which is why counting accounts alone filed every MCP server
       and every database under "available". */
    const has = (connector: Connector) =>
        (byConnector.get(connector.id)?.length ?? 0) > 0 || (byInstall.get(connector.id)?.length ?? 0) > 0;
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

            {(connectors.isPending || accounts.isPending) && <p className="empty-row">Reading…</p>}
            {(connectors.isError || accounts.isError) && (
                <p className="empty-row">Couldn’t load connectors.</p>
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
                        {all.some(isBringYourOwn) && (
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
                                        : "Every connector is already connected."}
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

            {accounts.isSuccess && connectors.isSuccess && accounts.data.length > 0 && (
                <p className="connectors__note">
                    <CheckCircleIcon size={13} /> A teammate can answer on any of these. Removing one stops
                    everything pinned to it.
                </p>
            )}
        </div>
    );
}
