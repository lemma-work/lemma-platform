import { useMemo, useState } from "react";
import { Modal } from "@/shell/modal";
import { LoadingIndicator } from "@/ui/loading";
import { CheckCircleIcon, WarningIcon } from "@/ui/icons";
import { Fields, NOT_A_LOGIN } from "./fields";
import { useConnector, useConnectorRefresh, useInstall, useUpdateInstall } from "./queries";
import { blank, fields, payload, problems, type Values } from "./schema";
import { connectorProblem, discoveryNote, installSchema, kindNamed, type CatalogEntry, type Install } from "./install";

/** Changing a connection somebody set up — its name, the server or database
 *  it points at, or the OAuth app it signs in through — without removing it.
 *
 *  Removing was the only way to change one, and its accounts go with it. The
 *  server keeps them through an edit and asks people to sign in again only
 *  where the change made their credential meaningless: a different host for a
 *  database, a different origin for a server, a different app for OAuth.
 *
 *  What comes back from the server is masked, and the form is drawn from it,
 *  so a secret nobody touched is sent back masked and the server restores it.
 *  See `payload`. The credential itself is the account's, not the install's,
 *  and is replaced from the account's own row. */
export function EditInstall({ orgId, install, connector, takenNames, onClose, onDone }: {
    orgId: string;
    install: Install;
    connector: CatalogEntry;
    /** Every install name in the organization. Names are unique per org. */
    takenNames: string[];
    onClose: () => void;
    onDone: () => void;
}) {
    const detail = useConnector(connector.id);
    const current = useInstall(orgId, install);
    const kind = useMemo(() => kindNamed(detail.data ?? connector, install.kind), [detail.data, connector, install.kind]);
    const list = useMemo(() => fields(installSchema(kind)), [kind]);
    const update = useUpdateInstall(orgId);
    const refresh = useConnectorRefresh(orgId);

    const [name, setName] = useState(install.name || "");
    /* Null until somebody types, so the form follows the fresh read when it
       lands rather than freezing on the list's copy. */
    const [values, setValues] = useState<Values | null>(null);
    const ready = values ?? blank(list, current.data?.config ?? install.config ?? null);
    const [shown, setShown] = useState<Record<string, string>>({});
    const [failure, setFailure] = useState<string | null>(null);
    const [done, setDone] = useState<{ note: string; bad: boolean; reauth: number } | null>(null);

    const loading = detail.isLoading || current.isLoading;
    const signsIn = kind?.auth_scheme === "OAUTH2";

    const submit = async () => {
        setFailure(null);
        const found = problems(list, ready);
        setShown(found);
        if (Object.keys(found).length > 0) return;
        const wanted = name.trim();
        if (!wanted) { setFailure("Give it a name you will recognise later."); return; }
        const renamed = wanted !== (install.name || "");
        if (renamed && takenNames.includes(wanted)) { setFailure("Another connection here is already called that."); return; }
        try {
            const answer = await update.mutateAsync({
                install,
                name: renamed ? wanted : undefined,
                config: list.length > 0 ? payload(list, ready, { editing: true }) : undefined,
            });
            refresh();
            const status = answer.discovery?.status;
            /* Re-discovery only runs when the change moved where operations
               come from. Without it, and with nobody asked to sign in again,
               there is nothing to report beyond the list redrawing. */
            const rediscovered = Boolean(status && status !== "not_applicable");
            if (!rediscovered && answer.reauth === 0) { onDone(); return; }
            setDone({
                note: rediscovered ? discoveryNote(status, answer.discovery?.operation_count, null) : "Saved.",
                bad: status === "failed",
                reauth: answer.reauth,
            });
        } catch (problem) {
            setFailure(connectorProblem(problem, "Those changes could not be saved."));
        }
    };

    if (done) {
        return (
            <Modal title={name.trim() || connector.title} subtitle="Saved" narrow onClose={onDone}>
                <div className="connect-form">
                    <p className={"connect-result" + (done.bad ? " connect-result--warn" : "")} role="status">
                        {done.bad ? <WarningIcon size={18} /> : <CheckCircleIcon size={18} />} {done.note}
                    </p>
                    {done.reauth > 0 && (
                        <p className="connect-lead">
                            {done.reauth === 1 ? "One account was" : done.reauth + " accounts were"} connected to the old one,
                            and {done.reauth === 1 ? "has" : "have"} to sign in again. {done.reauth === 1 ? "It is" : "They are"} kept, and marked in the list.
                        </p>
                    )}
                    <div className="record-form__actions">
                        <button className="btn btn--primary" onClick={onDone}>Done</button>
                    </div>
                </div>
            </Modal>
        );
    }

    return (
        <Modal title={"Edit " + (install.name || connector.title)} subtitle={connector.title} narrow onClose={onClose}>
            <div className="connect-form">
                {loading ? <p className="connect-lead"><LoadingIndicator inline label="Reading this connection" /></p> : (
                    <>
                        <p className="connect-lead">
                            {signsIn
                                ? "Changing the app’s client asks everyone signed in through it to sign in again."
                                : "Accounts stay connected. Pointing it somewhere else asks the people using it to sign in again."}
                        </p>
                        <div className="record-form">
                            <div className="record-form__field">
                                <label htmlFor="edit-install-name">Name<i aria-hidden="true"> *</i></label>
                                <small>How your team and its teammates refer to this connection.</small>
                                <input id="edit-install-name" value={name} disabled={update.isPending} autoComplete="off" {...NOT_A_LOGIN}
                                    onChange={(event) => setName(event.target.value)} />
                            </div>
                        </div>
                        <Fields list={list} values={ready} problems={shown} disabled={update.isPending}
                            onChange={(field, value) => setValues({ ...ready, [field]: value })} />
                        {failure && <p className="library-problem" role="alert">{failure}</p>}
                        <div className="record-form__actions">
                            <button className="btn btn--primary" disabled={update.isPending} onClick={() => void submit()}>
                                {update.isPending ? <LoadingIndicator inline label="Saving" /> : "Save changes"}
                            </button>
                            <button className="btn" disabled={update.isPending} onClick={onClose}>Cancel</button>
                        </div>
                    </>
                )}
            </div>
        </Modal>
    );
}
