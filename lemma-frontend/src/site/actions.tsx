"use client";
import { InvitationDecision } from "./invitation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "@/session/session";
import { hasApiUrl, lemma } from "@/session/client";
import { source as data } from "@/data";
import type { ActionProps } from "./action-host";
import type { VariableSpec, ImportStatusResponse } from "./import-types";
export function Actions(props: ActionProps) {
    const session = useSession();
    if (!hasApiUrl() || data.label === "sample")
        return (
            <p>
                Connect to a live workspace to continue.{" "}
                <a href="/connect">Connection settings</a>
            </p>
        );
    if (session.status === "loading")
        return <p role="status">Checking your session…</p>;
    if (session.status !== "in")
        return (
            <button className="btn btn--primary" onClick={session.signIn}>
                Sign in to continue
            </button>
        );
    return <SignedIn {...props} signOut={session.signOut} />;
}
function SignedIn(props: ActionProps & { signOut: () => Promise<void> }) {
    if (props.action === "organization") return <CreateOrganization />;
    if (props.action === "logout")
        return (
            <button className="btn" onClick={() => void props.signOut()}>
                Sign out of Lemma
            </button>
        );
    if (props.action === "invite")
        return (
            <InvitationDecision
                id={props.invitationId!}
                decision={props.decision!}
            />
        );
    return <Install {...props} />;
}
function Install({ action, owner, repo, source }: ActionProps) {
    const orgs = useQuery({
        queryKey: ["orgs"],
        queryFn: () => data.listOrgs(),
    });
    const [chosenOrg, setOrg] = useState("");
    const orgId = chosenOrg || orgs.data?.[0]?.id || "";
    const pods = useQuery({
        queryKey: ["pods", orgId],
        queryFn: () => data.listPods(orgId),
        enabled: !!orgId,
    });
    const [selectedTarget, setTarget] = useState(() =>
        new URLSearchParams(window.location.search).get("destination") ===
        "existing"
            ? "existing"
            : "new",
    );
    const target =
        selectedTarget === "existing"
            ? (pods.data?.[0]?.id ?? "new")
            : selectedTarget;
    const [name, setName] = useState(repo || "New teammate");
    const [created, setCreated] = useState<string | null>(null);
    const [job, setJob] = useState<ImportStatusResponse | null>(null);
    const [values, setValues] = useState<Record<string, string>>({});
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [retry, setRetry] = useState(0);
    const jobId = job?.import_id;
    const podId = job?.pod_id;
    const polling =
        !!job &&
        ![
            "AWAITING_CONFIRMATION",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "PARTIALLY_CANCELLED",
        ].includes(job.status);
    useEffect(() => {
        if (!polling || !jobId || !podId) return;
        let alive = true;
        let timer: ReturnType<typeof setTimeout>;
        async function poll() {
            try {
                const next = await lemma(podId).request<ImportStatusResponse>(
                    "GET",
                    "/pods/" + podId + "/bundle/imports/" + jobId,
                );
                if (alive) {
                    setJob(next);
                    timer = setTimeout(() => void poll(), 1500);
                }
            } catch (e) {
                if (alive)
                    setError(
                        e instanceof Error
                            ? e.message
                            : "Could not read progress.",
                    );
            }
        }
        timer = setTimeout(() => void poll(), 1500);
        return () => {
            alive = false;
            clearTimeout(timer);
        };
    }, [polling, jobId, podId, retry]);
    async function prepare() {
        setBusy(true);
        setError("");
        try {
            if (action === "remix") {
                const url = new URL(source || "");
                if (!["http:", "https:"].includes(url.protocol))
                    throw new Error("Use a valid http or https app URL.");
            }
            let id = target === "new" ? created : target;
            if (!id) {
                const pod = await data.createPod(orgId, name.trim());
                id = pod.id;
                setCreated(id);
            }
            if (action === "remix") {
                const url = new URL(source || "");
                if (!["http:", "https:"].includes(url.protocol))
                    throw new Error("Use a valid http or https app URL.");
                window.location.assign(
                    "/t/" +
                        encodeURIComponent(id) +
                        "/conversation?remixSource=" +
                        encodeURIComponent(url.href),
                );
                return;
            }
            const next = await lemma(id).request<ImportStatusResponse>(
                "POST",
                "/pods/" + id + "/bundle/imports",
                { body: { kind: "GITHUB", owner, repo } },
            );
            setJob(next);
        } catch (e) {
            setError(
                e instanceof Error
                    ? e.message
                    : "Could not prepare the installation.",
            );
        } finally {
            setBusy(false);
        }
    }
    async function apply() {
        if (!job) return;
        setBusy(true);
        setError("");
        try {
            setJob(
                await lemma(job.pod_id).request<ImportStatusResponse>(
                    "POST",
                    "/pods/" +
                        job.pod_id +
                        "/bundle/imports/" +
                        job.import_id +
                        "/apply",
                    {
                        body: {
                            variables: Object.fromEntries(
                                (job.plan?.variables ?? []).map((v) => [
                                    v.name,
                                    values[v.name] ?? v.default ?? "",
                                ]),
                            ),
                            confirm_destructive: confirm,
                        },
                    },
                ),
            );
        } catch (e) {
            setError(e instanceof Error ? e.message : "Installation failed.");
        } finally {
            setBusy(false);
        }
    }
    async function cancel() {
        if (!job) return;
        setBusy(true);
        try {
            await lemma(job.pod_id).request(
                "DELETE",
                "/pods/" + job.pod_id + "/bundle/imports/" + job.import_id,
            );
            setJob({ ...job, status: "CANCELLED" });
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not cancel.");
        } finally {
            setBusy(false);
        }
    }
    if (orgs.isPending) return <p role="status">Loading your organizations…</p>;
    if (orgs.isError)
        return (
            <p role="alert">
                Could not load your organizations.{" "}
                <button onClick={() => void orgs.refetch()}>Retry</button>
            </p>
        );
    if (!orgs.data?.length)
        return (
            <p>
                <a href="/organizations/new">Create an organization</a>, then
                return here to continue.
            </p>
        );
    return (
        <section className="site-card">
            <h2>
                {action === "remix"
                    ? "Choose where to remix"
                    : "Install in your workspace"}
            </h2>
            {!job ? (
                <>
                    <label>
                        Organization
                        <select
                            value={orgId}
                            onChange={(e) => {
                                setOrg(e.target.value);
                                setTarget("new");
                                setCreated(null);
                            }}
                        >
                            {orgs.data.map((o) => (
                                <option key={o.id} value={o.id}>
                                    {o.name}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label>
                        Destination
                        <select
                            value={target}
                            onChange={(e) => {
                                setTarget(e.target.value);
                                setCreated(null);
                            }}
                        >
                            <option value="new">New teammate workspace</option>
                            {pods.data?.map((p) => (
                                <option key={p.id} value={p.id}>
                                    {p.teammate.name}
                                </option>
                            ))}
                        </select>
                    </label>
                    {target === "new" && (
                        <label>
                            Name
                            <input
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                            />
                        </label>
                    )}
                    {action === "remix" && (
                        <p>
                            Source:{" "}
                            {source ||
                                "Missing — open a Remix on Lemma link from an app."}
                        </p>
                    )}
                    <button
                        className="btn btn--primary"
                        disabled={
                            busy ||
                            !orgId ||
                            !name.trim() ||
                            (action === "remix" && !source)
                        }
                        onClick={() => void prepare()}
                    >
                        {busy
                            ? "Preparing…"
                            : action === "remix"
                              ? "Continue with teammate"
                              : "Preview installation"}
                    </button>
                    {/* A remix hands the app to a teammate in a
                        conversation; there is no installation plan on that
                        path, so promising one was wrong there. */}
                    <p>
                        {action === "remix"
                            ? "The teammate picks it up in a new conversation, where you can steer the remix."
                            : "Review the installation plan before applying any resources."}
                    </p>
                </>
            ) : (
                <>
                    <p role="status">
                        {job.status.replaceAll("_", " ")} · {job.progress.done}/
                        {job.progress.total}
                    </p>
                    {job.error && <p role="alert">{job.error}</p>}
                    {[...job.warnings, ...(job.plan?.warnings ?? [])].map(
                        (w, i) => (
                            <p key={i}>{w}</p>
                        ),
                    )}
                    {job.plan && (
                        <>
                            <ul>
                                {job.plan.steps.map((s) => (
                                    <li key={s.index}>
                                        {s.action} {s.kind}: {s.name}
                                        {s.destructive
                                            ? " — replaces existing content"
                                            : ""}
                                        {s.error ? " — " + s.error : ""}
                                    </li>
                                ))}
                            </ul>
                            {job.status === "AWAITING_CONFIRMATION" && (
                                <form
                                    onSubmit={(e) => {
                                        e.preventDefault();
                                        void apply();
                                    }}
                                >
                                    {job.plan.variables.map((v) => (
                                        <ImportVariable
                                            key={v.name}
                                            variable={v}
                                            orgId={orgId}
                                            value={
                                                values[v.name] ??
                                                v.default ??
                                                ""
                                            }
                                            onChange={(value) =>
                                                setValues({
                                                    ...values,
                                                    [v.name]: value,
                                                })
                                            }
                                        />
                                    ))}{" "}
                                    {job.plan.has_destructive_steps && (
                                        <label>
                                            <input
                                                type="checkbox"
                                                checked={confirm}
                                                onChange={(e) =>
                                                    setConfirm(e.target.checked)
                                                }
                                            />
                                            I approve replacing the resources
                                            marked above.
                                        </label>
                                    )}
                                    <button
                                        className="btn btn--primary"
                                        disabled={
                                            busy ||
                                            (job.plan.has_destructive_steps &&
                                                !confirm)
                                        }
                                    >
                                        Apply installation
                                    </button>
                                </form>
                            )}
                        </>
                    )}
                    {job.status === "COMPLETED" ? (
                        <a
                            className="btn btn--primary"
                            href={"/t/" + encodeURIComponent(job.pod_id)}
                        >
                            Open workspace ↗
                        </a>
                    ) : (
                        ![
                            "CANCELLED",
                            "PARTIALLY_CANCELLED",
                            "FAILED",
                        ].includes(job.status) && (
                            <button
                                className="btn"
                                disabled={busy}
                                onClick={() => void cancel()}
                            >
                                Cancel installation
                            </button>
                        )
                    )}
                </>
            )}
            {error && (
                <p role="alert" className="site-error">
                    {error}{" "}
                    {polling && (
                        <button
                            onClick={() => {
                                setError("");
                                setRetry((v) => v + 1);
                            }}
                        >
                            Retry progress
                        </button>
                    )}
                </p>
            )}
        </section>
    );
}

function CreateOrganization() {
    const [name, setName] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    async function create() {
        setBusy(true);
        try {
            const org = await data.createOrg({ name: name.trim() });
            window.location.assign("/t?org=" + encodeURIComponent(org.id));
        } catch (e) {
            setError(
                e instanceof Error
                    ? e.message
                    : "Could not create organization.",
            );
            setBusy(false);
        }
    }
    return (
        <form
            onSubmit={(e) => {
                e.preventDefault();
                void create();
            }}
        >
            <label>
                Organization name
                <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                />
            </label>
            <button
                className="btn btn--primary"
                disabled={busy || !name.trim()}
            >
                {busy ? "Creating…" : "Create organization"}
            </button>
            {error && <p role="alert">{error}</p>}
        </form>
    );
}

function ImportVariable({
    variable: v,
    orgId,
    value,
    onChange,
}: {
    variable: VariableSpec;
    orgId: string;
    value: string;
    onChange: (value: string) => void;
}) {
    const accounts = useQuery({
        queryKey: ["accounts", orgId],
        queryFn: () => data.listAccounts(orgId),
        enabled: v.kind === "account",
    });
    return (
        <label>
            {v.name}
            {v.required ? " *" : ""}
            <small>{v.description}</small>
            {v.kind === "account" ? (
                <>
                    <select
                        required={v.required}
                        value={value}
                        onChange={(e) => onChange(e.target.value)}
                    >
                        <option value="">Choose a connected account</option>
                        {accounts.data
                            ?.filter(
                                (a) =>
                                    a.usable &&
                                    (!v.connector ||
                                        a.connectorId === v.connector),
                            )
                            .map((a) => (
                                <option key={a.id} value={a.id}>
                                    {a.label || a.ref || a.connectorId}
                                </option>
                            ))}
                    </select>
                    <a
                        href={
                            "/t?settings=connectors&org=" +
                            encodeURIComponent(orgId)
                        }
                        target="_blank"
                        rel="noreferrer"
                    >
                        Connect an account ↗
                    </a>{" "}
                    <button
                        type="button"
                        onClick={() => void accounts.refetch()}
                    >
                        Refresh accounts
                    </button>
                    {accounts.isError && (
                        <p role="alert">Could not load connected accounts.</p>
                    )}
                </>
            ) : (
                <input
                    type={v.kind === "secret" ? "password" : "text"}
                    required={v.required}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                />
            )}
        </label>
    );
}
