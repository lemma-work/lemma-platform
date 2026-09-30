export const TOUR_STEPS = [
    { name: "Give it a job", says: "Give your teammate a name and its first responsibility." },
    { name: "Let your people in", says: "Invite the people who need it. Choose who can work with it and what each of them can see." },
    { name: "It builds what the job needs", says: "Apps, tables and workflows, kept in its space, where your team works in them too." },
    { name: "It writes down what it learns", says: "Correct it once. It writes the lesson down where your team can read it." },
    { name: "Reach it where you work", says: "Connect Slack, Telegram or WhatsApp to reach your teammate there." },
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
