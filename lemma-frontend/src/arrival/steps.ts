/** The steps of the way in, and which ones each door walks.
 *
 *  The way in is asked by the Lemma being made, in its own voice, one thing
 *  at a time:
 *
 *  - "name": your name, asked by Lemma itself before anything is made;
 *  - "team": only when your email lets you join your company's organization,
 *    because joining shows you to colleagues;
 *  - "lemma": the Lemma says hello -- the one just made for you, or the ones
 *    you came into, with the rest to join;
 *  - "apps": what you do, and the apps it would add for that;
 *  - "reach": while those go in, the places you can write to it from --
 *    its email address, WhatsApp, Telegram, Slack -- and your first hello;
 *  - "ready": what is in, where it can be reached, and the way into it.
 *
 *  "reach" and "ready" are walked after the apps step even when none was
 *  picked: a Lemma with no apps can still be written to. */

export type ArrivalStepId = "name" | "team" | "lemma" | "apps" | "reach" | "ready";

export const STEP_NAMES: Record<ArrivalStepId, string> = {
    name: "Your name",
    team: "Your team",
    lemma: "Your Lemma",
    apps: "Apps",
    reach: "Where to reach it",
    ready: "Ready",
};

/** How they came into their organization, as `POST /users/me/first-workspace`
 *  says it. */
export type WorkspaceEntry = "existing" | "surface_join" | "invitation" | "domain_join" | "new_org";

/** The steps a door walks, in order. `name` asks the name first; `team` puts
 *  the company choice before the Lemma; `apps` is false where nothing is
 *  offered (an invitation accepted from Home by somebody already at work). */
export function stepsFor({ name = true, team = false, apps = true }: { name?: boolean; team?: boolean; apps?: boolean } = {}): ArrivalStepId[] {
    return [
        ...(name ? (["name"] as const) : []),
        ...(team ? (["team"] as const) : []),
        "lemma",
        ...(apps ? (["apps", "reach", "ready"] as const) : []),
    ];
}

/** The step after `at`, or null when `at` was the last. */
export function nextStep(steps: readonly ArrivalStepId[], at: ArrivalStepId): ArrivalStepId | null {
    const index = steps.indexOf(at);
    return index >= 0 && index + 1 < steps.length ? steps[index + 1] : null;
}

/** What the Lemma on the left is doing, in a word, at each step. */
export function statusAt(at: ArrivalStepId, { made }: { made: boolean }): string {
    switch (at) {
        case "name":
            return "Listening";
        case "team":
            return "Your team is here";
        case "lemma":
            return made ? "Just made" : "You're in";
        case "apps":
            return "Listening";
        case "reach":
            return "Working";
        case "ready":
            return "Ready";
    }
}
