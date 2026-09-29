"use client";
import { useEffect, useState } from "react";
import {
    QueryClient,
    QueryClientProvider,
    useQuery,
} from "@tanstack/react-query";
import {
    ArrowSquareOut,
    ArrowsClockwise,
    Check,
    CircleNotch,
    Plus,
    Warning,
    X,
} from "@phosphor-icons/react";
import { useSession } from "@/session/session";
import { hasApiUrl, lemma } from "@/session/client";
import { source as data } from "@/data";
import { Mark } from "@/shell/mark";
import type { Pod } from "@/data/types";
import type { ImportStatusResponse, VariableSpec } from "../import-types";
import {
    WORKING,
    actionLabel,
    askedVariables,
    groupSteps,
    humanize,
    statusLine,
    teammateName,
} from "./plan";
import s from "./import.module.css";

type Props = { owner: string; repo: string; title: string };

const NEW = "new";

export function InstallPanel(props: Props) {
    const [client] = useState(
        () =>
            new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    );
    return (
        <QueryClientProvider client={client}>
            <Gate {...props} />
        </QueryClientProvider>
    );
}

function Gate(props: Props) {
    const session = useSession();
    if (!hasApiUrl() || data.label === "sample")
        return (
            <Panel>
                <h2 className={s.panelTitle}>Install {props.title}</h2>
                <p className={s.muted}>
                    Installing needs a live Lemma.{" "}
                    <a href="/connect">Connection settings</a>
                </p>
            </Panel>
        );
    if (session.status === "loading")
        return (
            <Panel>
                <p role="status" className={s.muted}>
                    Checking your session…
                </p>
            </Panel>
        );
    if (session.status !== "in")
        return (
            <Panel>
                <h2 className={s.panelTitle}>Install {props.title}</h2>
                <p className={s.muted}>
                    Give it to a new or existing teammate. You see everything it
                    adds before anything is installed.
                </p>
                <button className={s.primary} onClick={session.signIn}>
                    Sign in to install
                </button>
            </Panel>
        );
    return <Journey {...props} />;
}

function Panel({ children }: { children: React.ReactNode }) {
    return <div className={s.panel}>{children}</div>;
}

function Steps({ at }: { at: 0 | 1 | 2 | 3 }) {
    const names = ["Choose", "Review", "Install"];
    return (
        <ol className={s.steps} aria-label="Installation steps">
            {names.map((name, i) => (
                <li
                    key={name}
                    data-state={i < at ? "done" : i === at ? "now" : "next"}
                    aria-current={i === at ? "step" : undefined}
                >
                    <span aria-hidden>
                        {i < at ? <Check size={11} /> : i + 1}
                    </span>
                    {name}
                </li>
            ))}
        </ol>
    );
}

function Journey({ repo, owner, title }: Props) {
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
    const [picked, setPicked] = useState<string | null>(null);
    const wantsExisting =
        typeof window !== "undefined" &&
        new URLSearchParams(window.location.search).get("destination") ===
            "existing";
    const target =
        picked ?? (wantsExisting ? (pods.data?.[0]?.id ?? NEW) : NEW);
    const [name, setName] = useState(() => title || teammateName(repo));
    /* The teammate this page made for the install. Kept, so going back to
       the choice and on again reuses it rather than hiring a second one. */
    const [created, setCreated] = useState<Pod | null>(null);
    const [job, setJob] = useState<ImportStatusResponse | null>(null);
    const [values, setValues] = useState<Record<string, string>>({});
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [retry, setRetry] = useState(0);

    const existing = pods.data?.find((p) => p.id === target) ?? null;
    const who =
        target === NEW
            ? (created?.teammate.name ?? name.trim())
            : (existing?.teammate.name ?? "your teammate");
    const podForMark = target === NEW ? created : existing;

    const jobId = job?.import_id;
    const podId = job?.pod_id;
    const polling = !!job && WORKING.includes(job.status);
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
                    timer = setTimeout(() => void poll(), 1200);
                }
            } catch (e) {
                if (alive)
                    setError(
                        e instanceof Error
                            ? e.message
                            : "Lost track of the installation.",
                    );
            }
        }
        timer = setTimeout(() => void poll(), 1200);
        return () => {
            alive = false;
            clearTimeout(timer);
        };
    }, [polling, jobId, podId, retry]);

    async function review() {
        setBusy(true);
        setError("");
        try {
            let id = target === NEW ? created?.id : target;
            if (!id) {
                const pod = await data.createPod(orgId, name.trim());
                setCreated(pod);
                id = pod.id;
            }
            setJob(
                await lemma(id).request<ImportStatusResponse>(
                    "POST",
                    "/pods/" + id + "/bundle/imports",
                    { body: { kind: "GITHUB", owner, repo } },
                ),
            );
        } catch (e) {
            setError(
                e instanceof Error
                    ? e.message
                    : "Could not read what " + title + " includes.",
            );
        } finally {
            setBusy(false);
        }
    }

    async function install() {
        if (!job?.plan) return;
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
                                job.plan.variables.map((v) => [
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
            setError(
                e instanceof Error ? e.message : "The installation failed.",
            );
        } finally {
            setBusy(false);
        }
    }

    /** Back to the choice. A job still waiting on review is withdrawn so it
     *  does not sit open against the teammate. */
    async function back() {
        if (job && job.status === "AWAITING_CONFIRMATION") {
            setBusy(true);
            try {
                await lemma(job.pod_id).request(
                    "DELETE",
                    "/pods/" + job.pod_id + "/bundle/imports/" + job.import_id,
                );
            } catch {
                /* Leaving it open is harmless; a new review starts a new job. */
            } finally {
                setBusy(false);
            }
        }
        setJob(null);
        setConfirm(false);
        setError("");
    }

    async function stop() {
        if (!job) return;
        setBusy(true);
        try {
            await lemma(job.pod_id).request(
                "DELETE",
                "/pods/" + job.pod_id + "/bundle/imports/" + job.import_id,
            );
            setJob({ ...job, status: "CANCELLED" });
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not stop it.");
        } finally {
            setBusy(false);
        }
    }

    if (orgs.isPending)
        return (
            <Panel>
                <p role="status" className={s.muted}>
                    Loading your teammates…
                </p>
            </Panel>
        );
    if (orgs.isError)
        return (
            <Panel>
                <p role="alert" className={s.muted}>
                    Could not load your organizations.
                </p>
                <button
                    className={s.secondary}
                    onClick={() => void orgs.refetch()}
                >
                    Try again
                </button>
            </Panel>
        );
    if (!orgs.data?.length)
        return (
            <Panel>
                <h2 className={s.panelTitle}>Install {title}</h2>
                <p className={s.muted}>
                    You need an organization to hire a teammate into.
                </p>
                <a className={s.primary} href="/organizations/new">
                    Create an organization
                </a>
            </Panel>
        );

    const errorLine = error && (
        <p role="alert" className={s.error}>
            <Warning size={16} aria-hidden />
            <span>
                {error}
                {polling && (
                    <>
                        {" "}
                        <button
                            className={s.link}
                            onClick={() => {
                                setError("");
                                setRetry((v) => v + 1);
                            }}
                        >
                            Check again
                        </button>
                    </>
                )}
            </span>
        </p>
    );

    /* ── Choose ─────────────────────────────────────────────────────── */
    if (!job)
        return (
            <Panel>
                <Steps at={0} />
                <h2 className={s.panelTitle}>Who gets {title}?</h2>
                {orgs.data.length > 1 && (
                    <label className={s.field}>
                        <span>Organization</span>
                        <select
                            value={orgId}
                            onChange={(e) => {
                                setOrg(e.target.value);
                                setPicked(NEW);
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
                )}
                <div
                    className={s.choices}
                    role="radiogroup"
                    aria-label={"Who gets " + title}
                >
                    <label
                        className={s.choice}
                        data-on={target === NEW || undefined}
                    >
                        <input
                            type="radio"
                            name="target"
                            checked={target === NEW}
                            onChange={() => setPicked(NEW)}
                        />
                        <span className={s.newMark} aria-hidden>
                            <Plus size={16} />
                        </span>
                        <span className={s.choiceText}>
                            <b>Hire a new teammate</b>
                            <small>Starts with only what {title} brings</small>
                        </span>
                    </label>
                    {target === NEW && (
                        <label className={s.nameField}>
                            <span>Name</span>
                            <input
                                value={created?.teammate.name ?? name}
                                disabled={!!created}
                                onChange={(e) => setName(e.target.value)}
                                maxLength={60}
                            />
                        </label>
                    )}
                    {pods.isPending && (
                        <p className={s.muted} role="status">
                            Loading your teammates…
                        </p>
                    )}
                    {pods.isError && (
                        <p className={s.muted} role="alert">
                            Could not load your teammates.{" "}
                            <button
                                className={s.link}
                                onClick={() => void pods.refetch()}
                            >
                                Try again
                            </button>
                        </p>
                    )}
                    {!!pods.data?.length && (
                        <p className={s.or}>or give it to</p>
                    )}
                    <div className={s.existing}>
                        {pods.data?.map((p) => (
                            <label
                                key={p.id}
                                className={s.choice}
                                data-on={target === p.id || undefined}
                            >
                                <input
                                    type="radio"
                                    name="target"
                                    checked={target === p.id}
                                    onChange={() => setPicked(p.id)}
                                />
                                <Mark
                                    seed={p.id}
                                    name={p.teammate.name}
                                    icon={p.teammate.iconUrl}
                                    size={32}
                                    still
                                />
                                <span className={s.choiceText}>
                                    <b>{p.teammate.name}</b>
                                    <small>Adds to what it already has</small>
                                </span>
                            </label>
                        ))}
                    </div>
                </div>
                <div className={s.foot}>
                    <button
                        className={s.primary}
                        disabled={
                            busy || !orgId || (target === NEW && !name.trim())
                        }
                        onClick={() => void review()}
                    >
                        {busy ? (
                            <>
                                <CircleNotch
                                    size={16}
                                    className={s.spin}
                                    aria-hidden
                                />
                                Reading {title}…
                            </>
                        ) : (
                            "Review what's included"
                        )}
                    </button>
                    <p className={s.fine}>
                        Nothing is added until you approve it.
                    </p>
                    {errorLine}
                </div>
            </Panel>
        );

    const plan = job.plan;
    const groups = plan ? groupSteps(plan.steps) : [];
    const asked = plan ? askedVariables(plan.variables) : [];
    const warnings = [...job.warnings, ...(plan?.warnings ?? [])];
    const markFor = podForMark ?? {
        id: job.pod_id,
        teammate: { name: who, iconUrl: null },
    };
    const cancelled =
        job.status === "CANCELLED" || job.status === "PARTIALLY_CANCELLED";
    const missing = asked.some(
        (v) => v.required && !(values[v.name] ?? v.default ?? ""),
    );

    /* ── Done ───────────────────────────────────────────────────────── */
    if (job.status === "COMPLETED")
        return (
            <Panel>
                <Steps at={3} />
                <div className={s.done}>
                    <Mark
                        seed={markFor.id}
                        name={who}
                        icon={markFor.teammate.iconUrl}
                        size={56}
                        greeting={1}
                    />
                    <h2 className={s.panelTitle}>{who} is ready</h2>
                    <p className={s.muted}>
                        {who !== title && title + " is installed. "}
                        Say hello and give it its first job.
                    </p>
                </div>
                <a
                    className={s.primary}
                    href={"/t/" + encodeURIComponent(job.pod_id)}
                >
                    Open {who}
                </a>
                {warnings.length > 0 && <Warnings list={warnings} />}
            </Panel>
        );

    /* ── Stopped ────────────────────────────────────────────────────── */
    if (job.status === "FAILED" || cancelled)
        return (
            <Panel>
                <h2 className={s.panelTitle}>{statusLine(job.status)}</h2>
                <p className={s.muted}>
                    {job.status === "FAILED"
                        ? (job.error ??
                          "Something went wrong while installing.")
                        : job.status === "PARTIALLY_CANCELLED"
                          ? "What was already installed stays with " + who + "."
                          : "Nothing was added."}
                </p>
                <button className={s.primary} onClick={() => void back()}>
                    <ArrowsClockwise size={16} aria-hidden />
                    Start again
                </button>
                {errorLine}
            </Panel>
        );

    /* ── Review, and progress while planning or installing ──────────── */
    const reviewing = job.status === "AWAITING_CONFIRMATION";
    const installing = job.status === "APPLYING";
    return (
        <Panel>
            <Steps at={installing ? 2 : 1} />
            <div className={s.for}>
                <Mark
                    seed={markFor.id}
                    name={who}
                    icon={markFor.teammate.iconUrl}
                    size={28}
                    still
                />
                <span>
                    {reviewing || installing ? "For " : "Preparing for "}
                    <b>{who}</b>
                </span>
            </div>
            <h2 className={s.panelTitle}>
                {reviewing ? "What's included" : statusLine(job.status)}
            </h2>
            {!reviewing && (
                <Progress done={job.progress.done} total={job.progress.total} />
            )}
            {warnings.length > 0 && <Warnings list={warnings} />}
            {groups.length > 0 && (
                <div className={s.plan}>
                    {groups.map((g) => (
                        <section key={g.kind}>
                            <h3>
                                {g.label} <span>{g.steps.length}</span>
                            </h3>
                            <ul>
                                {g.steps.map((step) => (
                                    <li key={step.index}>
                                        <span className={s.stepName}>
                                            {step.name}
                                        </span>
                                        {installing ? (
                                            <StepState status={step.status} />
                                        ) : (
                                            <span
                                                className={s.badge}
                                                data-tone={
                                                    step.destructive
                                                        ? "bad"
                                                        : step.action === "SKIP"
                                                          ? "quiet"
                                                          : undefined
                                                }
                                            >
                                                {actionLabel(step)}
                                            </span>
                                        )}
                                        {step.error && (
                                            <small className={s.stepError}>
                                                {step.error}
                                            </small>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        </section>
                    ))}
                </div>
            )}
            {reviewing && plan && (
                <form
                    className={s.confirm}
                    onSubmit={(e) => {
                        e.preventDefault();
                        void install();
                    }}
                >
                    {asked.length > 0 && (
                        <fieldset className={s.needs}>
                            <legend>Needs from you</legend>
                            {asked.map((v) => (
                                <Variable
                                    key={v.name}
                                    variable={v}
                                    orgId={orgId}
                                    value={values[v.name] ?? v.default ?? ""}
                                    onChange={(value) =>
                                        setValues({
                                            ...values,
                                            [v.name]: value,
                                        })
                                    }
                                />
                            ))}
                        </fieldset>
                    )}
                    {plan.has_destructive_steps && (
                        <label className={s.check}>
                            <input
                                type="checkbox"
                                checked={confirm}
                                onChange={(e) => setConfirm(e.target.checked)}
                            />
                            <span>
                                Replace what {who} already has where marked{" "}
                                <em>Replaces yours</em>.
                            </span>
                        </label>
                    )}
                    <div className={s.row + " " + s.foot}>
                        <button
                            className={s.primary}
                            disabled={
                                busy ||
                                missing ||
                                (plan.has_destructive_steps && !confirm)
                            }
                        >
                            {busy ? "Starting…" : "Install"}
                        </button>
                        <button
                            type="button"
                            className={s.secondary}
                            disabled={busy}
                            onClick={() => void back()}
                        >
                            Back
                        </button>
                    </div>
                </form>
            )}
            {polling && (
                <button
                    className={s.quiet}
                    disabled={busy}
                    onClick={() => void stop()}
                >
                    <X size={14} aria-hidden />
                    Stop
                </button>
            )}
            {errorLine}
        </Panel>
    );
}

function Progress({ done, total }: { done: number; total: number }) {
    const known = total > 0;
    return (
        <div className={s.progress}>
            <div
                className={s.bar}
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={known ? total : undefined}
                aria-valuenow={known ? done : undefined}
                data-indeterminate={!known || undefined}
            >
                <i
                    style={
                        known
                            ? { width: (done / total) * 100 + "%" }
                            : undefined
                    }
                />
            </div>
            {known && (
                <small>
                    {done} of {total}
                </small>
            )}
        </div>
    );
}

function StepState({ status }: { status: string }) {
    if (status === "DONE")
        return (
            <span className={s.badge} data-tone="ok">
                <Check size={12} aria-hidden /> Done
            </span>
        );
    if (status === "RUNNING")
        return (
            <span className={s.badge}>
                <CircleNotch size={12} className={s.spin} aria-hidden />{" "}
                Installing
            </span>
        );
    if (status === "FAILED")
        return (
            <span className={s.badge} data-tone="bad">
                Failed
            </span>
        );
    if (status === "SKIPPED")
        return (
            <span className={s.badge} data-tone="quiet">
                Skipped
            </span>
        );
    return (
        <span className={s.badge} data-tone="quiet">
            Waiting
        </span>
    );
}

function Warnings({ list }: { list: string[] }) {
    return (
        <ul className={s.warnings}>
            {list.map((w, i) => (
                <li key={i}>
                    <Warning size={14} aria-hidden />
                    <span>{w}</span>
                </li>
            ))}
        </ul>
    );
}

function Variable({
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
    const label =
        v.kind === "account" && v.connector
            ? humanize(v.connector) + " account"
            : humanize(v.name);
    if (v.kind !== "account")
        return (
            <label className={s.field}>
                <span>
                    {label}
                    {!v.required && <em> optional</em>}
                </span>
                {v.description && <small>{v.description}</small>}
                <input
                    required={v.required}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                />
            </label>
        );
    const usable =
        accounts.data?.filter(
            (a) => a.usable && (!v.connector || a.connectorId === v.connector),
        ) ?? [];
    return (
        <label className={s.field}>
            <span>{label}</span>
            {v.description && <small>{v.description}</small>}
            <select
                required={v.required}
                value={value}
                onChange={(e) => onChange(e.target.value)}
            >
                <option value="">
                    {accounts.isPending
                        ? "Loading accounts…"
                        : usable.length
                          ? "Choose an account"
                          : "No " + label.toLowerCase() + " connected yet"}
                </option>
                {usable.map((a) => (
                    <option key={a.id} value={a.id}>
                        {a.label || a.ref || a.connectorId}
                    </option>
                ))}
            </select>
            <span className={s.fieldActions}>
                <a
                    href={
                        "/t?settings=connectors&org=" +
                        encodeURIComponent(orgId)
                    }
                    target="_blank"
                    rel="noreferrer"
                >
                    Connect one <ArrowSquareOut size={12} aria-hidden />
                </a>
                <button
                    type="button"
                    className={s.link}
                    onClick={() => void accounts.refetch()}
                >
                    Refresh
                </button>
            </span>
            {accounts.isError && (
                <small role="alert" className={s.stepError}>
                    Could not load connected accounts.
                </small>
            )}
        </label>
    );
}
