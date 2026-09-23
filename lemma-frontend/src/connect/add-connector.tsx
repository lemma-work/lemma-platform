import { useMemo, useState } from "react";
import { Modal } from "@/shell/modal";
import { CheckCircleIcon, CodeIcon, ConnectorIcon, TableIcon, WarningIcon } from "@/ui/icons";
import { Fields } from "./fields";
import { useConnector, useConnectorRefresh, useCreateInstall, useRefreshOperations } from "./queries";
import { blank, fields, payload, problems, type Values } from "./schema";
import { COMPOSIO, discoveryNote, installSchema, kindNamed, urlRefusal, type CatalogEntry } from "./install";

/** Pointing this organization at a server of its own.
 *
 *  These are already in the catalogue and this app had no door to them: one
 *  entry per sort, standing for every server of that sort. So "which
 *  connector" is the wrong first question — the first question is what kind of
 *  thing you are connecting, and the entry follows from that.
 *
 *  The kinds are found by asking the catalogue what is not brokered, rather
 *  than by hardcoding three ids: the ids are catalogue data, and a deployment
 *  that renames or removes one should show what it has rather than three dead
 *  tiles.
 */
const LOOKS = {
    mcp: { title: "An MCP server", blurb: "Tools your teammates can call, from any server speaking MCP." },
    http: { title: "A REST API", blurb: "Anything with an OpenAPI description. Its operations are read from the spec." },
    sql: { title: "A database", blurb: "Query it directly, with a connection string you supply." },
} as const;

function Glyph({ kind }: { kind: string }) {
    if (kind === "sql") return <TableIcon size={20} />;
    if (kind === "http") return <CodeIcon size={20} />;
    return <ConnectorIcon size={20} />;
}

export function AddConnector({ orgId, entries, onClose, onDone }: {
    orgId: string;
    /** The whole catalogue; the bring-your-own entries are picked out of it. */
    entries: CatalogEntry[];
    onClose: () => void;
    onDone: () => void;
}) {
    /** Every non-brokered kind on offer, with the entry that carries it. */
    const choices = useMemo(() => {
        const found: { kind: string; entry: CatalogEntry }[] = [];
        for (const entry of entries) {
            for (const one of entry.kinds ?? []) {
                if (one.kind === COMPOSIO) continue;
                if (found.some((seen) => seen.kind === one.kind)) continue;
                found.push({ kind: one.kind, entry });
            }
        }
        return found;
    }, [entries]);

    const [picked, setPicked] = useState<{ kind: string; entry: CatalogEntry } | null>(null);

    if (!picked) {
        return (
            <Modal title="Add your own" subtitle="Connect a service your organization uses" narrow onClose={onClose}>
                {choices.length === 0 ? (
                    <p role="status" className="connect-lead">
                        Custom connections are not available on this deployment.
                    </p>
                ) : (
                    <div className="connect-picks">
                        {choices.map((choice) => {
                            const look = LOOKS[choice.kind as keyof typeof LOOKS];
                            return (
                                <button className="connect-pick" key={choice.kind} onClick={() => setPicked(choice)}>
                                    <span className="connect-pick__glyph"><Glyph kind={choice.kind} /></span>
                                    <span>
                                        <strong>{look?.title ?? choice.entry.title}</strong>
                                        <small>{look?.blurb ?? choice.entry.description ?? ""}</small>
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                )}
            </Modal>
        );
    }

    return <AddOne orgId={orgId} kind={picked.kind} entry={picked.entry}
        onBack={() => setPicked(null)} onClose={onClose} onDone={onDone} />;
}

function AddOne({ orgId, kind, entry, onBack, onClose, onDone }: {
    orgId: string;
    kind: string;
    entry: CatalogEntry;
    onBack: () => void;
    onClose: () => void;
    onDone: () => void;
}) {
    const detail = useConnector(entry.id);
    const spec = useMemo(() => kindNamed(detail.data ?? entry, kind), [detail.data, entry, kind]);
    const list = useMemo(() => fields(installSchema(spec)), [spec]);
    const [name, setName] = useState("");
    const [values, setValues] = useState<Values>({});
    const [shown, setShown] = useState<Record<string, string>>({});
    const [failure, setFailure] = useState<string | null>(null);
    /* What discovery said, once there is an install to say it about. Kept
       rather than closed over, because "connected, and it advertised nothing"
       is the answer somebody most needs to read — and the one a dialog that
       closed on success would never show. */
    const [found, setFound] = useState<string | null>(null);

    const make = useCreateInstall(orgId);
    const discover = useRefreshOperations(orgId);
    const refresh = useConnectorRefresh(orgId);
    const busy = make.isPending || discover.isPending;
    const ready = list.length > 0 && Object.keys(values).length === 0 ? blank(list) : values;
    const look = LOOKS[kind as keyof typeof LOOKS];

    const submit = async () => {
        setFailure(null);
        const problem = problems(list, ready);
        setShown(problem);
        if (Object.keys(problem).length > 0) return;
        if (!name.trim()) { setShown({ ...problem, __name: "" }); setFailure("Give it a name you will recognise later."); return; }
        try {
            const made = await make.mutateAsync({
                connectorId: entry.id,
                kind,
                name: name.trim(),
                config: payload(list, ready),
                ownCredentials: true,
            });
            refresh();
            /* Discovery is the point of the exercise for these kinds, and it
               can fail on its own after a perfectly good install — a server
               that is up, reachable and refuses to list. Reported rather than
               folded into the create. */
            try {
                const answer = await discover.mutateAsync(made);
                setFound(discoveryNote(answer.status, answer.operation_count, answer.error));
            } catch {
                setFound("Connected, but its operations could not be read just now.");
            }
            refresh();
        } catch (problem) {
            const message = problem instanceof Error ? problem.message : "That could not be added.";
            setFailure(urlRefusal(message) ?? message);
        }
    };

    if (found !== null) {
        const bad = /refused|could not/.test(found);
        return (
            <Modal title={name || look?.title || entry.title} subtitle="Added" narrow onClose={onDone}>
                <p className={"connect-result" + (bad ? " connect-result--warn" : "")} role="status">
                    {bad ? <WarningIcon size={18} /> : <CheckCircleIcon size={18} />} {found}
                </p>
                {bad && (
                    <p className="connect-lead">
                        It is saved either way. Fix the address or the token and re-read its operations from the list.
                    </p>
                )}
                <div className="record-form__actions"><button className="btn btn--primary" onClick={onDone}>Done</button></div>
            </Modal>
        );
    }

    return (
        <Modal title={look?.title ?? entry.title} subtitle={look?.blurb} narrow onClose={onClose}>
            {detail.isPending ? <p role="status">Reading what this needs…</p> : (
                <>
                    <div className="record-form">
                        <div className="record-form__field">
                            <label htmlFor="connect-name">Name<i aria-hidden="true"> *</i></label>
                            <small>Choose a name so your team can recognize this connection.</small>
                            <input id="connect-name" value={name} disabled={busy} placeholder="Sentry, Postgres (staging)…"
                                onChange={(event) => setName(event.target.value)} />
                        </div>
                    </div>
                    <Fields list={list} values={ready} problems={shown} disabled={busy}
                        onChange={(field, value) => setValues({ ...ready, [field]: value })} />
                    {failure && <p className="library-problem" role="alert">{failure}</p>}
                    <div className="record-form__actions">
                        <button className="btn btn--primary" disabled={busy} onClick={() => void submit()}>
                            {make.isPending ? "Adding…" : discover.isPending ? "Reading its operations…" : "Add connection"}
                        </button>
                        <button className="btn" disabled={busy} onClick={onBack}>Back</button>
                    </div>
                </>
            )}
        </Modal>
    );
}
