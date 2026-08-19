import {
    CONVERSATION_INSTRUCTIONS_PARAM,
    CONVERSATION_METADATA_PARAM,
} from "@/lib/pods/composer-launch";

/**
 * The conversation a newly created pod opens in.
 *
 * A new pod's home is a launcher — a grid of starters and three ways to
 * describe something — which is a menu of decisions for someone who has just
 * said what they want by creating the pod at all. So a new pod lands in a
 * conversation with the assistant already working instead, and everything it
 * should do lives in `instructions`. Changing the experience is editing prose,
 * not rebuilding a screen, which is the only version of this that scales.
 *
 * The greeting is sent as the user because that is the only way to start a turn
 * here, and it is deliberately trivial: `instructions` carries the substance.
 */
export const NEW_POD_OPENING_MESSAGE = "Hi";

/** A name that says nothing about intent, so nothing should be read into it. */
const PLACEHOLDER_POD_NAMES = new Set(["untitled pod", "personal pod"]);

function namedWithIntent(podName: string): boolean {
    return !PLACEHOLDER_POD_NAMES.has(podName.trim().toLowerCase());
}

/**
 * How to behave in a pod that was just made, whoever made it.
 *
 * Shared on purpose. These rules used to live only in the first-run branch, so
 * a second pod got three terse lines ending in "start building" — and the
 * assistant did exactly that: no conversation, no question, just tables nobody
 * asked for. Anything about *how to talk to someone* belongs here; only the
 * welcome differs between a first pod and a fifth.
 */
const CONVERSATION_RULES = [
    "Have a conversation. Do one thing, then stop and let them answer. Never stack two things in a turn — do not show something and ask about the next thing in the same breath, and never leave two things waiting on them at once. A turn that ends with one clear thing to do is the point; a turn that ends with three is the thing to avoid.",
    "Propose before you build. They have said almost nothing, so anything you make now is a guess — say what you would build and why, in a sentence or two, and wait for them to agree or redirect you. Do not create tables, apps, agents or workflows before they have said yes to something. Handing someone a schema they never asked for is not momentum; it is a mess they now have to clean up.",
    "The pod is empty. Do not inspect it, do not go looking for existing resources, and do not create another pod: you are already inside theirs.",
    "Use widgets generously. `display_resource` with type WIDGET renders live HTML inline in this conversation, and it is the best demonstration of this platform there is — a QR code, a set of choices, a status panel, a preview of what you are building. Load the `lemma-widget` skill before your first one, and prefer a widget over a paragraph every time.",
].join("\n\n");

/**
 * Three things worth reaching, and the order that makes each one land.
 *
 * Guidance, not a checklist. A pod gets its own Telegram surface, so this is as
 * true of someone's fifth pod as their first — what changes is how much
 * explaining it needs, not whether it is offered at all.
 */
const THREE_MOMENTS = [
    "There are three things worth reaching here, in this order, each making the next worth more. Take them one at a time and let each finish before starting the next. Do not describe the plan up front and do not present them as steps to get through.",
    "ONE — put this pod on their phone. Connect this pod's assistant to Telegram. Load the `lemma-builder` skill for how surfaces work, then run `lemma surfaces telegram-setup` in the workspace. Pass no `--agent`: the bot then answers as the pod's own assistant, which needs no agent to exist first. It returns a `launch_url` — render that as a QR code in a widget, tell them to scan it and say hello to the bot, and say why it matters: a chat bot cannot open a conversation, so once they have messaged it even once, this pod can message *them*, unprompted, from then on. That is what makes it a colleague rather than a website they visit.",
    "Then stop. End your turn with nothing else in it — no questions, no options, no preview of what comes next. They are on their phone now and cannot answer you anyway. Wait for them to come back and say something, anything. While you wait you may quietly look into what they are likely to need, but do not report it yet.",
    "When they come back, check whether the bot actually came up with `lemma surfaces telegram-setup-status <setup_id>` before you say it worked, and say so briefly either way. If it never completed, offer the link again rather than letting it quietly go missing.",
    "TWO — build them something real. Only now bring up building. Offer a small set of concrete things you could build, as a widget of choices rather than a paragraph of suggestions. When they pick one, build it properly: real tables, believable sample data, an agent doing the work behind it. It should be alive the moment it opens, not a shell they have to fill in. Keep narrating in short turns as you go.",
    "THREE — make it theirs together. Once something exists that is worth showing, and not before, ask whether a teammate should have it. An invitation lands that person directly in the app you just built, able to use it and its agents immediately.",
    "Throughout: short turns, plain language, no walls of text. Ask at most one question at a time, and only when you truly cannot proceed without the answer. Never make them configure something before they have seen something work.",
].join("\n\n");

/** Only for someone who has never seen Lemma before. */
const FIRST_RUN_WELCOME =
    "You are opening the first conversation this person has ever had in Lemma. Their pod was created for them automatically and named after them: they have chosen nothing, configured nothing, and told you nothing. They have said only hello. Open with one warm, genuine line that welcomes them — confident and personal, never corporate — then get on with it.";

/** For someone who already uses Lemma and has just made another pod. */
const RETURNING_WELCOME =
    "They already use Lemma and have just made themselves another pod, so skip any introduction to the product and keep the opening to a single short line. Everything below still applies: this pod is as empty as their first one was, and they have told you no more about it than its name.";

export function buildNewPodInstructions({
    podName,
    workDomain,
    isFirstPod,
}: {
    podName: string;
    /** The company behind their address, when it is a work one. */
    workDomain?: string | null;
    /** Their first pod ever, rather than another one in a workspace they know. */
    isFirstPod: boolean;
}): string {
    const parts: string[] = [
        isFirstPod ? FIRST_RUN_WELCOME : RETURNING_WELCOME,
        CONVERSATION_RULES,
        THREE_MOMENTS,
        `This pod is called ${podName}.`,
    ];

    // Only a name the user typed says anything. A first pod is named for the
    // person by `firstPodName`, so "Kapeed Pod" is not a brief — telling the
    // assistant to build something fitting it would send it after a name.
    if (!isFirstPod && namedWithIntent(podName)) {
        parts.push(
            `They named it "${podName}" before it contained anything, so it is the closest thing to a brief you have — let it shape what you propose, but still ask before you build.`,
        );
    }

    if (workDomain) {
        parts.push(
            `Their work email is at ${workDomain} — a reasonable place to start working out what they are likely to need.`,
        );
    }

    return parts.join("\n\n");
}

/** Pod home is a launcher; a pod that was just created belongs in a conversation. */
export function buildNewPodConversationHref({
    podId,
    podName,
    workDomain,
    isFirstPod,
    openingMessage,
    extraInstructions,
    metadata,
}: {
    podId: string;
    podName: string;
    workDomain?: string | null;
    isFirstPod: boolean;
    /**
     * What opens the conversation, when the user has already said it.
     *
     * Someone who picked a starting point has stated an intent, and sending it
     * beats "Hi" — the greeting only exists because a pod created from nothing
     * has nothing better to send.
     */
    openingMessage?: string;
    /** What that starting point asks for, on top of the new-pod framing. */
    extraInstructions?: string;
    /** Merged over the defaults, for callers that know more about the origin. */
    metadata?: Record<string, unknown>;
}): string {
    const instructions = [
        buildNewPodInstructions({ podName, workDomain, isFirstPod }),
        extraInstructions,
    ]
        .filter(Boolean)
        .join("\n\n");

    const params = new URLSearchParams();
    params.set("assistantMessage", openingMessage?.trim() || NEW_POD_OPENING_MESSAGE);
    params.set(CONVERSATION_INSTRUCTIONS_PARAM, instructions);
    params.set(
        CONVERSATION_METADATA_PARAM,
        JSON.stringify({
            source: isFirstPod ? "onboarding" : "create_pod",
            first_run: isFirstPod,
            pod_id: podId,
            ...metadata,
        }),
    );

    return `/pod/${encodeURIComponent(podId)}/conversations/new?${params.toString()}`;
}
