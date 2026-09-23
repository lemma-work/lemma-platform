import type { PlanStepState } from "@/thread/turns";

/** The one line under the teammate's name, decided in one place.
 *
 *  One place because there are two surfaces. The call bar and the call screen
 *  both say what the call is doing, and a rule written out at each of them is
 *  a rule that eventually answers differently depending on which half of the
 *  call somebody happens to be looking at.
 *
 *  The order is a priority, not a preference. An error is the only thing
 *  worth saying when there is one. Muted outranks everything after it because
 *  it is about whether the call works at all, and a person who cannot be
 *  heard needs telling before they are told what the teammate is up to.
 *
 *  Then the plan, which is the specific answer to "what is happening", and
 *  only then the generic one. */
export function statusLine({ error, muted, plan, thinking, teammate }: {
    error?: string | null;
    muted?: boolean;
    /** The teammate's own plan, as the transcript reads it. */
    plan?: PlanStepState[];
    thinking?: boolean;
    teammate: string;
}): string {
    if (error) return error;
    if (muted) return "Muted — it cannot hear you";

    /* An open step is authoritative and says itself. Requiring one was too
       strict: an agent that writes the whole plan first, or that closes each
       step before opening the next, has stretches with nothing in progress —
       and the line went blank in the middle of the work it was meant to
       describe. So the first unfinished step stands in, but only while the
       run is actually going, or a finished-with-us plan would keep claiming
       work long after the turn ended. */
    const steps = plan ?? [];
    const open = steps.findIndex((step) => step.status === "in_progress");
    const at = open >= 0 ? open : steps.findIndex((step) => step.status !== "completed");
    if (at >= 0 && (open >= 0 || thinking)) {
        return `Step ${at + 1} of ${steps.length} · ${steps[at].step}`;
    }

    if (thinking) return `${teammate} is still working — you can keep talking.`;
    return "Just talk. It is listening.";
}
