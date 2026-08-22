import { TELEGRAM_STEP } from "@/lib/pods/new-pod-conversation";
import { DEFAULT_RESPONDER_NAME } from "@/lib/utils/agents";

/**
 * The four things a brand new pod offers, and what each one asks for.
 *
 * A pod used to open by sending `"Hi"` as the user and spending the first turn
 * on a welcome nobody asked for. The greeting existed because a pod created
 * from nothing has nothing better to send — so this is the something better:
 * four sentences a person can pick instead of typing one, each of which puts
 * the conversation on the stated-intent branch that already exists.
 *
 * Every option is a whole message, not a stem to finish. A stem would be a
 * stronger brief, but the composer on `/conversations/new` cannot carry
 * `instructions` alongside a hand-typed message the way pod home can, and an
 * option that loses its framing is worse than one that asks a question first.
 * Where the brief is genuinely missing — an app, an agent — the instructions
 * spend the first turn getting it.
 */

export type PodWelcomeOptionId =
    | "surface"
    | "app"
    | "agent"
    | "people"
    /** Not a card: the zero-effort path in the footer. */
    | "surprise";

/**
 * The ids that get a card, which is every id but the footer's. Named so the
 * artwork map can be exhaustive over the grid without being asked for a
 * picture of a text button.
 */
export type PodWelcomeCardId = Exclude<PodWelcomeOptionId, "surprise">;

export interface PodWelcomeOption {
    id: PodWelcomeOptionId;
    /** Written as an instruction to Lem, because that is what clicking it is. */
    title: string;
    /** One line under the title. Never a second sentence. */
    note: string;
    /** Sent as the user: the only way to start a turn here. */
    message: string;
    /** Framing carried by that one message, and spent by it. */
    instructions: string;
}

/** An option that gets a picture and a place in the grid. */
export interface PodWelcomeCard extends PodWelcomeOption {
    id: PodWelcomeCardId;
    /**
     * Which of the five sanctioned identity tones the card wears, applied as
     * `lm-identity-hue-N` so the tint and the artwork both come out of the
     * palette the rest of the product draws agents in.
     *
     * Four cards, four different tones, on the one screen where that is right:
     * a grid of identical grey panels reads as a settings page, and this is the
     * first thing anyone sees.
     *
     * Tone 3 is missing on purpose. It is `#c22f15`, the same red this product
     * raises errors in, and a card that says "add your people" in the error
     * colour is a warning nobody meant to write. The Telegram card takes the
     * violet instead, which is Lem's own tone and the right one for the card
     * that is about sending Lem somewhere.
     */
    tone: 0 | 1 | 2 | 4;
}

/**
 * Order is the argument. Chat is first because it is the fastest thing in the
 * product that can be true — a minute to a bot on a phone that can message you
 * back — and someone who closes the tab before anything works never sees the
 * rest. The app is the larger idea and sits second; agents and people both get
 * better once something exists, so they follow.
 */
export const POD_WELCOME_OPTIONS: readonly PodWelcomeCard[] = [
    {
        id: "surface",
        tone: 0,
        title: `Get ${DEFAULT_RESPONDER_NAME} on Telegram`,
        note: "Then it can message you first.",
        message: "Put yourself on Telegram so I can reach you from my phone.",
        instructions: [
            "They asked for Telegram, so set it up now. Do not offer it again, do not ask whether they are sure, and do not explain what a surface is first.",
            TELEGRAM_STEP,
        ].join("\n\n"),
    },
    {
        id: "app",
        tone: 4,
        title: "Build an app",
        note: "A screen, with an agent behind it.",
        message: "Build me an app.",
        instructions: [
            "They want an app but have not said what for, so your first turn is one question: what job should it do, or who is it for. One question, then stop.",
            "Once they answer, build the smallest working version properly — real tables, believable sample data, an agent doing the work behind the screen. The app goes in front and the agents behind it. It should be alive the moment it opens, not a shell they have to fill in.",
        ].join("\n\n"),
    },
    {
        id: "agent",
        tone: 1,
        title: "Hire another agent",
        note: "One for each job.",
        message: "Make me another agent.",
        instructions: [
            "They want a second agent but have not said what for, so your first turn is one question: what should it handle. One question, then stop.",
            `Once they answer, create it with a name, a purpose, and the instructions that job actually needs, then say in one line what it will now do without being asked. It works off the same data and tools ${DEFAULT_RESPONDER_NAME} does — it is a colleague, not a copy.`,
        ].join("\n\n"),
    },
    {
        id: "people",
        tone: 2,
        title: "Add your people",
        note: "Same agents, same data.",
        message: "Add my team here so they can use you too.",
        instructions: [
            "They asked to bring someone in, so show them how rather than talking them out of it. Say in one line what the person lands in — the same agents, the same data, the same screens — and then walk them through sending the invitation.",
            "Only after that, if the pod is still empty, say plainly that an invitation lands better once something exists here, and offer to build that next. Never lead with it.",
        ].join("\n\n"),
    },
];

/**
 * The one control that asks nothing of anybody.
 *
 * Everything else on the door needs a decision, and the field needs a whole
 * sentence — which is the most work on the screen, aimed at the person least
 * likely to know what to type. This is the path for someone who wants to see
 * the thing before committing to a plan for it, and it is a fair trade: they
 * spend one click, and they get something on screen inside a turn.
 *
 * A widget, deliberately, and nothing more. Building tables and agents to
 * answer "show me" would take minutes and leave a mess behind in a pod they
 * have not decided to keep.
 */
export const POD_WELCOME_SURPRISE: PodWelcomeOption = {
    id: 'surprise',
    title: 'Surprise me',
    note: 'One click, something on screen.',
    message: 'Surprise me. Show me something you can do.',
    instructions: [
        'There is no brief and none is needed: they asked to be shown. Do not ask a question, and do not describe what you are about to do first.',
        'In this one turn, put one small live thing on screen — a widget through `display_resource`, with the `lemma-widget` skill loaded — that could only exist somewhere like this. Make it about them where you can. Keep it to about a minute of work.',
        'Do not create tables, agents, apps or workflows to do it. Finish by saying in one line what it would take to make it real, and then stop.',
    ].join('\n\n'),
};

/** What a person typed instead of picking, which is the strongest brief there is. */
export const POD_WELCOME_OWN_WORDS_INSTRUCTIONS =
    "They skipped the options and said this in their own words, so it is the brief. Take it at face value and start on it.";

export function podWelcomeOption(
    id: string | null | undefined,
): PodWelcomeOption | null {
    if (id === POD_WELCOME_SURPRISE.id) return POD_WELCOME_SURPRISE;
    return POD_WELCOME_OPTIONS.find((option) => option.id === id) ?? null;
}
