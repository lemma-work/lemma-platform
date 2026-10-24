import { useCallback, useEffect, useMemo, useState, type MutableRefObject } from "react";
import { ActivityIndicator, TextInput, View } from "react-native";
import { haptics } from "../ui/haptics";
import { Icon } from "../ui/icons";
import { Pill, Press } from "../ui/press";
import { Counted, Text } from "../ui/text";
import { color, font, TOUCH } from "../ui/tokens";
import { HOST_PERMISSION_WINDOW_MS, decisionLabel, hostPermissionExpired, interactionHeading, type ApprovalDecision, type AskQuestion } from "@web/thread/approval";
import { consequenceLine, consequenceOf } from "./consequence";
import { Glyph } from "./glyphs";
import { HoldButton } from "./hold-button";
import { useLook } from "./look";
import type { Interaction } from "@web/thread/turns";
import type { AnswerWith, Resolve } from "./use-chat";

/** The card a run stops at: an approval or a question. The web's
 *  `interaction-card.tsx`, for a phone.
 *
 *  Three states kept apart, as there: deciding (the POST, short, a button
 *  holds a spinner), submitted (recorded; the approved thing may take
 *  minutes to run) and resolved (the tool return has landed). A card never
 *  disappears: answered, it stays in the chat where it was asked, folded to
 *  what was asked and what you said.
 *
 *  Open, it is docked above the composer (`InteractionDock` in
 *  `dock.tsx`), at the composer's width, since a question is
 *  answered with the hands.
 *
 *  An approval that does something a person cannot take back -- money,
 *  words going outside the Lemma, deleting (`consequence.ts`) -- is held
 *  for a second; any other is one tap. "Not this one" is always one tap.
 *  Buttons are in the Lemma's colour; violet stays the sign that it waits. */

/** A button in the chat's colour: filled for the yes, outlined for the rest. */
function ChatButton({ title, onPress, kind = "primary", busy = false, disabled = false, height = 48, grow = false, size = 15.5 }: {
    title: string;
    onPress: () => void;
    kind?: "primary" | "outline";
    busy?: boolean;
    disabled?: boolean;
    height?: number;
    grow?: boolean;
    size?: number;
}) {
    const look = useLook();
    const primary = kind === "primary";
    return (
        <Press
            onPress={onPress}
            disabled={disabled || busy}
            label={title}
            accessibilityState={{ disabled: disabled || busy, busy }}
            style={{
                minHeight: Math.max(height, TOUCH),
                paddingHorizontal: 16,
                borderRadius: 14,
                backgroundColor: primary ? look.ink : "transparent",
                borderWidth: primary ? 0 : 1,
                borderColor: color.line2,
                alignItems: "center",
                justifyContent: "center",
                flexGrow: grow ? 1 : 0,
                flexBasis: grow ? 0 : "auto",
                opacity: disabled ? 0.4 : 1,
            }}
        >
            {busy ? <ActivityIndicator size="small" color={primary ? look.onInk : color.ink} /> : <Text size={size} weight={primary ? "medium" : "regular"} tone={primary ? look.onInk : color.ink} numberOfLines={1}>{title}</Text>}
        </Press>
    );
}

const OTHER = "__other__";

/** The phone says "chat" where the web says "conversation". */
function said(decision: string, kind: "approval" | "question"): string {
    return decisionLabel(decision, kind).replace("this conversation", "this chat");
}

function useDecision(id: string, onResolve?: Resolve) {
    const [deciding, setDeciding] = useState<ApprovalDecision | null>(null);
    const [submitted, setSubmitted] = useState<ApprovalDecision | null>(null);
    const [error, setError] = useState<string | null>(null);
    const decide = useCallback(async (decision: ApprovalDecision, response?: Record<string, unknown>) => {
        if (!onResolve || deciding || submitted) return false;
        setDeciding(decision);
        setError(null);
        try {
            await onResolve(id, decision, response);
            setSubmitted(decision);
            return true;
        } catch (problem) {
            setError(problem instanceof Error ? problem.message : "That did not go through.");
            return false;
        } finally {
            setDeciding(null);
        }
    }, [id, onResolve, deciding, submitted]);
    return { deciding, submitted, error, decide };
}

/** Re-read once, when a coding agent's request runs out, so the card stops
 *  offering an answer when the agent stops waiting for one. */
function useNowUntil(deadline: number | undefined): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (deadline === undefined) return;
        const left = deadline - Date.now();
        if (left <= 0) return;
        const timer = setTimeout(() => setNow(Date.now()), Math.min(left + 250, 2 ** 31 - 1));
        return () => clearTimeout(timer);
    }, [deadline]);
    return now;
}

function clockAt(ms: number): string {
    return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function afterNote(decision: ApprovalDecision): string {
    return decision === "DENY" ? "Decision sent." : "Approval sent.";
}

/** One question, folded back to its answer, with what the chosen option
 *  meant when it said. */
function Answered({ question, labels, onChange }: { question: AskQuestion; labels: string[]; onChange?: () => void }) {
    const shown = labels.filter((label) => label !== OTHER);
    const only = shown.length === 1 ? question.options.find((option) => option.label === shown[0]) : undefined;
    return (
        <View style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
            <View style={{ flex: 1, gap: 1 }}>
                <Text size={12.5} tone={color.ink3}>{question.question || question.header}</Text>
                <Text size={14} tone={shown.length ? color.ink : color.ink3}>
                    {shown.length > 0 ? shown.join(", ") : "Not answered"}
                    {only?.description ? <Text size={14} tone={color.ink2}> — {only.description}</Text> : null}
                </Text>
            </View>
            {onChange ? (
                <Press label={`Change: ${question.question || question.header}`} onPress={onChange} hitSlop={10} style={{ minHeight: 30, justifyContent: "center" }}>
                    <Text size={13} tone={color.violetInk}>Change</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** A choice, large enough to answer with a thumb without looking twice. */
function Option({ label, description, on, multi, onPress }: { label: string; description?: string; on: boolean; multi: boolean; onPress: () => void }) {
    const look = useLook();
    return (
        <Press
            role="radio"
            label={label + (description ? ". " + description : "")}
            accessibilityState={{ checked: on }}
            onPress={() => {
                haptics.tick();
                onPress();
            }}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: 54, paddingVertical: 10, paddingHorizontal: 14, borderWidth: on ? 1.5 : 1, borderColor: on ? look.ink : color.line2, borderRadius: 14, backgroundColor: on ? look.soft : color.paper }}
        >
            <View style={{ width: 20, height: 20, borderRadius: multi ? 6 : 10, borderWidth: on ? 0 : 1.5, borderColor: color.ink3, backgroundColor: on ? look.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
                {on ? <Icon name="check" size={13} width={2.6} tone={look.onInk} /> : null}
            </View>
            <View style={{ flex: 1, gap: 2 }}>
                <Text size={16}>{label}</Text>
                {description ? <Text size={13} tone={color.ink2}>{description}</Text> : null}
            </View>
        </Press>
    );
}

/** The questions inside an `ask_user`, one at a time: those behind fold to
 *  their answers, those ahead are not drawn, and the count says how many
 *  are coming. A single choice moves on by itself; the last question never
 *  sends by itself. */
function Questions({ interaction, questions, onResolve, folded, answerWith }: {
    interaction: Interaction;
    questions: AskQuestion[];
    onResolve?: Resolve;
    folded?: boolean;
    answerWith?: MutableRefObject<AnswerWith | null>;
}) {
    const { deciding, submitted, error, decide } = useDecision(interaction.id, onResolve);
    const recorded = useMemo(() => {
        const seeded: Record<string, string[]> = {};
        for (const [header, value] of Object.entries(interaction.answers)) seeded[header] = (Array.isArray(value) ? value : [value]).map(String);
        return seeded;
    }, [interaction.answers]);
    const [chosenNow, setPicked] = useState<Record<string, string[]>>({});
    const [other, setOther] = useState<Record<string, string>>({});
    const [step, setStep] = useState(0);
    const picked = interaction.open ? chosenNow : recorded;
    const last = questions.length - 1;

    const answerFor = useCallback((question: AskQuestion): string | string[] | null => {
        const chosen = picked[question.header] ?? [];
        const free = (other[question.header] ?? "").trim();
        const values = chosen.filter((label) => label !== OTHER);
        if (chosen.includes(OTHER)) {
            if (!free) return null;
            values.push(free);
        }
        if (values.length === 0) return null;
        return question.multiSelect ? values : values[0];
    }, [picked, other]);
    const saidFor = useCallback((question: AskQuestion): string[] => {
        const value = answerFor(question);
        return value === null ? [] : Array.isArray(value) ? value : [value];
    }, [answerFor]);

    const closed = !interaction.open || Boolean(submitted);
    const here = questions[Math.min(step, last)];
    const ready = answerFor(here) !== null;
    const answered = questions.every((question) => answerFor(question) !== null);

    const toggle = (question: AskQuestion, label: string, index: number) => {
        const chosen = picked[question.header] ?? [];
        const next = question.multiSelect
            ? chosen.includes(label) ? chosen.filter((entry) => entry !== label) : [...chosen, label]
            : chosen[0] === label ? [] : [label];
        setPicked((was) => ({ ...was, [question.header]: next }));
        if (!question.multiSelect && label !== OTHER && next.length === 1 && index < last) setStep(index + 1);
    };
    const answers = () => {
        const out: Record<string, unknown> = {};
        for (const question of questions) {
            const value = answerFor(question);
            if (value !== null) out[question.header] = value;
        }
        return out;
    };

    /* Typing into the composer while a question is open answers it, rather
       than sending a message past it -- which the server would read as the
       question being skipped. */
    useEffect(() => {
        if (!answerWith) return;
        if (closed) {
            answerWith.current = null;
            return;
        }
        answerWith.current = async (text: string) => {
            const out = answers();
            const kept = (picked[here.header] ?? []).filter((label) => label !== OTHER);
            out[here.header] = here.multiSelect ? [...kept, text] : text;
            if (!(await decide("APPROVE_ONCE", { answers: out }))) throw new Error("That answer did not go through.");
        };
        return () => { answerWith.current = null; };
    });

    if (closed) {
        return (
            <View style={{ gap: 10 }}>
                {questions.map((question) => <Answered key={question.header} question={question} labels={saidFor(question)} />)}
                {submitted && interaction.open ? <Text size={13} tone={color.ink2}>{afterNote(submitted)}</Text> : null}
                {error ? <Text size={13} tone={color.amber}>{error}</Text> : null}
            </View>
        );
    }

    if (folded) {
        return (
            <Text size={13.5} tone={color.ink2} numberOfLines={1}>
                {questions.length > 1 ? `${step + 1} of ${questions.length} · ` : ""}{here.question || here.header}
            </Text>
        );
    }

    const chosen = picked[here.header] ?? [];
    return (
        <View style={{ gap: 10 }}>
            {step > 0 ? (
                <View style={{ gap: 8, paddingBottom: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
                    {questions.slice(0, step).map((question, index) => <Answered key={question.header} question={question} labels={saidFor(question)} onChange={() => setStep(index)} />)}
                </View>
            ) : null}
            {questions.length > 1 ? <Text size={12.5} tone={color.ink3}>Question {step + 1} of {questions.length}</Text> : null}
            {(here.question && here.question !== interaction.details.title) || questions.length > 1 ? <Counted size={16.5} style={{ lineHeight: 23 }}>{here.question || here.header}</Counted> : null}
            {here.multiSelect ? <Text size={12.5} tone={color.ink3}>Pick any that fit.</Text> : null}
            <View style={{ gap: 8 }}>
                {here.options.map((option) => (
                    <Option key={option.label} label={option.label} description={option.description} on={chosen.includes(option.label)} multi={here.multiSelect} onPress={() => toggle(here, option.label, step)} />
                ))}
                <Option label="Something else" on={chosen.includes(OTHER)} multi={here.multiSelect} onPress={() => toggle(here, OTHER, step)} />
            </View>
            {chosen.includes(OTHER) ? (
                <TextInput
                    accessibilityLabel="In your own words"
                    autoFocus
                    placeholder="In your own words…"
                    placeholderTextColor={color.ink3}
                    value={other[here.header] ?? ""}
                    onChangeText={(value) => setOther((was) => ({ ...was, [here.header]: value }))}
                    style={{ fontFamily: font.regular, fontSize: 15, color: color.ink, borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}
                />
            ) : null}
            <View style={{ flexDirection: "row", gap: 8 }}>
                {step < last ? (
                    <ChatButton title="Next" grow disabled={!ready} onPress={() => setStep(step + 1)} />
                ) : (
                    <ChatButton title={deciding === "APPROVE_ONCE" ? "Sending…" : "Answer"} grow busy={deciding === "APPROVE_ONCE"} disabled={!answered || Boolean(deciding)} onPress={() => void decide("APPROVE_ONCE", { answers: answers() }).then((went) => { if (went) haptics.done(); })} />
                )}
                <ChatButton title="Skip" kind="outline" busy={deciding === "DENY"} disabled={Boolean(deciding)} onPress={() => void decide("DENY", { answers: {} })} />
            </View>
            {error ? <Text size={13} tone={color.amber}>{error}</Text> : null}
        </View>
    );
}

/** Yes, yes for the rest of this chat, and "Not this one". Held for a
 *  second when it cannot be taken back, tapped otherwise. */
function Choices({ interaction, deciding, decide }: {
    interaction: Interaction;
    deciding: ApprovalDecision | null;
    decide: (decision: ApprovalDecision) => Promise<boolean>;
}) {
    const look = useLook();
    const consequence = consequenceOf(interaction.details);
    const forChat = interaction.details.canApproveForSession ? interaction.details.sessionLabel || "Approve for this chat" : null;
    const said = (decision: ApprovalDecision) => void decide(decision).then((went) => { if (went && decision !== "DENY" && !consequence) haptics.done(); });
    const no = <ChatButton title="Not this one" kind="outline" grow={!forChat || Boolean(consequence)} height={46} size={15} busy={deciding === "DENY"} disabled={Boolean(deciding)} onPress={() => void decide("DENY")} />;
    if (consequence) {
        const hint = `${consequenceLine(consequence)}. Hold for a second to approve.`;
        return (
            <View style={{ gap: 8 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                    <Glyph name={consequence === "delete" ? "trash" : consequence === "outside" ? "external" : "lock"} size={13} tone={color.ink2} />
                    <Text size={13} tone={color.ink2}>{consequenceLine(consequence)} · hold to approve</Text>
                </View>
                <HoldButton title="Hold to approve" hint={hint} tone={look.ink} onTone={look.onInk} ground={look.soft} busy={deciding === "APPROVE_ONCE"} disabled={Boolean(deciding)} onConfirm={() => void decide("APPROVE_ONCE")} />
                <View style={{ flexDirection: "row", gap: 8 }}>
                    {forChat ? <HoldButton title={forChat} hint={`${consequenceLine(consequence)}, and the same again in this chat without asking. Hold for a second.`} tone={look.ink} onTone={look.onInk} ground={color.wash} height={46} size={14.5} grow busy={deciding === "APPROVE_FOR_SESSION"} disabled={Boolean(deciding)} onConfirm={() => void decide("APPROVE_FOR_SESSION")} /> : null}
                    {no}
                </View>
            </View>
        );
    }
    return (
        <View style={{ gap: 8 }}>
            <ChatButton title={deciding === "APPROVE_ONCE" ? "Approving…" : "Approve"} height={52} size={16} busy={deciding === "APPROVE_ONCE"} disabled={Boolean(deciding)} onPress={() => said("APPROVE_ONCE")} />
            <View style={{ flexDirection: "row", gap: 8 }}>
                {forChat ? <ChatButton title={forChat} kind="outline" grow height={46} size={14.5} busy={deciding === "APPROVE_FOR_SESSION"} disabled={Boolean(deciding)} onPress={() => said("APPROVE_FOR_SESSION")} /> : null}
                {no}
            </View>
        </View>
    );
}

export function InteractionCard({ interaction, name, onResolve, docked, runEnded, resumesOnAnswer, folded, onFold, answerWith }: {
    interaction: Interaction;
    /** Who is asking: the Lemma's name, for a pause that named itself nothing. */
    name: string;
    onResolve?: Resolve;
    docked?: boolean;
    runEnded?: boolean;
    resumesOnAnswer?: boolean;
    folded?: boolean;
    onFold?: (folded: boolean) => void;
    answerWith?: MutableRefObject<AnswerWith | null>;
}) {
    const { deciding, submitted, error, decide } = useDecision(interaction.id, onResolve);
    const [recordOpen, setRecordOpen] = useState(false);
    const question = interaction.kind === "question";
    const questions = interaction.questions;
    const deadline = interaction.details.hostPermission && interaction.askedAtMs !== undefined ? interaction.askedAtMs + HOST_PERMISSION_WINDOW_MS : undefined;
    const now = useNowUntil(interaction.open ? deadline : undefined);
    const expired = interaction.open && !submitted && hostPermissionExpired(interaction.details, { askedAtMs: interaction.askedAtMs, nowMs: now, runEnded: Boolean(runEnded), resumesOnAnswer: Boolean(resumesOnAnswer) });
    const settled = !interaction.open ? interaction.decision || "ANSWERED" : submitted ?? "";
    const denied = settled === "DENY";
    const title = interactionHeading(interaction.kind, interaction.details.title, name, Boolean(settled));
    const pill = expired ? { text: "Expired", tone: "grey" as const }
        : settled ? { text: said(settled, question ? "question" : "approval"), tone: "grey" as const }
        : interaction.details.title ? { text: question ? "Your answer" : "Your yes", tone: "violet" as const }
        : null;
    const glyph = settled
        ? denied ? <Glyph name="deny" size={17} tone={color.ink2} /> : <Icon name="check" size={16} width={2.2} tone={color.ink2} />
        : question ? <Glyph name="question" size={17} tone={color.violetInk} /> : <Glyph name="shield" size={17} tone={color.violetInk} />;

    const header = (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            {glyph}
            <Counted size={15.5} weight="medium" style={{ flex: 1, lineHeight: 21 }}>{title}</Counted>
            {pill ? <Pill text={pill.text} tone={pill.tone} /> : null}
            {onFold ? (
                <Press label={folded ? "Show the question" : "Hide, to read the chat"} accessibilityState={{ expanded: !folded }} onPress={() => onFold(!folded)} hitSlop={10} style={{ flexDirection: "row", alignItems: "center", gap: 3, minHeight: 30, paddingLeft: 4 }}>
                    <Text size={13} tone={color.ink2}>{folded ? "Show" : "Hide"}</Text>
                    <View style={{ transform: [{ rotate: folded ? "180deg" : "0deg" }] }}><Icon name="down" size={13} width={2} tone={color.ink2} /></View>
                </Press>
            ) : null}
        </View>
    );
    const details = (
        <>
            {interaction.details.request && interaction.details.request !== title ? <Counted size={13.5} tone={color.ink2} style={{ lineHeight: 19 }}>{interaction.details.request}</Counted> : null}
            {!question && interaction.details.params.length > 0 ? (
                <View style={{ gap: 3 }}>
                    {interaction.details.params.map((param) => (
                        <View key={param.name} style={{ flexDirection: "row", gap: 8 }}>
                            <Text size={13} tone={color.ink3} style={{ width: 92 }} numberOfLines={1}>{param.name}</Text>
                            <Counted size={13} style={{ flex: 1 }}>{param.value}</Counted>
                        </View>
                    ))}
                </View>
            ) : null}
        </>
    );
    const frame = {
        alignSelf: "stretch" as const,
        gap: 10,
        paddingVertical: 12,
        paddingHorizontal: 14,
        ...(docked ? {} : { borderWidth: 1, borderColor: color.line2, borderRadius: 16 }),
    };

    /* Approved: a record, folded to its line; what was agreed to opens on a tap. */
    if (!question && (settled === "APPROVE_ONCE" || settled === "APPROVE_FOR_SESSION")) {
        return (
            <View accessibilityLabel={title + ". " + said(settled, "approval")} style={frame}>
                <Press label={recordOpen ? "Hide what was approved" : "Show what was approved"} accessibilityState={{ expanded: recordOpen }} onPress={() => setRecordOpen((was) => !was)}>{header}</Press>
                {recordOpen ? details : null}
                {submitted && interaction.open ? <Text size={13} tone={color.ink2}>{afterNote(submitted)}</Text> : null}
            </View>
        );
    }

    return (
        <View accessibilityLabel={question ? "A question for you" : "Needs your yes"} style={frame}>
            {header}
            {!folded ? details : null}
            {question && questions.length > 0 ? (
                <Questions interaction={interaction} questions={questions} onResolve={onResolve} folded={folded} answerWith={answerWith} />
            ) : folded ? null : (
                <>
                    {expired ? <Text size={13} tone={color.ink2}>Expired — the agent went on without it.</Text> : null}
                    {interaction.open && !submitted && !expired && deadline !== undefined ? <Text size={13} tone={color.ink2}>Answer by {clockAt(deadline)}. After that the agent goes on without it.</Text> : null}
                    {interaction.open && !submitted && !expired ? (
                        <Choices interaction={interaction} deciding={deciding} decide={decide} />
                    ) : null}
                    {submitted && interaction.open ? <Text size={13} tone={color.ink2}>{afterNote(submitted)}</Text> : null}
                    {error ? <Text size={13} tone={color.amber}>{error}</Text> : null}
                </>
            )}
        </View>
    );
}
