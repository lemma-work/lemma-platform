import { useCallback, useMemo, useState } from "react";
import { CheckIcon, DenyIcon, QuestionIcon, ShieldIcon } from "@/ui/icons";
import { decisionLabel, interactionHeading, type ApprovalDecision, type AskQuestion } from "./approval";
import type { Interaction } from "./turns";

/** The card a run stops at.
 *
 *  **Three states, kept apart, because they are three different facts.**
 *
 *  `deciding` is the POST, and it is short — a button holds a spinner against
 *  it. `submitted` is everything after: the decision is recorded and durable,
 *  and what remains is the server running the thing that was approved, which is
 *  allowed to take minutes. `resolved` is the tool return actually landing in
 *  the transcript.
 *
 *  Collapsing the middle state into the first is what makes a card like this
 *  feel broken: you click Approve, the card vanishes, and nothing replaces it
 *  for ninety seconds while a tool runs. Collapsing it into the last is worse
 *  — the buttons stay live and a second click posts a decision against an id
 *  that is already answered.
 *
 *  A resolved card never disappears either. "Approved once" sitting in the
 *  transcript at the point it was approved is the record of what you agreed
 *  to, and scrolling back to find it is a reasonable thing to want to do.
 *
 *  **Open and answered are drawn for two different readers.** While the run is
 *  blocked the card is a control, and it is shown docked above the composer by
 *  `InteractionDock` — every option in front of you, at the width of the box
 *  you would otherwise be typing into. Once it is answered it is a record, and
 *  a record does not need the options that were not taken: the questions
 *  collapse to what was asked and what you said. Redrawing the whole form
 *  greyed out instead means, for a two-question ask, nine boxes of dead
 *  controls standing in for one sentence of history. */

export type Resolve = (id: string, decision: ApprovalDecision, response?: Record<string, unknown>) => Promise<void>;

const OTHER = "__other__";

function useDecision(id: string, onResolve?: Resolve) {
    const [deciding, setDeciding] = useState<ApprovalDecision | null>(null);
    const [submitted, setSubmitted] = useState<ApprovalDecision | null>(null);
    const [error, setError] = useState<string | null>(null);

    const decide = useCallback(
        async (decision: ApprovalDecision, response?: Record<string, unknown>) => {
            if (!onResolve || deciding || submitted) return;
            setDeciding(decision);
            setError(null);
            try {
                await onResolve(id, decision, response);
                setSubmitted(decision);
            } catch (problem) {
                setError(problem instanceof Error ? problem.message : "That decision did not go through.");
            } finally {
                setDeciding(null);
            }
        },
        [id, onResolve, deciding, submitted],
    );

    return { deciding, submitted, error, decide };
}

/** What the server is doing on our behalf once a decision is recorded. */
function afterNote(decision: ApprovalDecision): string {
    return decision === "DENY" ? "Decision sent. Waiting for the teammate…" : "Approval sent. Waiting for the teammate…";
}

/** One question, folded back to the answer it got.
 *
 *  Used both for a question you have already moved past in a live card and for
 *  every question on an answered one. The description of the chosen option
 *  comes with it: "Debt service kills empires" is a title, and "fiscal math as
 *  the real mechanism" is what it actually meant — dropping the second on the
 *  way into the record loses the half that was doing the explaining. */
function Answered({
    question,
    labels,
    onChange,
}: {
    question: AskQuestion;
    labels: string[];
    onChange?: () => void;
}) {
    const said = labels.filter((label) => label !== OTHER);
    const only = said.length === 1 ? question.options.find((option) => option.label === said[0]) : undefined;

    return (
        <div className="ask__was">
            <p className="ask__was-q">{question.question || question.header}</p>
            <p className="ask__was-a">
                {said.length > 0 ? said.join(", ") : <i>Not answered</i>}
                {only?.description && <span> — {only.description}</span>}
            </p>
            {onChange && (
                <button type="button" className="ask__change" onClick={onChange}>
                    Change
                </button>
            )}
        </div>
    );
}

/** The questions inside an `ask_user`, asked one at a time.
 *
 *  An agent that asks two things with four options each puts fifteen controls
 *  on screen at once, and the reader has to hold the second question in their
 *  head while answering the first. So a live card shows exactly one question:
 *  the ones behind it fold into their answers, the ones ahead are not drawn at
 *  all, and the count says how many are coming so nobody is surprised by a
 *  second screen.
 *
 *  Picking an option on a single-choice question advances on its own — that is
 *  the whole point of the staging, and going back is one click on the folded
 *  answer. The last question never auto-submits: the button at the end is the
 *  one that restarts an agent, and it stays a deliberate act. */
function Questions({
    interaction,
    questions,
    onResolve,
}: {
    interaction: Interaction;
    questions: AskQuestion[];
    onResolve?: Resolve;
}) {
    const { deciding, submitted, error, decide } = useDecision(interaction.id, onResolve);

    /* A question that was already answered shows what it was answered with.
       The labels come back in the tool return, so the recorded answer seeds
       the selection rather than the card rendering a blank form nobody can
       fill in. */
    const recorded = useMemo(() => {
        const seeded: Record<string, string[]> = {};
        for (const [header, value] of Object.entries(interaction.answers)) {
            seeded[header] = (Array.isArray(value) ? value : [value]).map(String);
        }
        return seeded;
    }, [interaction.answers]);

    const [chosenNow, setPicked] = useState<Record<string, string[]>>({});
    const [other, setOther] = useState<Record<string, string>>({});
    const [step, setStep] = useState(0);
    const picked = interaction.open ? chosenNow : recorded;
    const last = questions.length - 1;

    const answerFor = useCallback(
        (question: AskQuestion): string | string[] | null => {
            const chosen = picked[question.header] ?? [];
            const free = (other[question.header] ?? "").trim();
            const values = chosen.filter((label) => label !== OTHER);
            if (chosen.includes(OTHER)) {
                if (!free) return null;
                values.push(free);
            }
            if (values.length === 0) return null;
            return question.multiSelect ? values : values[0];
        },
        [picked, other],
    );

    /* What a question has been answered with, as the reader wrote it. Not the
       raw selection: a free-text answer is held as the `OTHER` sentinel plus a
       sentence typed somewhere else, and folding that row without resolving it
       first showed "Not answered" over an answer somebody had just typed. */
    const said = useCallback(
        (question: AskQuestion): string[] => {
            const value = answerFor(question);
            if (value === null) return [];
            return Array.isArray(value) ? value : [value];
        },
        [answerFor],
    );

    const closed = !interaction.open || !!submitted;
    const here = questions[Math.min(step, last)];
    const ready = answerFor(here) !== null;
    const answered = questions.every((question) => answerFor(question) !== null);

    const toggle = (question: AskQuestion, label: string, index: number) => {
        const chosen = picked[question.header] ?? [];
        const next = question.multiSelect
            ? chosen.includes(label)
                ? chosen.filter((entry) => entry !== label)
                : [...chosen, label]
            : chosen[0] === label
              ? []
              : [label];
        setPicked((was) => ({ ...was, [question.header]: next }));
        /* Choosing is the answer on a single-choice question, so it moves on.
           "Something else" does not: the answer is the sentence you have not
           typed yet. */
        if (!question.multiSelect && label !== OTHER && next.length === 1 && index < last) setStep(index + 1);
    };

    const submit = () => {
        const answers: Record<string, unknown> = {};
        for (const question of questions) {
            const value = answerFor(question);
            if (value !== null) answers[question.header] = value;
        }
        void decide("APPROVE_ONCE", { answers });
    };

    /* Answered, and no longer a form. Every question folds to what it got. */
    if (closed) {
        return (
            <>
                <div className="ask__list">
                    {questions.map((question) => (
                        <Answered key={question.header} question={question} labels={said(question)} />
                    ))}
                </div>
                {submitted && interaction.open && <p className="approval__after">{afterNote(submitted)}</p>}
                {error && <p className="approval__error">{error}</p>}
            </>
        );
    }

    const chosen = picked[here.header] ?? [];

    return (
        <>
            {step > 0 && (
                <div className="ask__list">
                    {questions.slice(0, step).map((question, index) => (
                        <Answered
                            key={question.header}
                            question={question}
                            labels={said(question)}
                            onChange={() => setStep(index)}
                        />
                    ))}
                </div>
            )}

            <div className="ask__q">
                {questions.length > 1 && (
                    <p className="ask__step">
                        Question {step + 1} of {questions.length}
                    </p>
                )}
                <p className="ask__ask">{here.question || here.header}</p>
                <div className="ask__opts">
                    {[...here.options, { label: OTHER, description: undefined }].map((option) => {
                        const isOther = option.label === OTHER;
                        const on = chosen.includes(option.label);
                        return (
                            <button
                                key={option.label}
                                type="button"
                                className={"ask__opt" + (isOther ? " ask__opt--other" : "")}
                                aria-pressed={on}
                                onClick={() => toggle(here, option.label, step)}
                            >
                                <b>{isOther ? "Something else" : option.label}</b>
                                {option.description && <span>{option.description}</span>}
                            </button>
                        );
                    })}
                </div>
                {chosen.includes(OTHER) && (
                    <input
                        className="ask__other"
                        autoFocus
                        placeholder="In your own words…"
                        value={other[here.header] ?? ""}
                        onChange={(event) => setOther((was) => ({ ...was, [here.header]: event.target.value }))}
                    />
                )}
            </div>

            <div className="approval__acts">
                {step < last ? (
                    <button className="btn btn--primary" disabled={!ready} onClick={() => setStep(step + 1)}>
                        Next
                    </button>
                ) : (
                    <button className="btn btn--primary" disabled={!answered || !!deciding} onClick={submit}>
                        {deciding === "APPROVE_ONCE" ? "Sending…" : "Answer"}
                    </button>
                )}
                <button
                    className="btn approval__deny"
                    disabled={!!deciding}
                    onClick={() => void decide("DENY", { answers: {} })}
                >
                    {deciding === "DENY" ? "Skipping…" : "Skip"}
                </button>
            </div>

            {error && <p className="approval__error">{error}</p>}
        </>
    );
}

export function InteractionCard({
    interaction,
    teammate,
    onResolve,
    docked,
}: {
    interaction: Interaction;
    /** Who is asking. Only reached when the call named itself nothing, which
     *  `ask_user` regularly does — and a question with no title of its own is
     *  still somebody's question. */
    teammate: string;
    onResolve?: Resolve;
    /** Drawn on the shelf above the composer rather than in the transcript.
     *  Only a styling hook — the card is the same card, which is the reason
     *  the docked one and the record it becomes cannot drift apart. */
    docked?: boolean;
}) {
    const { deciding, submitted, error, decide } = useDecision(interaction.id, onResolve);
    const question = interaction.kind === "question";
    const questions = useMemo(() => interaction.questions, [interaction.questions]);

    /* Resolved is whichever came first: the tool return landing, or our own
       click. Both are true answers — the second just knows sooner. */
    const settled = !interaction.open ? interaction.decision || "ANSWERED" : submitted ?? "";
    const denied = settled === "DENY";

    return (
        <div
            className="approval"
            data-kind={question ? "question" : "approval"}
            data-state={settled ? (denied ? "denied" : "done") : "open"}
            data-docked={docked ? "" : undefined}
        >
            <div className="approval__top">
                <span className="approval__icon">
                    {settled ? (
                        denied ? <DenyIcon size={16} weight="bold" /> : <CheckIcon size={16} weight="bold" />
                    ) : question ? (
                        <QuestionIcon size={16} weight="bold" />
                    ) : (
                        <ShieldIcon size={16} weight="bold" />
                    )}
                </span>
                <span className="approval__title">
                    {interactionHeading(interaction.kind, interaction.details.title, teammate, Boolean(settled))}
                </span>
                {/* One statement of state per card. An untitled pause is
                    headed "Marketing needs your answer", and a chip beside it
                    reading "needs an answer" is the same sentence twice. Once
                    it is settled the chip is carrying the decision, which the
                    heading is not, so it comes back. */}
                {(settled || interaction.details.title) && (
                    <span className={"pill " + (settled ? "pill--done" : "pill--wait")}>
                        {settled
                            ? decisionLabel(settled, question ? "question" : "approval")
                            : question
                              ? "needs an answer"
                              : "needs approval"}
                    </span>
                )}
            </div>

            {interaction.details.request && <p className="approval__detail">{interaction.details.request}</p>}

            {/* The arguments of the call that will actually run — not the
                fields of the approval envelope. This is the only place a
                reader can see what they are agreeing to, so it is shown
                rather than summarised away. */}
            {!question && interaction.details.params.length > 0 && (
                <dl className="approval__args">
                    {interaction.details.params.map((param) => (
                        <div key={param.name}>
                            <dt>{param.name}</dt>
                            <dd>{param.value}</dd>
                        </div>
                    ))}
                </dl>
            )}

            {question && questions.length > 0 ? (
                <Questions interaction={interaction} questions={questions} onResolve={onResolve} />
            ) : (
                <>
                    {interaction.open && !submitted && (
                        <div className="approval__acts">
                            <button
                                className="btn btn--primary"
                                disabled={!!deciding}
                                onClick={() => void decide("APPROVE_ONCE")}
                            >
                                {deciding === "APPROVE_ONCE" ? "Approving…" : "Approve once"}
                            </button>
                            {interaction.details.canApproveForSession && (
                                <button
                                    className="btn"
                                    disabled={!!deciding}
                                    onClick={() => void decide("APPROVE_FOR_SESSION")}
                                >
                                    {deciding === "APPROVE_FOR_SESSION" ? "Approving…" : "Approve for this conversation"}
                                </button>
                            )}
                            <button
                                className="btn approval__deny"
                                disabled={!!deciding}
                                onClick={() => void decide("DENY")}
                            >
                                {deciding === "DENY" ? "Denying…" : "Deny"}
                            </button>
                        </div>
                    )}

                    {/* The decision landed; the work it unblocked has not. */}
                    {submitted && interaction.open && <p className="approval__after">{afterNote(submitted)}</p>}
                    {error && <p className="approval__error">{error}</p>}
                </>
            )}
        </div>
    );
}
