export const TOUR_STEPS = [
    { name: "Give it a job", says: "Give your teammate a name and its first responsibility." },
    { name: "Add your team", says: "Invite the people who need it. Choose who can work with it and what they can access." },
    { name: "It learns how you work", says: "Share context, correct its work, and teach it what good looks like." },
    { name: "Apps, workflows, memory", says: "It builds the apps the job needs, and your team works in them too." },
    { name: "Use it where you already are", says: "Connect Slack, Telegram or WhatsApp to reach your teammate there." },
] as const;

export type TourState = { mode: "guided" | "exploring"; step: number };
export type TourAction =
    | { type: "explore" }
    | { type: "scroll"; step: number }
    | { type: "step"; step: number };

export const INITIAL_TOUR: TourState = { mode: "guided", step: -1 };

/** Touching the workspace pauses the tour only for the step it happened on.
 *  Scrolling into another step is asking for that step, so the tour takes
 *  the workspace back: otherwise the copy beside it describes a screen the
 *  visitor is no longer looking at. */
export function tourReducer(state: TourState, action: TourAction): TourState {
    switch (action.type) {
        case "explore": return state.mode === "exploring" ? state : { ...state, mode: "exploring" };
        case "scroll": return state.step === action.step ? state : { mode: "guided", step: action.step };
        case "step": return { mode: "guided", step: action.step };
    }
}
