import { CheckIcon } from "@/ui/icons";
import type { PlanStepState } from "./turns";

/** The work, as the agent wrote it down.
 *
 *  `update_plan` is lifted out of the trace rather than folded in with every
 *  other tool call, which would put the one thing in a run that says what it
 *  intends to do behind a disclosure nobody opens. A plan is not working-out —
 *  it is the closest thing a run has to an agenda, and it belongs in the
 *  conversation.
 *
 *  Read-only on purpose. This is the agent's plan, not a shared to-do list:
 *  a checkbox here would imply the person can close a step the agent is in
 *  the middle of, and nothing on the other end would hear it. */
export function PlanCard({ steps }: { steps: PlanStepState[] }) {
    if (!steps.length) return null;

    const done = steps.filter((step) => step.status === "completed").length;

    /* A checklist, as a run's plan reads in a coding agent's transcript: a
       ring for what is next, a turning ring for the step in hand, a filled
       tick for what is done -- three states read at a glance without a
       legend, and no heading over a list that says what it is. */
    return (
        <section className="runplan" aria-label={`Plan, ${done} of ${steps.length} done`}>
            <ol className="runplan__steps">
                {steps.map((step, index) => (
                    <li
                        key={index + "-" + step.step}
                        className="runplan__step"
                        data-status={step.status}
                        /* The step being worked on is the one worth finding at
                           a glance, so it is the only one the list announces. */
                        aria-current={step.status === "in_progress" ? "step" : undefined}
                    >
                        <span className="runplan__mark" aria-hidden="true">
                            {step.status === "completed" ? <CheckIcon size={10} weight="bold" /> : null}
                        </span>
                        <span className="runplan__what">{step.step}</span>
                    </li>
                ))}
            </ol>
        </section>
    );
}
