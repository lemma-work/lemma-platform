export const TOUR_STEPS = [
    { tab: 0, name: "Give it a job", says: "Give your teammate a name and its first responsibility." },
    { tab: 0, name: "Add your team", says: "Invite the people who need it. Choose who can work with it and what they can access." },
    { tab: 3, name: "It learns how you work", says: "Share context, correct its work, and teach it what good looks like." },
    { tab: 1, name: "Apps, workflows, memory", says: "It builds apps for the job and keeps the work beside your conversation." },
    { tab: 0, name: "Use it where you already are", says: "Connect Slack, Telegram or WhatsApp to reach your teammate there." },
] as const;

export type TourState = { mode: "guided" | "exploring"; who: number; tab: number; step: number };
export type TourAction =
    | { type: "explore" }
    | { type: "scroll"; step: number }
    | { type: "teammate"; who: number }
    | { type: "tab"; tab: number }
    | { type: "resume" }
    | { type: "step"; step: number };

export const INITIAL_TOUR: TourState = { mode: "guided", who: 3, tab: 0, step: -1 };

/** Manual exploration owns the selection until the visitor explicitly resumes. */
export function tourReducer(state: TourState, action: TourAction): TourState {
    switch (action.type) {
        case "explore": return { ...state, mode: "exploring" };
        case "scroll":
            if (state.step === action.step) return state;
            return { ...state, step: action.step, tab: state.mode === "guided" && action.step >= 0 ? TOUR_STEPS[action.step].tab : state.tab };
        case "teammate": return { ...state, mode: "exploring", who: action.who };
        case "tab": return { ...state, mode: "exploring", tab: action.tab };
        case "resume": return { ...state, mode: "guided", tab: TOUR_STEPS[Math.max(0, state.step)].tab };
        case "step": return { ...state, mode: "guided", step: action.step, tab: TOUR_STEPS[action.step].tab };
    }
}
