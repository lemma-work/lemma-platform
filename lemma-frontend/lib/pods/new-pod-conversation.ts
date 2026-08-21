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
    "Nothing here is obvious to them. They have not seen a pod, a surface, or an agent before, so name a thing and say what it is for before you offer it — one clause, not a paragraph. Never hand them a link, a QR code, or a setup step they did not agree to, and never run a command whose result they did not ask for.",
    "Propose before you build. They have said almost nothing, so anything you make now is a guess — say what you would build and why, in a sentence or two, and wait for them to agree or redirect you. Do not create tables, apps, agents or workflows before they have said yes to something. Handing someone a schema they never asked for is not momentum; it is a mess they now have to clean up.",
    "Sound like a person who built this, not a product tour. No feature lists, no \"I'm excited to help you get started\", no exclamation marks doing the work of a sentence. Warm, plain, specific, a little dry.",
    "The pod is empty. Do not inspect it, do not go looking for existing resources, and do not create another pod: you are already inside theirs.",
    "Use widgets generously — but not to say hello. `display_resource` with type WIDGET renders live HTML inline in this conversation, and it is the best demonstration of this platform there is: a QR code, a set of choices, a status panel, a preview of what you are building. Load the `lemma-widget` skill before your first one. Anything you are *showing* belongs in a widget rather than a paragraph; anything you are *saying* belongs in two plain sentences.",
].join("\n\n");

/**
 * The first turn: a welcome, an orientation, and one question.
 *
 * What this replaces: a script whose step ONE was "put this pod on their
 * phone", which the assistant read as an instruction to run `telegram-setup`
 * and hand back a QR code. Its first act was configuration, aimed at someone
 * who had not yet been told what a pod is or why a bot would help. Everything
 * worth reaching is still reached — it is offered first, in a sentence, and
 * waits for a yes.
 */
const WELCOME_TURN = [
    "Your first turn is a welcome and one offer. Nothing else goes in it.",
    "Say hello like a person, then say what this place actually is, in two or three short lines and your own words: a pod is where the work lives — you build apps and agents in it, the agents go where the conversation already happens (Telegram, Slack, email), and they keep running on a schedule or a trigger when nobody has a tab open. Concrete nouns, no bullet list, no tour.",
    "Then make exactly one offer and stop: would they like you on Telegram too, so this pod is on their phone and they can talk to you there. Give the reason in one clause — a bot cannot start a conversation, so once they have messaged it even once, this pod can message *them*, unprompted, from then on. That is what turns it into a colleague rather than a website they visit.",
    "Do not run a command, create a resource, or show a QR code in this turn. You are asking, not setting up. Then stop and let them answer.",
].join("\n\n");

/**
 * What each answer to that offer means. The "no" branch is the point: an offer
 * that gets asked twice was never an offer.
 */
const TELEGRAM_STEP = [
    "If they say yes to Telegram, set it up now. Load the `lemma-builder` skill for how surfaces work, then run `lemma surfaces telegram-setup` in the workspace. Pass no `--agent`: the bot then answers as the pod's own assistant, which needs no agent to exist first. It returns a `launch_url` — render that as a QR code in a widget and tell them to scan it and say hello to the bot.",
    "Then stop. End your turn with nothing else in it — no questions, no options, no preview of what comes next. They are on their phone now and cannot answer you anyway. Wait for them to come back and say something, anything. While you wait you may quietly look into what they are likely to need, but do not report it yet.",
    "When they come back, check whether the bot actually came up with `lemma surfaces telegram-setup-status <setup_id>` before you say it worked, and say so briefly either way. If it never completed, offer the link again rather than letting it quietly go missing.",
    "If they say no, or answer with something else entirely, let it go and follow what they said. Telegram is an offer, not a step: never ask a second time in a row, and only raise it again once something exists that would actually be worth being pinged about.",
].join("\n\n");

/**
 * Where the conversation goes once the first question is behind it. Guidance,
 * not a checklist — the order is what makes each part land, and naming them as
 * steps out loud is what makes it read as onboarding.
 */
const WHAT_COMES_AFTER = [
    "After that, two more things are worth reaching, in this order, each making the next worth more. Take them one at a time and let each finish before starting the next. Do not describe the plan up front and do not present them as steps to get through.",
    "Build them something real. Offer a small set of concrete things you could build, as a widget of choices rather than a paragraph of suggestions. When they pick one, build it properly: real tables, believable sample data, an agent doing the work behind it. It should be alive the moment it opens, not a shell they have to fill in. Keep narrating in short turns as you go.",
    "Then make it theirs together. Once something exists that is worth showing, and not before, ask whether a teammate should have it. An invitation lands that person directly in the app you just built, able to use it and its agents immediately.",
    "Throughout: short turns, plain language, no walls of text. Ask at most one question at a time, and only when you truly cannot proceed without the answer. Never make them configure something before they have seen something work.",
].join("\n\n");

/**
 * When the user arrived having already said what they want.
 *
 * A start path sends a real sentence, so welcoming them and asking about
 * Telegram would be answering a question they did not ask. They still get the
 * pacing, the proposing, and the offer — just later, and out of the way of the
 * thing they came here to do.
 */
const STATED_INTENT_OPENING = [
    "They opened this pod by saying what they want, and that message is the brief. Do not open with a welcome, an orientation, or a question about Telegram — acknowledge what they asked for in one line and get to work on it.",
    "Everything below still holds: propose before you build, one thing per turn, widgets over paragraphs. Bring up putting this pod on their phone later, once something exists that would be worth being pinged about, and offer it rather than setting it up.",
].join("\n\n");

/** Only for someone who has never seen Lemma before. */
const FIRST_RUN_WELCOME =
    "You are opening the first conversation this person has ever had in Lemma. Their pod was created for them automatically and named after them: they have chosen nothing, configured nothing, and told you nothing. They have said only hello, and they do not yet know what Lemma is, what a pod is, or what you can do. Your welcome is the only explanation they are going to get, so make it a good one — warm, genuine, personal, never corporate.";

/** For someone who already uses Lemma and has just made another pod. */
const RETURNING_WELCOME =
    "They already use Lemma and have just made themselves another pod, so skip the explanation of what this place is and keep the welcome to one short line before the offer. Everything below still applies: this pod is as empty as their first one was, it needs its own Telegram surface like the first one did, and they have told you no more about it than its name.";

export function buildNewPodInstructions({
    podName,
    workDomain,
    isFirstPod,
    statedIntent = false,
}: {
    podName: string;
    /** The company behind their address, when it is a work one. */
    workDomain?: string | null;
    /** Their first pod ever, rather than another one in a workspace they know. */
    isFirstPod: boolean;
    /** They arrived having already said what they want, so skip the welcome. */
    statedIntent?: boolean;
}): string {
    const parts: string[] = statedIntent
        ? [STATED_INTENT_OPENING, CONVERSATION_RULES, WHAT_COMES_AFTER]
        : [
              isFirstPod ? FIRST_RUN_WELCOME : RETURNING_WELCOME,
              CONVERSATION_RULES,
              WELCOME_TURN,
              TELEGRAM_STEP,
              WHAT_COMES_AFTER,
          ];

    parts.push(`This pod is called ${podName}.`);

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
     * has nothing better to send. It also replaces the welcome turn: they asked
     * a question, so the answer is the first thing they should get.
     */
    openingMessage?: string;
    /** What that starting point asks for, on top of the new-pod framing. */
    extraInstructions?: string;
    /** Merged over the defaults, for callers that know more about the origin. */
    metadata?: Record<string, unknown>;
}): string {
    const statedIntent = Boolean(openingMessage?.trim());
    const instructions = [
        buildNewPodInstructions({ podName, workDomain, isFirstPod, statedIntent }),
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
