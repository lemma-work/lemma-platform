"use client";

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { buildSchemaFormFields, buildSchemaFormPayload, buildSchemaFormValues, type SchemaFormField } from "lemma-sdk";
import { source, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { Character, characterForSeed, characterFromUrl } from "@/shell/character";
import { markTint } from "@/shell/mark-tint";
import { identityVariantSeed, parseResourceIcon } from "@/shell/resource-icon";
import { HOST_PERMISSION_WINDOW_MS, hostPermissionExpired, interactionHeading, type ApprovalDecision, type AskOption, type AskQuestion } from "@/thread/approval";
import { Prose } from "@/thread/markdown";
import { buildTurns, openInteraction, type Interaction } from "@/thread/turns";
import { CONVERSATION_ASKS } from "@/thread/waiting-on-you";
import { Sk, SHOW_AFTER_MS, useLasted } from "@/ui/skeleton";
import { sayRefusal } from "@/workflow/runs";
import { Problem } from "./calm";
import { answerLine } from "./data";
import { ageOf, answersHere, verbOf, viewOfItem, type Go, type QueueItem } from "./model";
import { Icon, to } from "./parts";

const DAY_MS = 86_400_000;
/** How long a row says what its answer did before it goes. */
const SAID_MS = 1500;
/** The collapse itself; `.nrow` in needs-page.css runs it in 320ms. */
const LEAVE_MS = 340;

type Phase = "said" | "leaving" | "left";

/** Answered here: what the row now says, for the wait it answered. A new
 *  wait under the same key starts again (its `sinceMs` differs). */
interface Done {
    said: string;
    /** The wait as it was answered. */
    item: QueueItem;
    phase: Phase;
}

const live = () => source.label === "live";

/** Read what waits on you again: the asks (which the organization's list
 *  is kept under), and the automations' waits when it was one of theirs. */
function reread(cache: QueryClient, waits: boolean) {
    void cache.invalidateQueries({ queryKey: [CONVERSATION_ASKS] });
    if (waits) void cache.invalidateQueries({ queryKey: ["workflow-waiting"] });
}

function stillWanted(): boolean {
    return typeof window !== "undefined" && typeof window.matchMedia === "function"
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** What a row says under its title: whose it is and where it was asked,
 *  without the Lemma's name twice when the detail already says it. */
export function saidOf(item: Pick<QueueItem, "podName" | "detail" | "conversationId" | "runId" | "contactId" | "line">, showLemma: boolean): string {
    const asked = item.conversationId && !item.runId && !item.contactId && !item.line;
    const where = item.detail ? (asked ? "In " + item.detail : item.detail) : null;
    const named = item.podName && where?.toLowerCase().includes(item.podName.toLowerCase());
    return [showLemma && !named ? item.podName : null, where].filter(Boolean).join(" · ");
}

/** One thing waiting on you, answered where it is listed.
 *
 *  The title opens where it was asked; the button opens the row onto what
 *  answers it -- an approval in full, a question's choices and a box for
 *  words, a form's fields -- except a person waiting on a person, which opens
 *  their chat to reply in. Once the answer is accepted the row says what it
 *  did, in green, then folds away; the list is read again at once rather
 *  than at its next check. A failed answer stays open, with what was typed,
 *  and says so in the row. */
export function NeedRow({ item, pods, now, go, showLemma = true, onAnswered, onLeft }: {
    item: QueueItem;
    pods: readonly Pod[];
    now: number;
    go: Go;
    /** Off on a Lemma's own page, where every row is that Lemma's. */
    showLemma?: boolean;
    /** The answer went through. A page that keeps the row drawn while it
     *  says so (Needs you) reads the list again at once; without one, the
     *  list is read again once the row has gone, so it does not vanish
     *  before it has said what happened. */
    onAnswered?: (item: QueueItem) => void;
    /** It has said so and folded away. */
    onLeft?: (item: QueueItem) => void;
}) {
    const cache = useQueryClient();
    const panelId = useId();
    const [open, setOpen] = useState(false);
    const [done, setDone] = useState<Done | null>(null);
    const here = answersHere(item);
    const pod = pods.find((one) => one.id === item.podId) ?? null;
    const opens = to(viewOfItem(item), go);
    const said = saidOf(item, showLemma);
    const settled = done && done.item.sinceMs === item.sinceMs ? done : null;

    /* The list is read again either at once or once the row has gone (see
       `onAnswered`) -- and if the page closes before then, as it closes.
       `owed` says whether the automations' waits are read again too. */
    const owed = useRef<{ waits: boolean } | null>(null);
    const settle = () => {
        if (!owed.current) return;
        reread(cache, owed.current.waits);
        owed.current = null;
    };
    const latest = useRef({ onLeft, settle });
    useEffect(() => {
        latest.current = { onLeft, settle };
    });
    useEffect(() => () => latest.current.settle(), []);
    useEffect(() => {
        if (!done || done.phase === "left") return;
        const next: Phase = done.phase === "said" && !stillWanted() ? "leaving" : "left";
        const timer = window.setTimeout(() => {
            setDone({ ...done, phase: next });
            if (next !== "left") return;
            latest.current.settle();
            latest.current.onLeft?.(done.item);
        }, done.phase === "said" ? SAID_MS : LEAVE_MS);
        return () => window.clearTimeout(timer);
    }, [done]);

    const answered = (words: string) => {
        setOpen(false);
        setDone({ said: words, item, phase: "said" });
        const waits = Boolean(item.runId);
        if (onAnswered) {
            onAnswered(item);
            reread(cache, waits);
        } else {
            owed.current = { waits };
        }
    };

    if (settled?.phase === "left") return null;

    const old = now - item.sinceMs > DAY_MS;
    const age = ageOf(item.sinceMs, now);
    const face = pod ?? { id: item.podId, name: item.podName, iconUrl: null };
    return (
        <div className="nrow" data-face={showLemma || undefined} data-open={open || undefined} data-done={settled ? "" : undefined} data-leaving={settled?.phase === "leaving" || undefined}>
            <div className="nrow__in">
                {settled ? (
                    <div className="nrow__top">
                        <p className="ndone" role="status"><Icon name="check" size={18} />{settled.said}</p>
                    </div>
                ) : (
                    <>
                        <div className="nrow__top">
                            {showLemma && <StillFace pod={face} size={30} />}
                            <span className="nrow__w">
                                <a className="nrow__t" {...opens}>{item.title}</a>
                                {said && <span className="nrow__s">{said}</span>}
                            </span>
                            <span className={"nage" + (old ? " nage--old" : "")} title={`Waiting ${age}`}>{age}</span>
                            {here && pod ? (
                                <button type="button" className={"b b--s nrow__go " + (open ? "b--quiet" : "b--secondary")} aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((was) => !was)}>
                                    {open ? "Close" : verbOf(item)}
                                </button>
                            ) : (
                                <a className="b b--s b--secondary nrow__go" {...opens}>{verbOf(item)}<Icon name="next" size={15} /></a>
                            )}
                        </div>
                        {open && pod && (
                            <div className="npanel" id={panelId}>
                                {here === "line" && item.line ? (
                                    <LineHere pod={pod} line={item.line} title={item.title} onAnswered={answered} />
                                ) : here === "form" && item.wait && item.runId ? (
                                    <FormHere pod={pod} runId={item.runId} wait={item.wait} onAnswered={answered} />
                                ) : item.conversationId ? (
                                    <AskHere pod={pod} conversationId={item.conversationId} title={item.title} now={now} onAnswered={answered} />
                                ) : null}
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

/** A Lemma's face, still: its character on its own tint (as `TeammateFace`
 *  draws it with `live` off), without loading the animated rigs -- a list
 *  never draws those, and a page that only lists should not pull them in. */
export function StillFace({ pod, size }: { pod: Pick<Pod, "id" | "name" | "iconUrl">; size: number }) {
    const [broken, setBroken] = useState(false);
    const icon = parseResourceIcon(pod.iconUrl);
    const tint = markTint(pod.id);
    const inner = Math.round(size * 0.86);
    const cast = icon?.kind === "url" ? characterFromUrl(icon.url) : undefined;
    let body: ReactNode;
    if (icon?.kind === "glyph") {
        body = <span className="nface__glyph" style={{ color: tint.color, fontSize: Math.round(size * 0.52) }}>{icon.glyph}</span>;
    } else if (icon?.kind === "url" && !cast && !broken) {
        body = <img src={icon.url} alt="" width={inner} height={inner} referrerPolicy="no-referrer" loading="lazy" onError={() => setBroken(true)} />;
    } else {
        const seeded = characterForSeed(identityVariantSeed(pod.id, icon?.kind === "identity" ? icon.variant : 0));
        body = <Character character={cast ?? seeded} size={inner} />;
    }
    return (
        <span className="tface nface" style={{ width: size, height: size, background: tint.background }} aria-hidden="true">
            {body}
        </span>
    );
}

/* ── answering ──────────────────────────────────────────────────────── */

/** Sends an answer once at a time, and keeps a failed one to try again. */
function useSend<T>(send: (value: T) => Promise<void>, done: (value: T) => void) {
    const [busy, setBusy] = useState<T | null>(null);
    const [failed, setFailed] = useState<{ value: T; problem: unknown } | null>(null);
    const run = async (value: T) => {
        if (busy !== null) return;
        setBusy(value);
        setFailed(null);
        try {
            await send(value);
        } catch (problem) {
            setFailed({ value, problem });
            setBusy(null);
            return;
        }
        setBusy(null);
        done(value);
    };
    const retry = () => {
        if (failed) void run(failed.value);
    };
    return { busy, failed, run, retry };
}

function NotSent({ pod, busy, onRetry }: { pod: Pod; busy: boolean; onRetry: () => void }) {
    return <Problem onRetry={onRetry} retrying={busy}>Couldn’t send your answer. {pod.name} is still waiting on it.</Problem>;
}

/** What answers it, while it is read. Nothing for the first moment. */
function Reading({ label }: { label: string }) {
    const shown = useLasted(true, SHOW_AFTER_MS);
    return (
        <div className="nreading" role="status" aria-label={label}>
            {shown && <><Sk w="62%" /><Sk w="38%" /></>}
        </div>
    );
}

/** An approval or a question a Lemma stopped on in a chat, read from the
 *  chat's own messages (the pause is the tool call, so the transcript holds
 *  everything) and answered with the same call the chat makes. The answer
 *  is queued for the run to carry on with, so nothing here keeps it going. */
function AskHere({ pod, conversationId, title, now, onAnswered }: {
    pod: Pod;
    conversationId: string;
    title: string;
    now: number;
    onAnswered: (said: string) => void;
}) {
    const cache = useQueryClient();
    const chat = useQuery({
        queryKey: ["outcome-ask", pod.id, conversationId],
        queryFn: () => source.getConversation(pod.id, pod.teammate, conversationId),
        staleTime: 0,
    });
    const interaction = useMemo(() => openInteraction(buildTurns(chat.data?.messages ?? [])), [chat.data]);
    const gone = chat.isSuccess && !interaction;
    useEffect(() => {
        if (gone) void cache.invalidateQueries({ queryKey: [CONVERSATION_ASKS] });
    }, [gone, cache]);

    const resolve = async (id: string, decision: ApprovalDecision, response?: Record<string, unknown>) => {
        if (!live()) return;
        await lemma(pod.id).conversations.approvals.resolve(conversationId, id, { decision, response: response ?? {} }, { pod_id: pod.id });
    };

    if (chat.isPending) return <Reading label={`Reading what ${pod.name} asked`} />;
    if (chat.isError) {
        return <Problem onRetry={() => void chat.refetch()} retrying={chat.isFetching}>Couldn’t read what {pod.name} asked. It’s still in your chat with it.</Problem>;
    }
    if (!interaction) return <p className="nnote">Already answered. It leaves this list in a moment.</p>;
    if (interaction.kind === "question") {
        return <Asked pod={pod} title={title} questions={interaction.questions} send={(answers) => resolve(interaction.id, "APPROVE_ONCE", { answers })} onAnswered={onAnswered} />;
    }
    return <Approval pod={pod} title={title} interaction={interaction} now={now} resolve={resolve} onAnswered={onAnswered} />;
}

/** An approval, in full before the yes: what it will do, why, and the
 *  arguments of the call that will actually run. */
function Approval({ pod, title, interaction, now, resolve, onAnswered }: {
    pod: Pod;
    title: string;
    interaction: Interaction;
    now: number;
    resolve: (id: string, decision: ApprovalDecision) => Promise<void>;
    onAnswered: (said: string) => void;
}) {
    const { details } = interaction;
    const { busy, failed, run, retry } = useSend<ApprovalDecision>(
        (decision) => resolve(interaction.id, decision),
        (decision) => onAnswered(decision === "DENY" ? `Denied. ${pod.name} goes on without it.` : `Approved. ${pod.name} carries on.`),
    );
    const heading = interactionHeading("approval", details.title, pod.name, false);
    const deadline = details.hostPermission && interaction.askedAtMs !== undefined ? interaction.askedAtMs + HOST_PERMISSION_WINDOW_MS : undefined;
    const expired = hostPermissionExpired(details, { askedAtMs: interaction.askedAtMs, nowMs: now, runEnded: false });
    const what = heading !== title ? heading : null;
    const shows = what || details.request || details.params.length > 0 || details.grantNote;
    return (
        <>
            {shows && (
                <div className="nask">
                    {what && <p className="nask__t">{what}</p>}
                    {details.request && <p className="nask__what">{details.request}</p>}
                    {details.params.length > 0 && (
                        <dl className="nask__kv">
                            {details.params.map((param) => (
                                <div key={param.name}><dt>{param.name}</dt><dd>{param.value}</dd></div>
                            ))}
                        </dl>
                    )}
                    {details.grantNote && <p className="nask__body">{details.grantNote}</p>}
                </div>
            )}
            {expired ? (
                <p className="nnote">Too late to answer here: {pod.name} went on without it.</p>
            ) : (
                <>
                    {deadline !== undefined && (
                        <p className="nnote">Answer by {new Date(deadline).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}, or {pod.name} goes on without it.</p>
                    )}
                    {failed && <NotSent pod={pod} busy={busy !== null} onRetry={retry} />}
                    <div className="nacts">
                        <button type="button" className="b b--m b--secondary" disabled={busy !== null} onClick={() => void run("DENY")}>
                            {busy === "DENY" ? "Denying…" : "Deny"}
                        </button>
                        <button type="button" className="b b--m b--primary" disabled={busy !== null} onClick={() => void run("APPROVE_ONCE")}>
                            {busy === "APPROVE_ONCE" ? "Approving…" : "Approve"}
                        </button>
                    </div>
                </>
            )}
        </>
    );
}

/** What a question's answer is, from its picks and the reader's words:
 *  words stand in for a single pick, and join several. */
function answerFor(question: AskQuestion, picked: readonly string[], words: string): string | string[] | null {
    const typed = words.trim();
    if (question.multiSelect) {
        const all = typed ? [...picked, typed] : [...picked];
        return all.length ? all : null;
    }
    return typed || picked[0] || null;
}

/** A Lemma's questions in a chat it stopped on. One question with one
 *  answer is answered by a click, or in words; several, or one that takes
 *  several answers, are picked and then sent together. */
function Asked({ pod, title, questions, send, onAnswered }: {
    pod: Pod;
    title: string;
    questions: AskQuestion[];
    send: (answers: Record<string, unknown>) => Promise<void>;
    onAnswered: (said: string) => void;
}) {
    const sender = useSend<Record<string, unknown>>(send, () => onAnswered(`Answered. ${pod.name} carries on.`));
    const [picked, setPicked] = useState<Record<string, string[]>>({});
    const [words, setWords] = useState<Record<string, string>>({});

    /* A question with no answers to pick cannot be answered from here. */
    if (questions.length === 0) return <p className="nnote">This one is answered in your chat with {pod.name}. Its title opens it.</p>;

    const one = questions.length === 1 && !questions[0].multiSelect ? questions[0] : null;
    if (one) {
        const asked = one.question || one.header;
        return (
            <>
                {asked !== title && <p className="nq">{asked}</p>}
                <Choose
                    pod={pod}
                    choices={one.options}
                    busy={sender.busy !== null}
                    onChoice={(label) => void sender.run({ [one.header]: label })}
                    onWords={(text) => void sender.run({ [one.header]: text })}
                />
                {sender.failed && <NotSent pod={pod} busy={sender.busy !== null} onRetry={sender.retry} />}
            </>
        );
    }

    const toggle = (question: AskQuestion, label: string) => setPicked((was) => {
        const had = was[question.header] ?? [];
        const next = question.multiSelect
            ? (had.includes(label) ? had.filter((one) => one !== label) : [...had, label])
            : (had[0] === label ? [] : [label]);
        return { ...was, [question.header]: next };
    });
    const answers = () => {
        const out: Record<string, unknown> = {};
        for (const question of questions) {
            const value = answerFor(question, picked[question.header] ?? [], words[question.header] ?? "");
            if (value !== null) out[question.header] = value;
        }
        return out;
    };
    const ready = questions.every((question) => answerFor(question, picked[question.header] ?? [], words[question.header] ?? "") !== null);
    return (
        <form className="nqs" onSubmit={(event) => { event.preventDefault(); if (ready) void sender.run(answers()); }}>
            {questions.map((question) => {
                const asked = question.question || question.header;
                const chosen = picked[question.header] ?? [];
                return (
                    <fieldset key={question.header} className="nqset" disabled={sender.busy !== null}>
                        <legend className="nq">{asked}</legend>
                        <div className="nchoices">
                            {question.options.map((option) => (
                                <button key={option.label} type="button" className="nchoice" aria-pressed={chosen.includes(option.label)} onClick={() => toggle(question, option.label)}>
                                    {option.label}
                                    {option.description && <small>{option.description}</small>}
                                </button>
                            ))}
                        </div>
                        <input
                            className="kinput ninput"
                            placeholder="Or in your own words"
                            aria-label={`Your own answer to: ${asked}`}
                            value={words[question.header] ?? ""}
                            onChange={(event) => setWords((was) => ({ ...was, [question.header]: event.target.value }))}
                        />
                    </fieldset>
                );
            })}
            {sender.failed && <NotSent pod={pod} busy={sender.busy !== null} onRetry={sender.retry} />}
            <div className="nacts">
                <button type="submit" className="b b--m b--primary" disabled={sender.busy !== null || !ready}>
                    {sender.busy !== null ? "Sending…" : "Send"}
                </button>
            </div>
        </form>
    );
}

/** One question's answers as buttons, each the whole answer, and a box to
 *  answer in words instead. What was typed stays if it doesn't send. */
function Choose({ pod, choices, busy, onChoice, onWords }: {
    pod: Pod;
    choices: readonly AskOption[];
    busy: boolean;
    onChoice: (label: string) => void;
    onWords: (text: string) => void;
}) {
    const [words, setWords] = useState("");
    const typed = words.trim();
    return (
        <>
            {choices.length > 0 && (
                <div className="nchoices">
                    {choices.map((choice, index) => (
                        <button key={`${index}:${choice.label}`} type="button" className="nchoice" disabled={busy} onClick={() => onChoice(choice.label)}>
                            {choice.label}
                            {choice.description && <small>{choice.description}</small>}
                        </button>
                    ))}
                </div>
            )}
            <form className="nsay" onSubmit={(event) => { event.preventDefault(); if (typed) onWords(typed); }}>
                <input
                    className="kinput ninput"
                    placeholder={choices.length > 0 ? `Or tell ${pod.name} something else` : `Answer ${pod.name}`}
                    aria-label={`Your answer to ${pod.name}`}
                    value={words}
                    disabled={busy}
                    onChange={(event) => setWords(event.target.value)}
                />
                <button type="submit" className="b b--m b--primary" disabled={busy || !typed}>Send</button>
            </form>
        </>
    );
}

/** A question a Lemma asked in a chat you share with it, answered here with
 *  one of the answers it offered, or in words, as it would be in the chat. */
function LineHere({ pod, line, title, onAnswered }: {
    pod: Pod;
    line: NonNullable<QueueItem["line"]>;
    title: string;
    onAnswered: (said: string) => void;
}) {
    const sender = useSend<{ choice: string } | { text: string }>(
        async (answer) => {
            if (live()) await answerLine(pod.id, line, answer);
        },
        () => onAnswered(`Answered. ${pod.name} has your answer.`),
    );
    const choices = useMemo(() => line.choices.map((label) => ({ label })), [line.choices]);
    return (
        <>
            {/* The title is the question's first line; answered here is the
                whole of it, as it was asked. */}
            {line.prompt && line.prompt !== title && <div className="nq"><Prose text={line.prompt} /></div>}
            <Choose
                pod={pod}
                choices={choices}
                busy={sender.busy !== null}
                onChoice={(choice) => void sender.run({ choice })}
                onWords={(text) => void sender.run({ text })}
            />
            {sender.failed && <NotSent pod={pod} busy={sender.busy !== null} onRetry={sender.retry} />}
        </>
    );
}

/** The form an automation stopped on, filled in here: its fields as its
 *  author wrote them (the SDK's schema form, as the run's own page draws
 *  it), checked here first so a wrong field is named rather than refused. */
function FormHere({ pod, runId, wait, onAnswered }: {
    pod: Pod;
    runId: string;
    wait: NonNullable<QueueItem["wait"]>;
    onAnswered: (said: string) => void;
}) {
    const fields = useMemo(() => buildSchemaFormFields(wait.schema, wait.uiSchema), [wait.schema, wait.uiSchema]);
    const initial = useMemo(() => buildSchemaFormValues(wait.schema, {}, wait.uiSchema), [wait.schema, wait.uiSchema]);
    const [values, setValues] = useState<Record<string, unknown> | null>(null);
    const [problems, setProblems] = useState<Record<string, string>>({});
    const current = values ?? initial;
    const sender = useSend<Record<string, unknown>>(
        async (inputs) => {
            if (live()) await lemma(pod.id).workflows.runs.submitForm(runId, { node_id: wait.nodeId, inputs }, pod.id);
        },
        () => onAnswered(`Sent. ${pod.name} carries on.`),
    );

    if (!wait.schema) return <p className="nnote">This step came without a form to fill in here. Its title opens where it stopped.</p>;

    const set = (name: string, value: unknown) => {
        setValues({ ...current, [name]: value });
        setProblems((was) => (was[name] ? { ...was, [name]: "" } : was));
    };
    const submit = () => {
        const built = buildSchemaFormPayload(wait.schema, current, wait.uiSchema);
        if (!built.isValid) {
            setProblems(built.errors);
            return;
        }
        void sender.run(built.data);
    };
    /* A refusal that trying again cannot change -- not yours, gone, moved
       on -- is said as what it is, without Try again. */
    const status = (sender.failed?.problem as { statusCode?: number } | null | undefined)?.statusCode;
    const refused = status === 403 || status === 404 || status === 409 || status === 422 ? sayRefusal(status, "") : null;
    const busy = sender.busy !== null;
    return (
        <form className="nform" onSubmit={(event) => { event.preventDefault(); submit(); }}>
            {fields.length > 0 && (
                <div className="nform__fields">
                    {fields.map((field) => (
                        <FormField key={field.name} field={field} value={current[field.name]} problem={problems[field.name]} disabled={busy} onChange={(value) => set(field.name, value)} />
                    ))}
                </div>
            )}
            {sender.failed && (refused ? <Problem>{refused}</Problem> : <NotSent pod={pod} busy={busy} onRetry={sender.retry} />)}
            <div className="nacts">
                <button type="submit" className="b b--m b--primary" disabled={busy || Boolean(refused)}>{busy ? "Sending…" : "Send"}</button>
            </div>
        </form>
    );
}

/** One field, by what the schema says it is. A field nothing was designed
 *  for comes back as `json`, and is a box of JSON rather than a hole. */
function FormField({ field, value, problem, disabled, onChange }: {
    field: SchemaFormField;
    value: unknown;
    problem?: string;
    disabled: boolean;
    onChange: (value: unknown) => void;
}) {
    const required = field.required && <span aria-hidden="true"> *</span>;
    const after = (
        <>
            {field.description && <small>{field.description}</small>}
            {problem && <em className="nfield__bad" role="alert">{problem}</em>}
        </>
    );
    if (field.kind === "boolean") {
        return (
            <div className="kfield nfield nfield--wide">
                <label className="nfield__check">
                    <input type="checkbox" checked={value === true} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
                    <span>{field.label}{required}</span>
                </label>
                {after}
            </div>
        );
    }
    const wide = field.kind === "textarea" || field.kind === "json";
    const text = String(value ?? "");
    return (
        <label className={"kfield nfield" + (wide ? " nfield--wide" : "")}>
            <span>{field.label}{required}</span>
            {field.kind === "select" ? (
                <select className="kinput ninput" value={text} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
                    <option value="">Choose…</option>
                    {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
            ) : wide ? (
                <textarea className="kinput ninput ninput--area" rows={field.kind === "json" ? 4 : 3} value={text} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
            ) : (
                <input
                    className="kinput ninput"
                    type={field.kind === "number" ? "number" : field.kind === "date" ? "date" : field.kind === "datetime" ? "datetime-local" : field.kind === "email" ? "email" : "text"}
                    value={text}
                    disabled={disabled}
                    onChange={(event) => onChange(event.target.value)}
                />
            )}
            {after}
        </label>
    );
}
