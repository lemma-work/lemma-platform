/** The steps of the way in, and which ones each door walks.
 *
 *  Every door ends the same way: an organization, its shared Lemma, the
 *  question of what you do, then whatever suggestions follow from the answer.
 *  Doors differ only at the front. Somebody who made a new organization is
 *  shown what was made; somebody who joined one that already has people
 *  (invited, or let in by their email domain) is shown its Lemmas to join.
 *
 *  `plugins` is the step after the job question. It is listed so the steps
 *  along the top already name it, and is skipped until a component fills the
 *  slot in `plugins-step.tsx`. */

export type ArrivalStepId = "workspace" | "lemmas" | "work" | "plugins";

export const STEP_NAMES: Record<ArrivalStepId, string> = {
    workspace: "Your organization",
    lemmas: "Lemmas to join",
    work: "What you do",
    plugins: "Plugins",
};

/** How they came into their organization, as `POST /users/me/first-workspace`
 *  says it. */
export type WorkspaceEntry = "existing" | "surface_join" | "invitation" | "domain_join" | "new_org";

/** The steps a door walks, in order, from how the organization was found.
 *  `asksWork` is false for somebody who already answered (on another door,
 *  or earlier), and then the job step is left out rather than shown done. */
export function stepsFor(entry: WorkspaceEntry | null, { asksWork, hasPlugins }: { asksWork: boolean; hasPlugins: boolean }): ArrivalStepId[] {
    const front: ArrivalStepId[] = entry === "new_org" || entry === null ? ["workspace"] : ["workspace", "lemmas"];
    return [
        ...front,
        ...(asksWork ? (["work"] as const) : []),
        ...(hasPlugins ? (["plugins"] as const) : []),
    ];
}

/** The step after `at`, or null when `at` was the last. */
export function nextStep(steps: readonly ArrivalStepId[], at: ArrivalStepId): ArrivalStepId | null {
    const index = steps.indexOf(at);
    return index >= 0 && index + 1 < steps.length ? steps[index + 1] : null;
}
