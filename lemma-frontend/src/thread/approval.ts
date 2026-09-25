import { toolKey } from "./tool-name";

export interface ApprovalDetails {
    /** What is about to happen, in the agent's own words where it wrote them. */
    title: string;
    /** Why it is asking. */
    request: string;
    /** The real call's real arguments, at most four. */
    params: { name: string; value: string }[];
    /** The tool that will actually run. */
    toolName?: string;
    canApproveForSession: boolean;
}

export interface AskOption {
    label: string;
    description?: string;
}

export interface AskQuestion {
    question: string;
    /** Doubles as the answer's key in the response payload. */
    header: string;
    options: AskOption[];
    multiSelect: boolean;
}

export type ApprovalDecision = "APPROVE_ONCE" | "APPROVE_FOR_SESSION" | "DENY";

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

type ToolMetadata = Record<string, unknown> | null | undefined;

/** Lemma's own pausing tools only. Someone else's MCP server may well have an
 *  `ask_user`, and nothing this app posts can answer it. */
export function isApprovalTool(name: string | null | undefined, metadata?: ToolMetadata): boolean {
    const tool = toolKey(name, metadata);
    return tool === "request_approval" || tool === "user_approval";
}

export function isAskTool(name: string | null | undefined, metadata?: ToolMetadata): boolean {
    return toolKey(name, metadata) === "ask_user";
}

/** Whether a tool pauses the run for a person. Both kinds end the run and
 *  resume through the same endpoint. */
export function isInteractionTool(name: string | null | undefined, metadata?: ToolMetadata): boolean {
    return isApprovalTool(name, metadata) || isAskTool(name, metadata);
}

/** What a resolved interaction settled on, read from the tool return. The
 *  backend writes it at the top level or nested under `output`. */
export function resolvedDecision(result: unknown): string {
    const record = asRecord(result);
    return asString(record.decision) || asString(asRecord(record.output).decision);
}

/** What a resolved `ask_user` was answered with, keyed by question header.
 *  A card that records a decision has to record the answer too — otherwise a
 *  question scrolled back to says it was answered and will not say with what. */
export function resolvedAnswers(result: unknown): Record<string, unknown> {
    const record = asRecord(result);
    const answers = asRecord(record.answers);
    return Object.keys(answers).length ? answers : asRecord(asRecord(record.output).answers);
}

/** The same decision means different things on the two cards. A question that
 *  came back APPROVE_ONCE was answered, not approved, and saying "Approved
 *  once" over a choice the reader made is the envelope leaking into the
 *  sentence again. */
export function decisionLabel(decision: string, kind: "approval" | "question" = "approval"): string {
    if (kind === "question") return decision === "DENY" ? "Skipped" : "Answered";
    if (decision === "APPROVE_FOR_SESSION") return "Approved for this conversation";
    if (decision === "APPROVE_ONCE") return "Approved once";
    if (decision === "DENY") return "Denied";
    return "Answered";
}

/** `exec_command` → "Exec command". Display only. */
export function toolTitle(name: string): string {
    const spaced = name.replace(/[-_]+/g, " ").trim();
    return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : name;
}

/** A value a person can read. Objects and arrays are summarised rather than
 *  printed: a wall of JSON in an approval card is the same failure as printing
 *  the key names — technically the truth, and unreadable at the one moment it
 *  has to be read. */
function asValue(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) return value.length + (value.length === 1 ? " item" : " items");
    const keys = Object.keys(asRecord(value));
    return keys.length ? "{ " + keys.slice(0, 3).join(", ") + (keys.length > 3 ? ", …" : "") + " }" : "{}";
}

/** Some tool names carry their command as one long string. That string is the
 *  single most useful thing on the card, so it is never truncated here — the
 *  card wraps it instead. */
export function approvalDetails(toolArgs: unknown, fallbackText?: string): ApprovalDetails {
    const args = asRecord(toolArgs);
    const toolName = asString(args.tool_name);
    const title = asString(args.title);
    const reason = asString(args.reason);

    const inner = asRecord(args.args);
    const params = Object.entries(inner)
        .filter(([, value]) => asValue(value) !== "")
        .slice(0, 4)
        .map(([key, value]) => ({ name: toolTitle(key), value: asValue(value) }));

    return {
        /* Empty when the call gave nothing to name it with, and left that way
           on purpose: what a nameless pause should be called depends on who is
           asking and what kind of asking it is, and neither of those is in
           here. `interactionHeading` knows both. */
        title: title || (toolName ? toolTitle(toolName) : "") || asString(fallbackText),
        request:
            reason ||
            (toolName ? "The teammate wants to run " + toolTitle(toolName) + "." : "") ||
            asString(fallbackText),
        params,
        toolName: toolName || undefined,
        /* Approving for the session keeps this kind of action from asking
           again until it expires. Always offered here: the pod does not talk
           to a local agent host, which is the one case upstream where "for
           session" would promise a grant it cannot keep. */
        canApproveForSession: true,
    };
}

/** What the card is called.
 *
 *  Nearly always the agent's own words — a `request_approval` carries a title
 *  and a tool name, and a well-written `ask_user` carries a title too. The
 *  fallback is for the one that does not, and it was "Approval required": the
 *  wrong noun for a question, in the largest type on the card, over four
 *  options and an Answer button. Nothing was being approved.
 *
 *  So the fallback says who is asking and what they want, which is what the
 *  heading of a pause is for. It changes tense once it is settled, because the
 *  card stays in the transcript afterwards and a record that still says
 *  somebody "needs your answer" is a record that reads as an open request. */
export function interactionHeading(
    kind: "approval" | "question",
    title: string,
    teammate: string,
    settled: boolean,
): string {
    if (title) return title;
    const who = asString(teammate) || "Your teammate";
    if (kind === "question") return settled ? who + " asked you" : who + " needs your answer";
    return settled ? who + " asked to run something" : who + " needs your approval";
}

/** The questions inside an `ask_user` call. A question with no options is one
 *  this UI cannot answer, so it is dropped rather than drawn as an empty card —
 *  the agent's own text still says what it wanted. */
export function askQuestions(toolArgs: unknown): AskQuestion[] {
    const raw = asRecord(toolArgs).questions;
    if (!Array.isArray(raw)) return [];
    return raw
        .map((entry): AskQuestion => {
            const record = asRecord(entry);
            const options = Array.isArray(record.options)
                ? record.options
                      .map((option) => {
                          const item = asRecord(option);
                          return {
                              label: asString(item.label),
                              description: asString(item.description) || undefined,
                          };
                      })
                      .filter((option) => option.label)
                : [];
            return {
                question: asString(record.question),
                header: asString(record.header),
                options,
                multiSelect: record.multi_select === true,
            };
        })
        .filter((question) => question.header && question.options.length > 0);
}
