"use client";
import { useEffect, useState } from "react";
import {
    QueryClient,
    QueryClientProvider,
    useQuery,
} from "@tanstack/react-query";
import {
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
import { connectLinkLabel, failureOf } from "@/data/pod-connectors";
import { Mark } from "@/shell/mark";
import { personalNameFor } from "@/org/arrival";
import { forgetInstall, markInstallArrival, rememberInstall } from "./pending-install";
import type { Pod } from "@/data/types";
import type {
    ConnectorPlan,
    ImportStatusResponse,
    VariableSpec,
} from "../import-types";
import {
    CREATE_INSTALLATION,
    WORKING,
    actionLabel,
    askedConnectors,
    askedVariables,
    blockedBy,
    connectorChoices,
    connectorLine,
    declares,
    defaultPick,
    shareableAccounts,
    type ConnectorPick,
    contentsLine,
    groupSteps,
    humanize,
    installedApp,
    landingPath,
    needsAnswer,
    statusLine,
    suggestedValue,
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
    /* Written before anything can send the person away — sign-in, a new
       organization — so the workspace can offer the install back if the way
       home is lost. Cleared when the install finishes. */
    useEffect(() => {
        try {
            rememberInstall(window.localStorage, {
                path: window.location.pathname + window.location.search,
                title: props.title,
            });
        } catch {
            /* no storage */
        }
    }, [props.title]);
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
                    Sign in or create an account
                </button>
            </Panel>
        );
    return <Journey {...props} />;
}

/** A workspace for somebody who has none, made here rather than on a page of
 *  its own. Somebody who came to install a template has no reason to know
 *  what an organization is; they need somewhere for it to go, named after
 *  them, and the install carrying on. */
function FirstWorkspace({ onMade }: { onMade: () => void }) {
    const session = useSession();
    const suggested = personalNameFor(session.user?.name);
    const [name, setName] = useState(suggested);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    async function make() {
        setBusy(true);
        setError("");
        try {
            const typed = name.trim();
            await data.createOrg({
                name: typed || suggested,
                /* Untouched, the name is ours, and a second Ada gets a
                   workspace rather than a complaint about a name she never
                   chose. */
                derived: !typed || typed === suggested,
            });
            onMade();
        } catch (e) {
            setError(
                e instanceof Error ? e.message : "Could not make your workspace.",
            );
            setBusy(false);
        }
    }
    return (
        <form
            onSubmit={(e) => {
                e.preventDefault();
                void make();
            }}
        >
            <p className={s.muted}>
                First, a workspace for your teammates. It is private to you;
                you can invite people later.
            </p>
            <label className={s.field}>
                <span>Workspace name</span>
                <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={80}
                />
            </label>
            <button className={s.primary} disabled={busy}>
                {busy ? (
                    <>
                        <CircleNotch size={16} className={s.spin} aria-hidden />
                        Making it…
                    </>
                ) : (
                    "Continue"
                )}
            </button>
            {error && (
                <p role="alert" className={s.error}>
                    <Warning size={16} aria-hidden />
                    <span>{error}</span>
                </p>
            )}
        </form>
    );
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
    const [picks, setPicks] = useState<Record<string, ConnectorPick>>({});
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    /* Where to fix the failure above, when the server named somewhere: a
       pod connector refusing as CONNECTION_REQUIRED carries its link. */
    const [errorLink, setErrorLink] = useState({ url: "", code: "" });
    const [retry, setRetry] = useState(0);
    const fail = (e: unknown, fallback: string) => {
        const said = failureOf(e, fallback);
        setError(said.message);
        setErrorLink({ url: said.connectUrl, code: said.code });
    };
    const clearError = () => {
        setError("");
        setErrorLink({ url: "", code: "" });
    };
    /* The plain path shows a sentence and one button. The component list, and
       the names behind it, are one press away for whoever wants them. */
    const [choosing, setChoosing] = useState(false);

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
                if (!alive) return;
                const said = failureOf(e, "Lost track of the installation.");
                setError(said.message);
                setErrorLink({ url: said.connectUrl, code: said.code });
            }
        }
        timer = setTimeout(() => void poll(), 1200);
        return () => {
            alive = false;
            clearTimeout(timer);
        };
    }, [polling, jobId, podId, retry]);

    const finished = job?.status === "COMPLETED";
    useEffect(() => {
        if (!finished) return;
        try {
            forgetInstall(window.localStorage);
        } catch {
            /* no storage */
        }
    }, [finished]);

    async function review() {
        setBusy(true);
        clearError();
        try {
            let id = target === NEW ? created?.id : target;
            if (!id) {
                const pod = await data.createPod(orgId, name.trim());
                setCreated(pod);
                id = pod.id;
            }
            /* Fetched through the pod's `github` connector as the person
               installing, or anonymously for a public repository: no account
               is named here. A failure for want of one comes back on the job
               with its `connect_url`. */
            setJob(
                await lemma(id).request<ImportStatusResponse>(
                    "POST",
                    "/pods/" + id + "/bundle/imports",
                    {
                        body: {
                            kind: "GITHUB",
                            owner,
                            repo,
                        },
                    },
                ),
            );
        } catch (e) {
            fail(e, "Could not read what " + title + " includes.");
        } finally {
            setBusy(false);
        }
    }

    async function install() {
        if (!job?.plan) return;
        setBusy(true);
        clearError();
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
                                    values[v.name] ?? suggestedValue(v, who),
                                ]),
                            ),
                            confirm_destructive: confirm,
                            connectors: connectorChoices(
                                job.plan.connectors ?? [],
                                picks,
                            ),
                        },
                    },
                ),
            );
        } catch (e) {
            fail(e, "The installation failed.");
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
        clearError();
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
            fail(e, "Could not stop it.");
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
                <FirstWorkspace onMade={() => void orgs.refetch()} />
            </Panel>
        );

    const errorLine = error && (
        <p role="alert" className={s.error}>
            <Warning size={16} aria-hidden />
            <span>
                {error}
                {errorLink.url && (
                    <>
                        {" "}
                        <a className={s.link} href={errorLink.url}>
                            {connectLinkLabel(errorLink.code)}
                        </a>
                    </>
                )}
                {polling && (
                    <>
                        {" "}
                        <button
                            className={s.link}
                            onClick={() => {
                                clearError();
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
    /* Only what nothing can stand in for is put in front of the person: a
       name we could not derive. Everything else is named after the teammate,
       and shown prefilled to whoever opens the list. */
    const needsFromYou = choosing
        ? asked
        : asked.filter((v) => needsAnswer(v, who));
    const valueOf = (v: VariableSpec) =>
        values[v.name] ?? suggestedValue(v, who);
    const warnings = [...job.warnings, ...(plan?.warnings ?? [])];
    const markFor = podForMark ?? {
        id: job.pod_id,
        teammate: { name: who, iconUrl: null },
    };
    const cancelled =
        job.status === "CANCELLED" || job.status === "PARTIALLY_CANCELLED";
    const missing = asked.some((v) => v.required && !valueOf(v));
    /* The bundle's connectors. Asked about only where there is a choice to
       make or a reason it cannot install; sharing an account is never
       assumed, so a shared one installs needing an account unless picked. */
    const connectors = plan?.connectors ?? [];
    const askedConns = askedConnectors(connectors);
    const pickOf = (c: ConnectorPlan) => picks[c.slug] ?? defaultPick(c);
    const blocked = connectors.some((c) => blockedBy(c, pickOf(c)) !== null);
    /* The app this install brought, if it brought one. It decides where the
       person goes when they leave this panel — see `landingPath`. */
    const app = installedApp(plan?.steps ?? []);
    const markArrival = () => {
        try {
            markInstallArrival(window.sessionStorage);
        } catch {
            /* no storage */
        }
    };

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
                        {app
                            ? "It came with " + humanize(app) + "."
                            : "Say hello and give it its first job."}
                    </p>
                </div>
                <a
                    className={s.primary}
                    href={landingPath(job.pod_id, plan?.steps ?? [])}
                    onClick={markArrival}
                >
                    Open {app ? humanize(app) : who}
                </a>
                {app && (
                    <a
                        className={s.quiet}
                        href={"/t/" + encodeURIComponent(job.pod_id)}
                        onClick={markArrival}
                    >
                        Or open {who} instead
                    </a>
                )}
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
                    {/* A repository the pod's `github` connector could not
                        reach as you: connecting your account is the fix. */}
                    {job.status === "FAILED" && job.connect_url && (
                        <>
                            {" "}
                            <a className={s.link} href={job.connect_url}>
                                {connectLinkLabel(job.error_code ?? "")}
                            </a>
                        </>
                    )}
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
            {reviewing && (
                <p className={s.muted}>
                    {who === title ? "Everything" : who + " gets everything"}{" "}
                    {title} brings
                    {groups.length > 0 ? " — " + contentsLine(groups) : ""}.
                </p>
            )}
            {!reviewing && (
                <Progress done={job.progress.done} total={job.progress.total} />
            )}
            {warnings.length > 0 && <Warnings list={warnings} />}
            {(choosing || !reviewing) && groups.length > 0 && (
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
            {reviewing && groups.length > 0 && (
                <button
                    type="button"
                    className={s.quiet}
                    aria-expanded={choosing}
                    onClick={() => setChoosing((on) => !on)}
                >
                    {choosing
                        ? "Hide the details"
                        : "I'm a developer — show me the details"}
                </button>
            )}
            {reviewing && plan && (
                <form
                    className={s.confirm}
                    onSubmit={(e) => {
                        e.preventDefault();
                        void install();
                    }}
                >
                    {needsFromYou.length > 0 && (
                        <fieldset className={s.needs}>
                            <legend>Needs from you</legend>
                            {needsFromYou.map((v) => (
                                <Variable
                                    key={v.name}
                                    variable={v}
                                    value={valueOf(v)}
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
                    {askedConns.length > 0 && (
                        <fieldset className={s.needs}>
                            <legend>Connections</legend>
                            {askedConns.map((c) => (
                                <ConnectorField
                                    key={c.slug}
                                    connector={c}
                                    pick={pickOf(c)}
                                    onChange={(pick) =>
                                        setPicks({ ...picks, [c.slug]: pick })
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
                                blocked ||
                                (plan.has_destructive_steps && !confirm)
                            }
                        >
                            {busy ? "Starting…" : "Install everything"}
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
    value,
    onChange,
}: {
    variable: VariableSpec;
    value: string;
    onChange: (value: string) => void;
}) {
    return (
        <label className={s.field}>
            <span>
                {humanize(v.name)}
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
}

/** One bundled connector: which of the organization's installations it maps
 *  to, and for a shared one whether to share one of your own accounts. */
function ConnectorField({
    connector: c,
    pick,
    onChange,
}: {
    connector: ConnectorPlan;
    pick: ConnectorPick;
    onChange: (pick: ConnectorPick) => void;
}) {
    const reason = blockedBy(c, pick);
    const pickable =
        declares(c) &&
        (c.installations.length !== 1 || c.can_create_installation);
    const accounts = shareableAccounts(c, pick);
    const sharing =
        declares(c) &&
        c.mode === "SHARED" &&
        pick.installation !== "" &&
        pick.installation !== CREATE_INSTALLATION;
    return (
        <div className={s.field}>
            <span>
                {c.slug}
                <em> {c.mode === "SHARED" ? "shared" : "per member"}</em>
            </span>
            <small>{connectorLine(c)}</small>
            {pickable && (
                <select
                    aria-label={"Installation for " + c.slug}
                    value={pick.installation}
                    onChange={(e) =>
                        onChange({ installation: e.target.value, share: "" })
                    }
                >
                    {c.installations.length === 0 && (
                        <option value="">Choose…</option>
                    )}
                    {c.installations.map((o) => (
                        <option key={o.auth_config_id} value={o.auth_config_id}>
                            {o.name}
                            {o.is_default ? " (default)" : ""}
                        </option>
                    ))}
                    {c.can_create_installation && (
                        <option value={CREATE_INSTALLATION}>
                            Install Lemma&apos;s {humanize(c.connector)} app
                        </option>
                    )}
                </select>
            )}
            {sharing && (
                <select
                    aria-label={"Account for " + c.slug}
                    value={pick.share}
                    onChange={(e) =>
                        onChange({ ...pick, share: e.target.value })
                    }
                >
                    <option value="">
                        Don&apos;t share an account now — it waits for one
                    </option>
                    {accounts.map((a) => (
                        <option key={a.account_id} value={a.account_id}>
                            Share {a.label ?? "my account"}
                        </option>
                    ))}
                </select>
            )}
            {reason && <small className={s.stepError}>{reason}</small>}
        </div>
    );
}
