import {
  BadgeDollarSign,
  Headphones,
  Handshake,
  Code2,
  Lightbulb,
  Layers3,
  Cog,
  Sparkles,
  UserRound,
  UserPlus,
  UsersRound,
} from "@/components/ui/icons";

import {
  buildKitAssistantOpeningMessage,
  type KitDefinition,
} from "@/lib/kits/catalog";
import { getRecipeById, recipeCatalog, type Recipe } from "@/lib/recipes/recipes";
import type { CustomProviderKind } from "@/components/agents/agent-runtime-helpers";

export type SetupStep =
  | "boot"
  | "identity"
  | "audience"
  | "team"
  | "workspace"
  | "connect"
  // Local-only. A Lemma Desktop installation has to answer two questions the
  // hosted flow never asks: what should answer in chats, and who can reach it.
  | "intelligence"
  | "sharing"
  | "start";
export type BuildPath = "ai" | "template" | "code";
export type OnboardingStartPath =
  | "telegram"
  | "chatgpt"
  | "internal-app"
  | "agent-skin"
  | "coding-agents"
  | "templates"
  // An empty pod, and nothing else. The way out of every guided path, for
  // anyone who already knows what they are doing.
  | "blank";
/** A start path that seeds the pod's own composer rather than a form. */
export type ComposerStartPath = Extract<
  OnboardingStartPath,
  "telegram" | "chatgpt" | "internal-app" | "agent-skin"
>;
export type CodingAgentKind = "codex" | "claude-code" | "opencode";

export function codingAgentStarterPrompt(agent: CodingAgentKind) {
  const agentName =
    agent === "claude-code"
      ? "Claude Code"
      : agent === "opencode"
        ? "OpenCode"
        : "Codex";

  return `Use Lemma as the durable workspace for this project.

First inspect this repository and explain, in one short paragraph, the most useful Lemma pod this codebase could become. Then:

1. Check whether the Lemma CLI and Lemma builder skill are available. If either is missing, show me the exact setup step and wait.
2. Create or select one pod for this project. Do not create duplicates.
3. Model the durable state, files, functions, agents, workflows, schedules, and app surfaces the product actually needs.
4. Build the smallest complete working path, seed believable data, and verify it end to end.
5. Keep the repository as the codebase and Lemma as its durable runtime and workspace. Finish by telling me what changed and how to use it.

Ask only one question if the repository does not make the intended outcome clear. Work through the ${agentName} session you are already running in.`;
}

/**
 * What a start path hands to the pod, now that it no longer collects a brief.
 *
 * A path used to be a form: pick a card, fill a textarea, and we baked the
 * answer into one long prompt and sent it. That textarea was a second, worse
 * composer — no attachments, no model picker, no agent scope, and disabled
 * until you typed. So a path now produces two things instead:
 *
 * - `stem`, an unfinished sentence the real composer opens with, cursor at the
 *   end. The user finishes it where they can also attach a file or change model.
 * - `instructions`, the durable framing that used to surround the brief. It
 *   rides along as the conversation's background instructions, so it shapes the
 *   build without being something the user has to read or write.
 * - `podName`, because picking a path already says what the pod is. Falling back
 *   to "Untitled pod" after someone pressed "Telegram agent" throws away the one
 *   thing they just told us. Only used when they did not type a name themselves.
 *
 * Nothing is sent until they press enter.
 */
export function startPathComposerLaunch(path: ComposerStartPath): {
  intent: string;
  podName: string;
  stem: string;
  instructions: string;
} {
  if (path === "telegram") {
    return {
      intent: "telegram_agent_companion_app",
      podName: "Telegram Agent",
      stem: "Build a Telegram agent and companion app that ",
      instructions: [
        "Treat the user's message as the agent's custom operating instructions.",
        "The companion app should turn the agent's messages and actions into a useful, persistent view.",
        "Create the smallest working version, with believable sample data. Then guide me through connecting Telegram. Do not claim Telegram is connected until the connector is actually authorized.",
      ].join("\n\n"),
    };
  }

  if (path === "chatgpt") {
    return {
      intent: "external_ai_pod_mcp",
      podName: "ChatGPT Work State",
      stem: "Keep this work state current so ChatGPT or Claude can continue it: ",
      instructions: [
        "Set up this pod so ChatGPT or Claude can keep real work state updated through the pod-scoped Lemma MCP surface.",
        "Create the durable tables, files, and views needed for that state, seed a useful first version, and preserve clear ownership and history.",
        "Then guide me through connecting my external AI client to this pod's MCP surface. Do not pretend the external connection is complete before it is authorized and tested.",
      ].join("\n\n"),
    };
  }

  if (path === "agent-skin") {
    return {
      intent: "local_agent_workspace_skin",
      podName: "Coding Agent Pod",
      stem: "Put a persistent workspace around my coding agent showing ",
      instructions: [
        "Build a persistent Lemma workspace skin around the user's local coding agent. Ask which one they run — Codex, Claude Code, or OpenCode — if they have not said.",
        "Give people a clear place to inspect state, steer the work, and revisit outputs without reading a terminal transcript.",
        "Keep the local coding agent as the executor. Build the durable state, app surfaces, run history, and artifacts around it in Lemma, using the Agent Host and MCP boundary where supported.",
        "Then guide me through connecting that agent. Do not claim the local agent is connected until its runtime is available, authorized, and tested.",
      ].join("\n\n"),
    };
  }

  return {
    intent: "internal_ai_app",
    podName: "Internal App",
    stem: "Build an internal app that lets my team ",
    instructions: [
      "Build an internal AI app inside this pod.",
      "Keep the first version focused on one clear user and one repeated decision or workflow.",
      "Put the app in front and the agents behind it. Create the durable state, approval points, and believable sample data needed to make the first version immediately usable.",
    ].join("\n\n"),
  };
}

// Who the user is setting this up for. Drives how much workspace setup we do
// up front (solo users never see the org/approvals step) and which starting
// points we surface.
export type Audience = "personal" | "team";

export const SETUP_STEPS: SetupStep[] = [
  "boot",
  "identity",
  "audience",
  "workspace",
  "team",
  "connect",
  "start",
];

export function resolveOnboardingStartStep(
  storedStep: SetupStep | null | undefined,
  needsProfile: boolean,
): SetupStep {
  if (
    needsProfile ||
    !storedStep ||
    storedStep === "boot" ||
    storedStep === "identity"
  ) {
    return "identity";
  }

  // Older drafts from the longer team-first flow resume at first value.
  return "start";
}

export function previousOnboardingStep(
  steps: SetupStep[],
  currentStep: SetupStep,
): SetupStep | null {
  const currentIndex = steps.indexOf(currentStep);
  if (currentIndex <= 0) return null;

  const previousStep = steps[currentIndex - 1];
  return previousStep === "boot" ? null : previousStep;
}

// A local installation's own setup, regardless of audience: the questions this
// machine cannot answer for itself, and nothing else.
//
// The hosted flow can defer AI configuration because hosted Lemma has models of
// its own; a local one has none until someone points it at something, so
// `intelligence` is the step that decides whether agents work at all. It covers
// both answers to that question — a coding agent already on this Mac, or an API
// provider — in one place, because they are one decision and splitting them
// meant sending the user through two screens and a second window to make it.
//
// `identity` and `start` are deliberately absent. A hosted signup is not asked
// for either one — the name comes from the address and the first pod is created
// for them — and a local signup knows exactly as much. Keeping those two here
// meant the one deployment that provisions nothing on its own was also the one
// that made the user do it by hand: an organization only existed once a name
// was typed, and a pod only once a starting point was picked. Both are now
// created around these steps rather than by them, which leaves this list as the
// difference between local and hosted rather than a longer version of it.
export const LOCAL_SETUP_STEPS: SetupStep[] = [
  "intelligence",
  "sharing",
];

// Solo users skip the workspace step entirely — their workspace is created
// silently when the first pod lands. SetupProgressBar uses this so its fill
// matches the path the user is actually on.
export function setupStepsForAudience(
  audience: Audience | null,
  isLocal = false,
): SetupStep[] {
  if (isLocal) {
    return LOCAL_SETUP_STEPS;
  }
  if (audience === "personal") {
    return ["identity", "start"];
  }
  if (audience === "team") {
    return [
      "boot",
      "identity",
      "audience",
      "workspace",
      "team",
      "connect",
      "start",
    ];
  }
  return SETUP_STEPS;
}

export function nextTeamSetupStep({
  hasOrganization,
  hasPod,
}: {
  hasOrganization: boolean;
  hasPod: boolean;
}): Extract<SetupStep, "workspace" | "team" | "connect"> {
  if (hasPod) return "connect";
  return hasOrganization ? "team" : "workspace";
}

export function normalizeOnboardingStep(
  step: SetupStep,
  audience: Audience | null,
  hasOrganization: boolean,
  isLocal = false,
): SetupStep {
  if (isLocal) {
    // A draft written before this install grew its own steps, or by the same
    // account against hosted Lemma, can name a step this flow does not have.
    // Resuming on one would strand the user on a screen with no way forward, so
    // it resumes at the first local question instead — the organization behind
    // it is provisioned either way.
    return LOCAL_SETUP_STEPS.includes(step) ? step : LOCAL_SETUP_STEPS[0];
  }
  // Drafts created by the old team-first flow may resume on the team step
  // before an organization exists. Send those users through workspace setup
  // instead of letting the pod CTA lead to another unrelated step.
  if (audience === "team" && step === "team" && !hasOrganization) {
    return "workspace";
  }
  return step;
}

export type TeamKind =
  | "sales"
  | "support"
  | "operations"
  | "recruiting"
  | "customer-success"
  | "product"
  | "finance"
  | "other";

export const TEAM_OPTIONS: Array<{
  id: TeamKind;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    id: "sales",
    title: "Sales",
    description: "Accounts, pipeline, follow-ups, and handoffs.",
    icon: Handshake,
  },
  {
    id: "support",
    title: "Support",
    description: "Shared inboxes, triage, and replies your team approves.",
    icon: Headphones,
  },
  {
    id: "operations",
    title: "Operations",
    description: "Approvals, queues, recurring work, and internal requests.",
    icon: Cog,
  },
  {
    id: "recruiting",
    title: "Recruiting",
    description: "Candidates, outreach, interviews, and hiring loops.",
    icon: UserPlus,
  },
  {
    id: "customer-success",
    title: "Customer Success",
    description: "Renewals, health signals, account notes, and escalations.",
    icon: UsersRound,
  },
  {
    id: "product",
    title: "Product",
    description: "Feedback, research, specs, and launch coordination.",
    icon: Lightbulb,
  },
  {
    id: "finance",
    title: "Finance",
    description: "Invoices, approvals, spend, and reporting.",
    icon: BadgeDollarSign,
  },
  {
    id: "other",
    title: "Something else",
    description: "Name the team yourself.",
    icon: Code2,
  },
];

export function teamLabelForKind(kind: TeamKind | null, customName = "") {
  if (kind === "other") return toTitleCase(customName.trim() || "Team");
  const option = TEAM_OPTIONS.find((item) => item.id === kind);
  return option?.title || "Team";
}

// The AI connection a user picks during onboarding. "lemma" keeps the built-in
// system profile; "provider" stores a pasted API key against an OpenAI- or
// Anthropic-compatible route. Local coding agents are not offered here: they
// belong to a paired computer, which is set up from Models settings once the
// workspace exists.
export type ConnectChoice =
  | { kind: "lemma" }
  | {
      kind: "provider";
      providerKind: CustomProviderKind;
      name: string;
      baseUrl: string;
      apiKey: string;
      modelNames: string[];
      defaultModelName?: string;
    };

export const AUDIENCE_OPTIONS: Array<{
  id: Audience;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    id: "personal",
    title: "Just me",
    description:
      "A personal space for your own work — a tracker, a CRM, a weekly digest. No team setup to do.",
    icon: UserRound,
  },
  {
    id: "team",
    title: "My team",
    description:
      "A shared workspace with teammates, approvals, and bots your team works in.",
    icon: UsersRound,
  },
];

// Curated starting points per audience, drawn from the recipes catalog. These
// are concrete outcomes ("Personal CRM") rather than build strategies, so a
// first-timer picks a result instead of a method.
const PERSONAL_START_RECIPE_IDS = [
  "telegram-personal-logger",
  "dashboard-internal-tool",
  "knowledge-workspace",
  "monitor-alert",
];

const TEAM_START_RECIPE_IDS = [
  "crm-pipeline-app",
  "email-support-desk",
  "slack-knowledge-teammate",
  "email-agent",
  "inbox-review-queue",
  "approval-review",
];

export function startRecipesForAudience(audience: Audience): Recipe[] {
  const ids =
    audience === "personal" ? PERSONAL_START_RECIPE_IDS : TEAM_START_RECIPE_IDS;
  const recipeIds = [
    ...ids,
    ...recipeCatalog
      .filter((recipe) => recipe.source.kind === "repo")
      .map((recipe) => recipe.id),
  ];
  return recipeIds
    .map((id) => getRecipeById(id))
    .filter((recipe): recipe is Recipe => Boolean(recipe));
}

export const SETUP_GREETINGS = [
  {
    text: "Hello",
    lang: "en",
    className: "setup-greeting-delay-0",
    skyline: "/onboarding/intro-skylines/usskyline.png",
    skylineClassName: "setup-skyline-delay-0",
  },
  {
    text: "नमस्ते!",
    lang: "hi",
    className: "setup-greeting-delay-1",
    skyline: "/onboarding/intro-skylines/indiaskyline.png",
    skylineClassName: "setup-skyline-delay-1",
  },
  {
    text: "你好",
    lang: "zh",
    className: "setup-greeting-delay-2",
    skyline: "/onboarding/intro-skylines/chinaskyline.png",
    skylineClassName: "setup-skyline-delay-2",
  },
  {
    text: "¡Hola!",
    lang: "es",
    className: "setup-greeting-delay-3",
    skyline: "/onboarding/intro-skylines/spainskyline.png",
    skylineClassName: "setup-skyline-delay-3",
  },
];

export const INTENT_EXAMPLES = [
  "Track investor follow-ups from Gmail and Slack",
  "Explore Lemma capabilities",
  "Run customer support from Gmail",
  "Monitor Meta Ads and weekly performance",
  "Manage candidate outreach",
  "Create a team knowledge app",
];

export const INTENT_EXAMPLE_LABELS: Record<string, string> = {
  "Explore Lemma capabilities": "Explore capabilities",
  "Run customer support from Gmail": "Customer support",
  "Monitor Meta Ads and weekly performance": "Ads reporting",
  "Manage candidate outreach": "Hiring outreach",
  "Create a team knowledge app": "Knowledge app",
};

export const BUILD_PATHS: Array<{
  id: BuildPath;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    id: "ai",
    title: "AI builds the first draft",
    description: "Describe the work. Lemma proposes the pod.",
    icon: Sparkles,
  },
  {
    id: "template",
    title: "Use a template",
    description: "Start from a proven shape.",
    icon: Layers3,
  },
  {
    id: "code",
    title: "Use the SDK",
    description: "Start from code when you know the shape.",
    icon: Code2,
  },
];

export function splitGraphemes(value: string) {
  if (typeof Intl !== "undefined" && "Segmenter" in Intl) {
    const segmenter = new Intl.Segmenter(undefined, {
      granularity: "grapheme",
    });
    return Array.from(segmenter.segment(value), (segment) => segment.segment);
  }

  return Array.from(value);
}

export function buildPromptFromIntent(value: string) {
  const intent = value.trim() || "Set up my first pod";
  return [
    `Use this goal as the starting point: ${intent}.`,
    "Propose the state this pod should track, the agents it needs, the workflows that should move the work forward, and the approval points where I should stay in control.",
  ].join(" ");
}

export function buildKitOnboardingPrompt(kit: KitDefinition) {
  return buildKitAssistantOpeningMessage(kit, "customize");
}

export function buildPodDescription(intent: string, buildPath: BuildPath) {
  const pathCopy =
    buildPath === "code"
      ? "SDK-ready starter"
      : buildPath === "template"
        ? "Template remix starter"
        : "AI-built starter";
  return [intent.trim(), "", `${pathCopy}.`, "Connectors can be connected later."]
    .filter(Boolean)
    .join("\n");
}

export function inferFullName(
  profile?: {
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    full_name?: string | null;
  } | null,
) {
  if (profile?.full_name?.trim()) return profile.full_name.trim();
  const combined = [profile?.first_name, profile?.last_name]
    .filter(Boolean)
    .join(" ")
    .trim();
  if (combined) return combined;
  const localPart = profile?.email?.split("@")[0] || "";
  return localPart
    .split(/[._-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function hasUsableProfileName(
  profile?: {
    first_name?: string | null;
    full_name?: string | null;
  } | null,
) {
  return Boolean(profile?.first_name?.trim() || profile?.full_name?.trim());
}

export function splitName(value: string) {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  return {
    firstName: parts[0] || "",
    lastName: parts.slice(1).join(" "),
  };
}

const ORGANIZATION_NAME_ADJECTIVES = [
  "Amber",
  "Bright",
  "Cedar",
  "Clear",
  "Copper",
  "Golden",
  "Indigo",
  "North",
  "Olive",
  "Open",
  "Quiet",
  "Silver",
  "Wild",
] as const;

const ORGANIZATION_NAME_NOUNS = [
  "Atlas",
  "Bridge",
  "Compass",
  "Field",
  "Forge",
  "Garden",
  "Grove",
  "Harbor",
  "Lantern",
  "Meadow",
  "Orchard",
  "Signal",
  "Studio",
  "Summit",
] as const;

export function generatedOrganizationName(seed: string, attempt = 0) {
  const normalizedSeed = seed.trim().toLowerCase() || "lemma";
  let hash = 2166136261;
  for (const character of normalizedSeed) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }

  const adjective =
    ORGANIZATION_NAME_ADJECTIVES[
      Math.abs(hash + attempt * 17) % ORGANIZATION_NAME_ADJECTIVES.length
    ];
  const noun =
    ORGANIZATION_NAME_NOUNS[
      Math.abs((hash >>> 8) + attempt * 29) % ORGANIZATION_NAME_NOUNS.length
    ];
  return `${adjective} ${noun}`;
}

const COMMON_COUNTRY_CODE_SECOND_LEVEL_DOMAINS = new Set([
  "ac",
  "co",
  "com",
  "edu",
  "gov",
  "net",
  "org",
]);

/** The company a work domain stands for: `research.acme.co.uk` -> `Acme`. */
export function organizationNameFromWorkDomain(domain: string) {
  const labels = domain
    .trim()
    .toLowerCase()
    .replace(/^@+/, "")
    .split(".")
    .filter(Boolean);
  if (labels.length < 2) return null;

  const topLevelDomain = labels.at(-1) || "";
  const secondLevelDomain = labels.at(-2) || "";
  const organizationLabel =
    labels.length >= 3 &&
    topLevelDomain.length === 2 &&
    COMMON_COUNTRY_CODE_SECOND_LEVEL_DOMAINS.has(secondLevelDomain)
      ? labels.at(-3) || ""
      : secondLevelDomain;
  return toTitleCase(organizationLabel.replace(/[-_]+/g, " ")) || null;
}

const COMPANY_NAME_ATTEMPTS = 10;

/** How many names onboarding will try before giving up on creating an org. */
export const MAX_ORGANIZATION_NAME_ATTEMPTS = 20;

const RETRIABLE_ORGANIZATION_CONFLICT_CODES = new Set([
  "ORGANIZATION_NAME_CONFLICT",
  "ORGANIZATION_SLUG_CONFLICT",
]);

/**
 * Whether a failed create can be retried under a different name.
 *
 * A taken name or slug is answered by the next candidate. A taken email domain
 * is not — every candidate claims the same domain — so that one has to surface
 * instead of burning the whole ladder on the same rejection.
 */
export function isRetriableOrganizationNameConflict(error: unknown) {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === "string" &&
    RETRIABLE_ORGANIZATION_CONFLICT_CODES.has(code);
}

/**
 * The name to try for this person's first organization, on the nth attempt.
 *
 * A work address names the company, because that org goes on to claim the
 * domain — every later colleague is auto-joined into it and has to recognise
 * it. A consumer address names nothing real, so an invented pair beats
 * "Gmail" or a surname guess.
 *
 * Names are globally unique, so each rung has to be a name we would still be
 * happy to show: the company, then its domain (unique by definition, so one
 * squatter can't push us off it), then a counter. An exhausted company ladder
 * falls back to an invented name rather than blocking signup.
 */
export function organizationNameCandidate({
  email,
  workDomain,
  attempt = 0,
}: {
  email: string;
  workDomain?: string;
  attempt?: number;
}) {
  const companyName = workDomain
    ? organizationNameFromWorkDomain(workDomain)
    : null;
  if (!companyName) return generatedOrganizationName(email, attempt);

  if (attempt === 0) return companyName;
  if (attempt === 1) return workDomain as string;
  if (attempt < COMPANY_NAME_ATTEMPTS) return `${companyName} ${attempt}`;
  return generatedOrganizationName(email, attempt - COMPANY_NAME_ATTEMPTS);
}

// The server accepts letters, numbers, spaces, hyphens and underscores in a pod
// name and nothing else (`normalize_pod_name`), because pods are addressable by
// name from the CLI. A name derived from a person routinely breaks that —
// apostrophes in "O'Brien", accents in "José" — so it is folded to the allowed
// set here rather than discovered as a rejected create.
const POD_NAME_DISALLOWED = /[^A-Za-z0-9 _-]/g;
const COMBINING_MARKS = /[\u0300-\u036f]/g;

function podNameSafe(value: string): string {
  return value
    .normalize("NFD")
    .replace(COMBINING_MARKS, "")
    .replace(POD_NAME_DISALLOWED, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * What to call the pod an account is given on arrival.
 *
 * "Personal Pod" is nobody's workspace; a name with the person in it is theirs
 * from the first screen. The possessive form this wanted ("Ada's Pod") is not
 * available: the apostrophe is exactly what the server rejects. Falls back to
 * the generic name when nothing usable survives.
 */
export function firstPodName(
  profile?: {
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    full_name?: string | null;
  } | null,
) {
  const { firstName } = splitName(inferFullName(profile));
  const safe = podNameSafe(firstName || "");
  if (!safe) return "Personal Pod";
  return `${safe} Pod`;
}

export function podNameForAudience(audience: Audience, teamName = "") {
  if (audience === "personal") return "Personal Pod";

  const label = toTitleCase(teamName.trim() || "Team");
  return /\bpod$/i.test(label) ? label : `${label} Pod`;
}

export function derivePodNameFromIntent(value: string) {
  const normalized = value.trim();
  const lower = normalized.toLowerCase();

  if (!normalized) return "First Pod";
  if (/knowledge|wiki|docs|document|manual/.test(lower))
    return "Team Knowledge App";
  if (/support|customer|ticket|request|inbox/.test(lower))
    return "Customer Support App";
  if (/investor|founder|fund|follow/.test(lower))
    return "Investor Follow-up Room";
  if (/meta|ads|campaign|performance/.test(lower)) return "Meta Ads Monitor";
  if (/candidate|hiring|recruit/.test(lower)) return "Candidate Outreach Pod";

  const cleaned = normalized
    .replace(/^(create|run|track|manage|monitor|build|make|set up)\s+/i, "")
    .replace(/\s+(from|with|using|in)\s+.*$/i, "")
    .replace(/[^\w\s-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const titled = toTitleCase(cleaned || normalized);
  return /\b(room|app|pod|monitor)\b/i.test(titled) ? titled : `${titled} Pod`;
}

export function toTitleCase(value: string) {
  const smallWords = new Set([
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "the",
    "to",
    "with",
  ]);
  return value
    .split(" ")
    .filter(Boolean)
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (index > 0 && smallWords.has(lower)) return lower;
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}
