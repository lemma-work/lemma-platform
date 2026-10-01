/** A DECISION node's question: what it asks, and where each answer goes.
 *
 *  `config.question` (`domain/nodes/decision.py`) is asked only when none of
 *  the node's rules matched. Its `branches` map an option to the node that
 *  answer goes to, and `on_open` is where a run goes when nothing could
 *  answer. Like a rule's target, none of them is an edge — a walk that follows
 *  only edges loses every one of these arms.
 *
 *  Shared by `shape.ts` (what a workflow does) and `run-tree.ts` (what a run
 *  did), which read the same config and must agree on where it can go.
 */

import { isRecord, str } from "./runs";

export interface QuestionBranch {
    /** The option that takes this branch. */
    answer: string;
    target: string;
}

export interface Question {
    /** What it asks, when the question is written inline. */
    prompt: string | null;
    /** The decider it asks by name (`email-triage`, `system:…`), if any. */
    decider: string | null;
    /** In the order the author wrote them. */
    branches: QuestionBranch[];
    /** Where a run goes when the question is left open. */
    onOpen: string | null;
}

export function readQuestion(config: Record<string, unknown> | null | undefined): Question | null {
    const raw = config && isRecord(config.question) ? config.question : null;
    if (!raw) return null;
    const branches = isRecord(raw.branches)
        ? Object.entries(raw.branches).flatMap(([answer, target]) => {
            const to = str(target);
            return to ? [{ answer, target: to }] : [];
        })
        : [];
    return { prompt: promptOf(raw), decider: str(raw.decider), branches, onOpen: str(raw.on_open) };
}

/** The inline question's prompt: the one it branches on, else its only one. */
function promptOf(raw: Record<string, unknown>): string | null {
    const definition = isRecord(raw.definition) ? raw.definition : null;
    const questions = definition && isRecord(definition.questions) ? definition.questions : null;
    if (!questions) return null;
    const key = str(raw.question_key) ?? Object.keys(questions)[0];
    const question = key && isRecord(questions[key]) ? questions[key] : null;
    return str(question?.prompt);
}

/** Every node the question can hand a run to, branches first. */
export function questionTargets(question: Question | null): string[] {
    if (!question) return [];
    return [...question.branches.map((branch) => branch.target), ...(question.onOpen ? [question.onOpen] : [])];
}

/** "Asks: What should happen to it? → act / ignore". */
export function sayQuestion(question: Question): string {
    const asks = question.prompt
        ? "Asks: " + question.prompt
        : "Asks " + (question.decider ?? "a question the payload did not name");
    const answers = question.branches.map((branch) => branch.answer);
    return answers.length ? asks + " → " + answers.join(" / ") : asks;
}
