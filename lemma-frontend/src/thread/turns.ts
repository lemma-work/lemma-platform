import { isPlanToolName, planStepsFromToolInvocation, type PlanStepState } from "lemma-sdk";
import { isDisplayResourceTool, parseDisplayResource, type DisplayResource } from "./display-resource";
import { parseToolCard, type SignInAsk, type ToolCard } from "./tool-cards";
import { toolKey, toolLabel, toolTitle } from "./tool-name";
import {
    approvalDetails,
    askQuestions,
    isAskTool,
    isInteractionTool,
    resolvedAnswers,
    resolvedDecision,
    type ApprovalDetails,
    type AskQuestion,
} from "./approval";

/** Turning a stream of messages into something a person reads.
 *
 *  A conversation is a sequence of TURNS. A turn is one person's message and
 *  the response to it: ask → work → result.
 *
 *  **Everything a turn shows is ONE list, in the order it happened.** That is
 *  the rule this file is built around, and it was learned the hard way — twice,
 *  here and upstream. Speech, cards and questions in three buckets drawn one
 *  after another puts a question the run is *blocked on* above the widget it
 *  was asking about, and strands an approval at the bottom of the pane far
 *  from the work that raised it. Chronology fixes both by construction: the
 *  run is paused at the ask, so nothing can come after it.
 *
 *  The trace is the deliberate exception. Reasoning and tool calls collapse
 *  behind one status line, because that is work rather than conversation.
 *  Speech never joins them: an intermediate "let me check the table first" is
 *  something the teammate said to you, and demoting it into a trace row
 *  labelled "Said" hid half a conversation behind a disclosure triangle.
 *
 *  Transport is the SDK's job (`useAssistantSession`). This file is only about
 *  presentation, which is why it takes plain messages and returns plain data
 *  with no client in sight. */

export interface Note {
    kind: "tool" | "thought";
    label: string;
    detail: string;
    /** Whether `detail` is the agent's own sentence rather than a summary of
     *  its arguments. The two read differently and should look different: one
     *  is somebody talking, the other is this app describing a call. */
    said?: boolean;
    /** The read of this tool call, when there is one.
     *
     *  A note rather than an item, and that distinction is the whole point of
     *  the fold. A run makes dozens of tool calls; putting a card in the
     *  transcript for each one buries the two or three sentences the teammate
     *  actually said under a wall of terminal output and search results. The
     *  card is the *expanded* form of the step it already was — open the
     *  steps and you get the command and its output instead of a grey string.
     *
     *  The exception is a call that hands control back: a sign-in the run is
     *  waiting on is not work, it is a question, and it belongs in the
     *  transcript beside the approval card for the same reason. */
    card?: ToolCard;
    /** The call this step is, so the live indicator can find it. */
    toolCallId?: string;
    /** Made by a sub-agent (`parent_call_id`), and drawn under the `task`
     *  that started it rather than as the run's own step. */
    nested?: boolean;
}

/** A pause: `request_approval` or `ask_user`, carded where the run stopped.
 *
 *  `id` is the **tool call id**, and it is also the approval id — resolving is
 *  a POST against this exact string. There is no separate identifier to fetch.
 *  See `approval.ts`. */
export interface Interaction {
    id: string;
    kind: "approval" | "question";
    details: ApprovalDetails;
    questions: AskQuestion[];
    /** Empty until the tool return lands. Non-empty means it is answered, and
     *  says what it settled on. */
    decision: string;
    /** What a resolved question was answered with, keyed by question header. */
    answers: Record<string, unknown>;
    /** No tool return yet — the run is still waiting on a person. */
    open: boolean;
    at: string;
}

export type { PlanStepState };

export type TurnItem =
    | { kind: "text"; id: string; text: string; at: string }
    | { kind: "resource"; id: string; toolCallId?: string; resource: DisplayResource }
    | { kind: "interaction"; id: string; interaction: Interaction }
    /** The work, written down by the agent that is doing it. */
    | { kind: "plan"; id: string; steps: PlanStepState[] }
    /** A tool call this app reads rather than summarises — a terminal session,
     *  the sources behind an answer, a run that is asleep. */
    | { kind: "tool-card"; id: string; toolCallId?: string; card: ToolCard };

export interface Turn {
    id: string;
    day?: string;
    human?: { id: string; text: string; at: string };
    /** Reasoning and tool calls, folded behind one line. */
    notes: Note[];
    /** Speech, cards and pauses, in the order they happened. */
    items: TurnItem[];
    notice?: string;
    /** First and last assistant activity, for "Worked for 2m 14s". */
    startedAtMs?: number;
    endedAtMs?: number;
    /** Set on the last turn while its run is still going. */
    live?: boolean;
}

export interface RawMessage {
    id?: string;
    role?: string;
    kind?: string;
    text?: string | null;
    content?: string | null;
    tool_name?: string | null;
    tool_args?: unknown;
    tool_result?: unknown;
    tool_call_id?: string | null;
    sequence?: number;
    /** The run it belongs to. A message sent mid-run belongs to that run even
     *  when a later one delivers it; see `queued.ts`. */
    agent_run_id?: string | null;
    created_at?: string;
    /** `tool_source`, `tool_title`, `parent_call_id` and the rest of what the
     *  Agent Host says about a call. */
    metadata?: Record<string, unknown> | null;
}

export function clockOf(iso?: string | null): string {
    if (!iso) return "";
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function dayOf(iso?: string | null): string {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    if (date.toDateString() === new Date().toDateString()) return "Today";
    return date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

function msOf(iso?: string | null): number | undefined {
    if (!iso) return undefined;
    const at = new Date(iso).getTime();
    return Number.isNaN(at) ? undefined : at;
}

/** "9s", "2m 14s", "1h 3m". Long enough to be worth saying, short enough to sit
 *  inside a status line. */
export function spanOf(fromMs?: number, toMs?: number): string {
    if (fromMs === undefined || toMs === undefined) return "";
    const seconds = Math.round((toMs - fromMs) / 1000);
    if (seconds < 1) return "";
    if (seconds < 60) return seconds + "s";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + "m " + (seconds % 60) + "s";
    return Math.floor(minutes / 60) + "h " + (minutes % 60) + "m";
}

/** A tool's arguments as one short line — the command or path is what a
 *  person wants, not a serialised object. */
/** The agent's own one-line statement of intent, when it gave one.
 *
 *  Not a field this app invented a use for: the backend describes it, in as
 *  many words, as "One-line statement of intent, shown to the user" — a shared
 *  `comment` on every browser, web and shell tool. It was being thrown away,
 *  and the step said `comment, full_page, instructions` instead, which is the
 *  *names of the arguments* and tells nobody anything.
 *
 *  It beats the alternatives on both sides. Against an argument dump it is
 *  prose. Against the thinking it sits beside, it is one considered line
 *  rather than a paragraph of the agent talking itself through a dead end —
 *  which is worth reading afterwards and is not what you want to watch.
 */
export function commentOf(args: unknown): string {
    if (!args || typeof args !== "object") return "";
    const value = (args as Record<string, unknown>).comment;
    if (typeof value !== "string") return "";
    const clean = value.replace(/\s+/g, " ").trim();
    return clean.length > 140 ? clean.slice(0, 139).trimEnd() + "…" : clean;
}

/** What a step says when the agent did not say anything itself. */
export function argSummary(args: unknown): string {
    if (!args || typeof args !== "object") return "";
    const record = args as Record<string, unknown>;
    for (const key of ["command", "path", "file_path", "query", "name", "url", "content"]) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
            const clean = value.replace(/\s+/g, " ").trim();
            return clean.length > 80 ? clean.slice(0, 80) + "…" : clean;
        }
    }
    return Object.keys(record).slice(0, 3).join(", ");
}

function textOf(message: RawMessage): string {
    return (message.text ?? message.content ?? "").trim();
}

export interface Streaming {
    text: string;
    thinking: string;
    /** The call in flight. `args` matters as much as the name: it carries the
     *  agent's `comment`, which is the only thing that can say what this step
     *  is *for* while it is still happening. */
    tool: { toolName: string; toolCallId?: string; args?: Record<string, unknown> } | null;
}

/** What the run is doing right now, as one more step rather than a row of its
 *  own. Rendering it separately gave a reply two step rows — "6 steps · Exec
 *  command" with "working · request_approval" beneath it — which reads as two
 *  things happening when it is one thing, a step further along. */
export function liveNote(streaming: Streaming, landed: Note[] = []): Note[] {
    if (streaming.text) return [];
    if (streaming.tool) {
        /* A local agent's call lands whole, and the host announces it as
           running *after* the message, so the step is already on the list —
           as a card still waiting on its return. A second row for it would
           read as two calls. */
        const callId = streaming.tool.toolCallId;
        if (callId && landed.some((note) => note.toolCallId === callId)) return [];
        /* A pause arrives as a tool call like any other, and for the moment
           between the stream announcing it and the message landing it would
           otherwise read as a step called "request_approval" — the envelope's
           name, which is exactly the thing this pass is about not showing. */
        if (isInteractionTool(streaming.tool.toolName)) {
            return [{ kind: "tool", label: "Waiting on you", detail: "" }];
        }
        /* And the line below it did show the envelope's name — `exec_command`
           where a landed step says "Exec command" — because it was reading the
           raw tool name two lines under a comment complaining about exactly
           that. It also dropped the arguments, which is why the closed row sat
           still through a four-minute run: with no comment on the live step it
           fell back to the last one that had landed, so the line described
           something the agent had finished with several steps ago. */
        const said = commentOf(streaming.tool.args);
        return [{
            kind: "tool",
            label: toolLabel(streaming.tool.toolName),
            detail: said || argSummary(streaming.tool.args),
            said: Boolean(said),
        }];
    }
    if (streaming.thinking) return [{ kind: "thought", label: "Thought", detail: streaming.thinking }];
    return [];
}

/** A tool call as a step in the fold. */
function step(message: RawMessage, metadata: Record<string, unknown> | null, earlier: Note[]): Note {
    const said = commentOf(message.tool_args);
    const parent = typeof metadata?.parent_call_id === "string" ? metadata.parent_call_id : "";
    return {
        kind: "tool",
        label: toolLabel(message.tool_name ?? "tool", metadata),
        /* The agent's own line first; then, for a local agent's native tool,
           the adapter's title ("Search for 'two'"), which says more than the
           names of the arguments do. */
        detail: said || toolTitle(metadata) || argSummary(message.tool_args),
        said: Boolean(said),
        toolCallId: message.tool_call_id ?? undefined,
        /* Nested only under a task this turn actually shows. A parent from
           a page not yet loaded would indent a step under nothing. */
        nested: Boolean(parent) && earlier.some((note) => note.toolCallId === parent),
    };
}

export function buildTurns(messages: RawMessage[]): Turn[] {
    const ordered = [...messages].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));

    /* Returns are read ahead of time rather than in sequence: an interaction
       card has to know whether it was answered, and the answer arrives in a
       later message than the question. Keyed by tool call id, which is the
       only thing tying the two together. */
    const returns = new Map<string, RawMessage>();
    for (const message of ordered) {
        if ((message.kind ?? "") === "TOOL_RETURN" && message.tool_call_id) {
            returns.set(message.tool_call_id, message);
        }
    }

    const turns: Turn[] = [];
    let lastDay = "";
    let current: Turn | null = null;

    const open = (seed: RawMessage): Turn => {
        const day = dayOf(seed.created_at);
        const turn: Turn = {
            id: seed.id ?? "turn-" + turns.length,
            notes: [],
            items: [],
            day: day && day !== lastDay ? ((lastDay = day), day) : undefined,
        };
        turns.push(turn);
        return turn;
    };

    /** Every assistant message extends the turn's working span. */
    const worked = (turn: Turn, message: RawMessage) => {
        const at = msOf(message.created_at);
        if (at === undefined) return;
        if (turn.startedAtMs === undefined) turn.startedAtMs = at;
        turn.endedAtMs = at;
    };

    for (const message of ordered) {
        const kind = message.kind ?? "TEXT";

        if (kind === "THINKING") {
            const text = textOf(message);
            if (!current) current = open(message);
            worked(current, message);
            if (text) current.notes.push({ kind: "thought", label: "Thought", detail: text });
            continue;
        }

        if (kind === "TOOL_CALL") {
            if (!current) current = open(message);
            worked(current, message);

            /* A pause is not work. It is the run handing control back, and it
               belongs in the conversation at the point it happened — never
               folded into the trace, where a person would have to open a
               disclosure to discover they are being asked something. */
            const metadata = message.metadata ?? null;
            if (isInteractionTool(message.tool_name, metadata)) {
                const callId = message.tool_call_id ?? message.id ?? "";
                if (callId) {
                    const answered = returns.get(callId);
                    current.items.push({
                        kind: "interaction",
                        id: message.id ?? "i" + current.items.length,
                        interaction: {
                            id: callId,
                            kind: isAskTool(message.tool_name, metadata) ? "question" : "approval",
                            details: approvalDetails(message.tool_args, textOf(message)),
                            questions: isAskTool(message.tool_name, metadata) ? askQuestions(message.tool_args) : [],
                            decision: answered ? resolvedDecision(answered.tool_result) : "",
                            answers: answered ? resolvedAnswers(answered.tool_result) : {},
                            open: !answered,
                            at: clockOf(message.created_at),
                        },
                    });
                    continue;
                }
            }

            /* A plan is not a working note. `update_plan` was folding into
               the trace with every other tool call, which put the one thing
               in a run that says what it is going to do behind a disclosure
               nobody opens.

               One card per turn, not one per revision: a plan rewritten five
               times is one list that changed five times, and five checklists
               would be five answers to the same question. The last one wins,
               in the place the first one appeared, so the list does not jump
               down the transcript every time a step closes. */
            if (toolKey(message.tool_name, metadata) && isPlanToolName(message.tool_name ?? "")) {
                const asRecord = (value: unknown) =>
                    value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined;
                const steps = planStepsFromToolInvocation({
                    toolName: message.tool_name ?? "",
                    args: asRecord(message.tool_args) ?? {},
                    /* The backend's own copy, when the return has landed. The
                       SDK prefers it for a single-step update, where the call
                       args carry one row and the result carries the list. */
                    result: message.tool_call_id ? asRecord(returns.get(message.tool_call_id)?.tool_result) : undefined,
                });
                if (steps.length) {
                    const existing = current.items.findIndex((item) => item.kind === "plan");
                    const item: TurnItem = { kind: "plan", id: message.id ?? "p" + current.items.length, steps };
                    if (existing >= 0) current.items[existing] = item;
                    else current.items.push(item);
                    continue;
                }
            }

            if (isDisplayResourceTool(message.tool_name, metadata)) {
                const resource = parseDisplayResource(message.tool_args);
                if (resource) {
                    /* A widget the harness rejected has nothing to show, and
                       seven rows saying so is worse than the silence. The
                       agent says what went wrong in its own words anyway.

                       "Nothing to show" means none of the three sources, and
                       the list has to stay whole: when `path` arrived this read
                       two of three and silently dropped every file-backed
                       widget on the floor -- the resource never became a card,
                       so there was nothing on screen to debug. */
                    const emptyWidget =
                        resource.type === "WIDGET" &&
                        !resource.publicUrl &&
                        !resource.path &&
                        !(resource.content && /<[a-z][\s\S]*>/i.test(resource.content));
                    if (!emptyWidget) {
                        current.items.push({
                            kind: "resource",
                            id: message.id ?? "r" + current.items.length,
                            toolCallId: message.tool_call_id ?? undefined,
                            resource,
                        });
                    }
                    continue;
                }
            }

            /* The rest of the toolset, for the handful of calls whose result is
               the point: a command and what it printed, the sources behind an
               answer, a paused sign-in. Everything here is a read of a wire
               value, so a shape that does not fit comes back null and lands on
               the note below — the fallback has to stay reachable for the
               thirty-odd tools nothing claims. */
            const card = parseToolCard({
                toolName: message.tool_name,
                args: message.tool_args,
                result: message.tool_call_id ? returns.get(message.tool_call_id)?.tool_result : undefined,
                answered: message.tool_call_id ? returns.has(message.tool_call_id) : false,
                atMs: msOf(message.created_at),
                metadata,
            });
            if (card) {
                /* Waiting on a person is the only kind that leaves the fold.
                   A resolved one stays out too, for the same reason a decided
                   approval does: what you were asked and what you answered is
                   a record, and scrolling back to find it is reasonable. */
                if (card.kind === "sign-in") {
                    current.items.push({
                        kind: "tool-card",
                        id: message.id ?? "t" + current.items.length,
                        toolCallId: message.tool_call_id ?? undefined,
                        card,
                    });
                    continue;
                }
                current.notes.push({ ...step(message, metadata, current.notes), card });
                continue;
            }

            current.notes.push(step(message, metadata, current.notes));
            continue;
        }

        if (kind === "TOOL_RETURN") continue;

        const text = textOf(message);
        if (!text) continue;

        if (kind === "NOTIFICATION" || message.role === "system") {
            const turn = open(message);
            turn.notice = text;
            current = null;
            continue;
        }

        if (message.role === "user") {
            current = open(message);
            current.human = { id: message.id ?? "u" + turns.length, text, at: clockOf(message.created_at) };
            continue;
        }

        if (!current) current = open(message);
        worked(current, message);
        current.items.push({
            kind: "text",
            id: message.id ?? "s" + current.items.length,
            text,
            at: clockOf(message.created_at),
        });
    }

    return turns.filter((turn) => turn.human || turn.notes.length > 0 || turn.items.length > 0 || turn.notice);
}

/** The pause the run is currently blocked on, if any. Read from the transcript
 *  rather than fetched: the approval IS the tool call, so the messages already
 *  hold it — and they hold it the instant it streams in, which a list endpoint
 *  polled on a status change does not. */
export function openInteraction(turns: Turn[]): Interaction | null {
    for (let index = turns.length - 1; index >= 0; index -= 1) {
        const items = turns[index].items;
        for (let item = items.length - 1; item >= 0; item -= 1) {
            const entry = items[item];
            if (entry.kind === "interaction" && entry.interaction.open) return entry.interaction;
        }
    }
    return null;
}

/** A `browser_sign_in` still waiting on somebody.
 *
 *  Its own reader rather than a third kind inside `openInteraction`, because
 *  the two resolve differently: an approval is answered with a POST from this
 *  app, and this one is answered by a person going to another page and signing
 *  in. What both share is that the composer has to say the run is stopped —
 *  a paused sign-in looked exactly like an idle conversation, which is how a
 *  blocked run could sit there all afternoon with nobody told. */
export function openSignIn(turns: Turn[]): SignInAsk | null {
    for (let index = turns.length - 1; index >= 0; index -= 1) {
        const items = turns[index].items;
        for (let item = items.length - 1; item >= 0; item -= 1) {
            const entry = items[item];
            if (entry.kind === "tool-card" && entry.card.kind === "sign-in" && !entry.card.resolved) return entry.card;
        }
    }
    return null;
}
