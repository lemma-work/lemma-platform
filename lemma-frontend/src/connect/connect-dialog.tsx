import { useMemo, useState } from "react";
import { Modal } from "@/shell/modal";
import { ExternalIcon, RefreshIcon } from "@/ui/icons";
import { useConnector, useCreateAccount, useCreateInstall, useConnectorRefresh, useRotateCredentials } from "./queries";
import { Fields } from "./fields";
import { blank, fields, payload, problems, type Values } from "./schema";
import {
    canBringOwnApp, connectRoute, connectSchema, installSchema, kindNamed, needsOwnApp, urlRefusal,
    type CatalogEntry, type Install,
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
export function ConnectDialog({ orgId, connector, install, onClose, onDone, onAuthorize }: {
    orgId: string;
    connector: CatalogEntry;
    /** Which install to connect against, when the organization has more than
     *  one. The API permits many installs of one connector deliberately. */
    install: Install | null;
    onClose: () => void;
    onDone: () => void;
    /** Hands the browser round trip back to the caller, which already owns it. */
    onAuthorize: (installId: string | null, connectionFields?: Record<string, unknown>) => void;
}) {
    const detail = useConnector(connector.id);
    const entry = detail.data ?? connector;
    const kind = useMemo(() => kindNamed(entry, install?.kind), [entry, install?.kind]);
    const refresh = useConnectorRefresh(orgId);

    const ownApp = needsOwnApp(kind);
    const [bringingApp, setBringingApp] = useState(false);
    /* The install just registered from the own-app form, once there is one.
       From then on the dialog is on the sign-in half, even for a toolkit that
       always needs an app of its own. */
    const [madeInstall, setMadeInstall] = useState<string | null>(null);
    const showingApp = (ownApp || bringingApp) && madeInstall === null;

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
    const connectAccount = useCreateAccount(orgId);
    const busy = makeInstall.isPending || connectAccount.isPending;

    const route = connectRoute(install, kind);
    const authorizeAs = madeInstall ?? install?.id ?? null;

    const fail = (problem: unknown) => {
        const message = problem instanceof Error ? problem.message : "Couldn’t connect this account.";
        setFailure(urlRefusal(message) ?? message);
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
                   can authorise against it, so this creates the install and
                   hands straight over to the redirect. */
                const made = await makeInstall.mutateAsync({
                    connectorId: entry.id, kind: kind?.kind, config: body, ownCredentials: true,
                });
                refresh();
                if (fields(connectSchema(kind)).length > 0) {
                    /* Still something to ask before the sign-in can start. */
                    setMadeInstall(made.id ?? null);
                    setValues({});
                    setShown({});
                    return;
                }
                onAuthorize(made.id ?? null);
                return;
            }
            if (route === "redirect") {
                onAuthorize(authorizeAs, body);
                return;
            }
            if (!install) { setFailure("There is nothing to connect against yet."); return; }
            await connectAccount.mutateAsync({ installId: install.id, credentials: body });
            refresh();
            onDone();
        } catch (problem) { fail(problem); }
    };

    if (detail.isPending) {
        return <Modal title={"Connect " + connector.title} narrow onClose={onClose}>
            <p role="status">Reading what this one needs…</p>
        </Modal>;
    }

    return (
        <Modal
            title={showingApp ? "Use your own " + connector.title + " app" : "Connect " + connector.title}
            subtitle={install?.name && !showingApp ? install.name : undefined}
            narrow
            onClose={onClose}
        >
            {showingApp && (
                <p className="connect-lead">
                    {ownApp
                        ? "Lemma holds no credentials for this one, so it connects through an app you register yourself."
                        : "Authorisation will run against your app rather than Lemma's."}
                </p>
            )}

            {!showingApp && route === "redirect" && list.length === 0 ? (
                <>
                    <p className="connect-lead">
                        This one signs in through {connector.title}. You will come back here once it is done.
                    </p>
                    <div className="record-form__actions">
                        <button className="btn btn--primary" onClick={() => onAuthorize(authorizeAs)}>
                            Continue <ExternalIcon size={13} />
                        </button>
                        {canBringOwnApp(kind) && !ownApp && (
                            <button className="btn" onClick={() => { setBringingApp(true); setValues({}); }}>
                                Use your own app
                            </button>
                        )}
                        <button className="btn" onClick={onClose}>Cancel</button>
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
                    {failure && <p className="library-problem" role="alert">{failure}</p>}
                    <div className="record-form__actions">
                        <button className="btn btn--primary" disabled={busy} onClick={() => void submit()}>
                            {busy ? "Connecting…" : showingApp ? "Save and authorise"
                                : route === "redirect" ? <>Continue <ExternalIcon size={13} /></> : "Connect"}
                        </button>
                        <button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
                    </div>
                </>
            )}
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
            <p className="connect-lead">
                Existing connections will use the replacement credential. Check that it has the access they need.
            </p>
            {detail.isPending ? <p role="status">Reading what this one needs…</p>
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
                                {rotate.isPending ? "Replacing…" : <>Replace <RefreshIcon size={14} /></>}
                            </button>
                            <button className="btn" disabled={rotate.isPending} onClick={onClose}>Cancel</button>
                        </div>
                    </>
                )}
        </Modal>
    );
}
