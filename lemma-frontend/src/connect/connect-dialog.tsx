import { useMemo, useState } from "react";
import { Modal } from "@/shell/modal";
import { LoadingIndicator } from "@/ui/loading";
import { ExternalIcon, RefreshIcon } from "@/ui/icons";
import {
    useConnector, useCreateAccount, useCreateInstall, useConnectorRefresh, useDeleteInstall, useRotateCredentials,
} from "./queries";
import { Fields } from "./fields";
import { blank, fields, payload, problems, type Values } from "./schema";
import {
    canBringOwnApp, canInstallWithDefaults, connectorProblem, connectRoute, connectSchema, freshInstallName, installSchema, kindFor,
    kindNamed, needsOwnApp, type CatalogEntry, type Install,
} from "./install";

/** Connecting an account, by whichever of the two routes this one is on.
 *
 *  This app had only the first: ask for an authorize URL, open it. When there
 *  was no URL it stopped and said "this one cannot be authorised from here
 *  yet" — which is every API-key connector in the catalogue, and the reason
 *  this dialog exists.
 *
 *  Three things can be in the way, and they need different work from the
 *  person, so they are three states rather than one error:
 *    - the organization has to bring its own OAuth app first;
 *    - the credential is a form to fill in;
 *    - it is a browser round trip, which is the case this app already had.
 *
 *  A browser round trip can still need a form first. Signing in says who the
 *  person is, not which tenant they mean: Shopify needs the store name before
 *  there is anywhere to send them. Those fields ride along to `onAuthorize`.
 */
export function ConnectDialog({
    orgId, connector, install, takenNames, mayInstall = null, authorizing = false, authorizeFailure = null,
    onClose, onDone, onAuthorize,
}: {
    orgId: string;
    connector: CatalogEntry;
    /** Which install to connect against, when the organization has more than
     *  one. The API permits many installs of one connector deliberately.
     *  Null when it has none yet — one is made on the way. */
    install: Install | null;
    /** Every install name in the organization. Names are unique per org, and
     *  an app of the organization's own is a second install of a connector
     *  that usually already has one. */
    takenNames: string[];
    /** Owner or editor. Anyone may connect an account; only they may make the
     *  organization's own app. */
    mayInstall?: boolean | null;
    /** The caller is fetching the sign-in address. Making the install and
     *  asking the provider for a URL is a couple of round trips, and a button
     *  that does nothing visible for that long reads as a button that failed. */
    authorizing?: boolean;
    /** Why the sign-in could not start. Said here, because the card the caller
     *  would otherwise write it on is behind this dialog. */
    authorizeFailure?: string | null;
    onClose: () => void;
    onDone: () => void;
    /** Hands the browser round trip back to the caller, which already owns it. */
    onAuthorize: (installId: string | null, connectionFields?: Record<string, unknown>) => void;
}) {
    const detail = useConnector(connector.id);
    const entry = detail.data ?? connector;
    /* An install made in this dialog — the organization's own app, when that
       app then takes a credential rather than a sign-in. */
    const [made, setMade] = useState<Install | null>(null);
    const against = made ?? install;
    /* With no install, the kind a fresh one would take. Asking only for an
       unambiguous kind left every connector offering two — Composio and a
       native one — at "has not described what it needs". */
    const kind = useMemo(() => kindFor(entry, against), [entry, against]);
    const refresh = useConnectorRefresh(orgId);

    /* Nothing to connect against, and Lemma cannot make it alone: the
       organization's own app, or whatever else the install needs, comes first. */
    const ownApp = !against && (needsOwnApp(kind) || !canInstallWithDefaults(kind));
    const [bringingApp, setBringingApp] = useState(false);
    const showingApp = ownApp || bringingApp;

    const list = useMemo(
        () => fields(showingApp ? installSchema(kind) : connectSchema(kind)),
        [kind, showingApp],
    );
    const [values, setValues] = useState<Values>({});
    const [shown, setShown] = useState<Record<string, string>>({});
    const [failure, setFailure] = useState<string | null>(null);
    const ready = useMemo(() => {
        /* Seeded once the schema is known, and not on every render: the form
           is the person's from the moment it is drawn. */
        if (list.length > 0 && Object.keys(values).length === 0) return blank(list);
        return values;
    }, [list, values]);

    const makeInstall = useCreateInstall(orgId);
    const dropInstall = useDeleteInstall(orgId);
    const connectAccount = useCreateAccount(orgId);
    const busy = makeInstall.isPending || connectAccount.isPending || authorizing;

    const route = connectRoute(against, kind);
    /* A connector that takes no credential at all is connected with an empty
       one — the account is still what every execution resolves. */
    const nothingToAsk = kind?.auth_scheme === "NOAUTH" || against?.auth_scheme === "NOAUTH";
    /* An organization's own OAuth app, or merely details an install needs:
       the same form, and not the same thing to say about it. */
    const signsIn = kind?.auth_scheme === "OAUTH2";

    const fail = (problem: unknown) => {
        setFailure(connectorProblem(problem, "Couldn’t connect this account."));
    };

    const submit = async () => {
        setFailure(null);
        const found = problems(list, ready);
        setShown(found);
        if (Object.keys(found).length > 0) return;
        const body = payload(list, ready);
        try {
            if (showingApp) {
                /* The organization's own app. It has to exist before anybody
                   can authorise against it. Named apart from the install
                   Lemma's own app lives on, which already holds the
                   connector's name. */
                const created = await makeInstall.mutateAsync({
                    connectorId: entry.id,
                    kind: kind?.kind,
                    name: freshInstallName(entry.id, takenNames),
                    config: body,
                    ownCredentials: true,
                });
                refresh();
                if (connectRoute(created, kind) === "redirect" && fields(connectSchema(kind)).length === 0) {
                    onAuthorize(created.id ?? null);
                    return;
                }
                /* A credential, or a sign-in that needs a question answered
                   first (Shopify's store): the next form, against the install
                   just made. */
                setMade(created);
                setBringingApp(false);
                setValues({});
                setShown({});
                return;
            }
            if (route === "redirect") {
                /* The sign-in's own fields, answered: on to the provider. */
                onAuthorize(against?.id ?? null, body);
                return;
            }
            /* Lemma's own install, made now when there is none. The account
               is authorised against an install and never against a connector;
               this is the step the old dialog stopped at, with "there is
               nothing to connect against yet". */
            let installId = against?.id ?? null;
            let madeHere: Install | null = null;
            if (!installId) {
                madeHere = await makeInstall.mutateAsync({ connectorId: entry.id, kind: kind?.kind });
                installId = madeHere.id;
            }
            try {
                await connectAccount.mutateAsync({ installId, credentials: body });
            } catch (problem) {
                /* Two calls, one act: an install made moments ago with nobody
                   on it would otherwise hold the connector's name, and every
                   retry would be refused. */
                if (madeHere) await dropInstall.mutateAsync(madeHere).catch(() => undefined);
                throw problem;
            }
            refresh();
            onDone();
        } catch (problem) { fail(problem); }
    };

    /* Loading, not pending: a query that is switched off — sample data has no
       catalogue to ask — is pending forever, and this would never get past it. */
    if (detail.isLoading) {
        return <Modal title={"Connect " + connector.title} narrow onClose={onClose}>
            <p className="connect-lead"><LoadingIndicator inline label="Reading what this one needs" /></p>
        </Modal>;
    }

    return (
        <Modal
            title={showingApp
                ? signsIn ? "Use your own " + connector.title + " app" : "Set up " + connector.title
                : "Connect " + connector.title}
            subtitle={against?.name && !showingApp ? against.name : undefined}
            narrow
            onClose={onClose}
        >
            <div className="connect-form">
                {showingApp && (
                    <p className="connect-lead">
                        {!signsIn
                            ? connector.title + " needs a few details from your organization before anyone can connect it."
                            : ownApp
                                ? "Lemma holds no credentials for this one, so it connects through an app you register yourself."
                                : "Authorisation will run against your app rather than Lemma's."}
                    </p>
                )}

                {!showingApp && route === "redirect" && list.length === 0 ? (
                    <>
                        <p className="connect-lead">
                            This one signs in through {connector.title}. You will come back here once it is done.
                        </p>
                        {authorizeFailure && <p className="library-problem" role="alert">{authorizeFailure}</p>}
                        <div className="record-form__actions">
                            <button className="btn btn--primary" disabled={busy} onClick={() => onAuthorize(against?.id ?? null)}>
                                {authorizing
                                    ? <LoadingIndicator inline label={"Opening " + connector.title} />
                                    : <>Continue <ExternalIcon size={13} /></>}
                            </button>
                            {canBringOwnApp(kind) && !ownApp && mayInstall !== false && (
                                <button className="btn" disabled={busy} onClick={() => { setBringingApp(true); setValues({}); }}>
                                    Use your own app
                                </button>
                            )}
                            <button className="btn" onClick={onClose}>Cancel</button>
                        </div>
                    </>
                ) : !showingApp && list.length === 0 && nothingToAsk ? (
                    <>
                        <p className="connect-lead">Nothing to fill in for this one.</p>
                        {failure && <p className="library-problem" role="alert">{failure}</p>}
                        <div className="record-form__actions">
                            <button className="btn btn--primary" disabled={busy} onClick={() => void submit()}>
                                {busy ? <LoadingIndicator inline label="Connecting" /> : "Connect"}
                            </button>
                            <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
                        </div>
                    </>
                ) : list.length === 0 ? (
                    <>
                        {/* A schema with no fields is not a form to submit. Saying
                            so beats drawing an empty box with a Connect button
                            under it that can only fail. */}
                        <p role="alert" className="connect-lead">
                            This connector has not described what it needs, so it cannot be connected from here yet.
                        </p>
                        <div className="record-form__actions"><button className="btn" onClick={onClose}>Close</button></div>
                    </>
                ) : (
                    <>
                        {!showingApp && route === "redirect" && (
                            <p className="connect-lead">
                                This one signs in through {connector.title}, once it knows which account you mean.
                            </p>
                        )}
                        <Fields list={list} values={ready} problems={shown} disabled={busy}
                            onChange={(name, value) => setValues({ ...ready, [name]: value })} />
                        {(failure ?? authorizeFailure) && <p className="library-problem" role="alert">{failure ?? authorizeFailure}</p>}
                        <div className="record-form__actions">
                            <button className="btn btn--primary" disabled={busy} onClick={() => void submit()}>
                                {busy ? <LoadingIndicator inline label="Connecting" /> : showingApp ? "Save and authorise"
                                    : route === "redirect" ? <>Continue <ExternalIcon size={13} /></> : "Connect"}
                            </button>
                            <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
                        </div>
                    </>
                )}
            </div>
        </Modal>
    );
}

/** Replacing a credential on an account that already exists.
 *
 *  Its own dialog rather than a mode of the one above, because it is a
 *  different act with a different consequence: this keeps the account id, and
 *  disconnect-then-reconnect does not. Everything pinned to the old id — a
 *  schedule, a surface, a grant — survives this and does not survive that.
 */
export function RotateDialog({ orgId, connector, install, accountId, accountName, onClose, onDone }: {
    orgId: string;
    connector: CatalogEntry;
    install: Install | null;
    accountId: string;
    accountName: string;
    onClose: () => void;
    onDone: () => void;
}) {
    const detail = useConnector(connector.id);
    const kind = useMemo(() => kindNamed(detail.data ?? connector, install?.kind), [detail.data, connector, install?.kind]);
    const list = useMemo(() => fields(connectSchema(kind)), [kind]);
    const [values, setValues] = useState<Values>({});
    const [shown, setShown] = useState<Record<string, string>>({});
    const [failure, setFailure] = useState<string | null>(null);
    const rotate = useRotateCredentials(orgId);
    const refresh = useConnectorRefresh(orgId);
    const ready = list.length > 0 && Object.keys(values).length === 0 ? blank(list) : values;

    const submit = async () => {
        setFailure(null);
        const found = problems(list, ready);
        setShown(found);
        if (Object.keys(found).length > 0) return;
        try {
            await rotate.mutateAsync({ accountId, credentials: payload(list, ready) });
            refresh();
            onDone();
        } catch (problem) {
            setFailure(problem instanceof Error ? problem.message : "That credential was not accepted.");
        }
    };

    return (
        <Modal title="Replace the credential" subtitle={accountName} narrow onClose={onClose}>
            <div className="connect-form">
                <p className="connect-lead">
                    Existing connections will use the replacement credential. Check that it has the access they need.
                </p>
                {detail.isLoading ? <p className="connect-lead"><LoadingIndicator inline label="Reading what this one needs" /></p>
                    : list.length === 0 ? (
                        <>
                            <p role="alert" className="connect-lead">
                                This connector does not describe a credential that can be replaced from here.
                            </p>
                            <div className="record-form__actions"><button className="btn" onClick={onClose}>Close</button></div>
                        </>
                    ) : (
                        <>
                            <Fields list={list} values={ready} problems={shown} disabled={rotate.isPending}
                                onChange={(name, value) => setValues({ ...ready, [name]: value })} />
                            {failure && <p className="library-problem" role="alert">{failure}</p>}
                            <div className="record-form__actions">
                                <button className="btn btn--primary" disabled={rotate.isPending} onClick={() => void submit()}>
                                    {rotate.isPending ? <LoadingIndicator inline label="Replacing" /> : <>Replace <RefreshIcon size={14} /></>}
                                </button>
                                <button className="btn" disabled={rotate.isPending} onClick={onClose}>Cancel</button>
                            </div>
                        </>
                    )}
            </div>
        </Modal>
    );
}
