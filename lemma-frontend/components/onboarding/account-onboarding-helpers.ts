import {
  Code2,
  Layers3,
  Sparkles,
  UserRound,
  UsersRound,
} from "lucide-react";

import { HarnessKind } from "lemma-sdk";

import {
  buildKitAssistantOpeningMessage,
  type KitDefinition,
} from "@/lib/kits/catalog";
import { getRecipeById, type Recipe } from "@/lib/recipes/recipes";
import type { CustomProviderKind } from "@/components/agents/agent-runtime-helpers";

export type SetupStep =
  | "boot"
  | "identity"
  | "audience"
  | "workspace"
  | "connect"
  | "start";
export type BuildPath = "ai" | "template" | "code";

// Who the user is setting this up for. Drives how much workspace setup we do
// up front (solo users never see the org/approvals step) and which starting
// points we surface.
export type Audience = "personal" | "team";

export const SETUP_STEPS: SetupStep[] = [
  "boot",
  "identity",
  "audience",
  "workspace",
  "connect",
  "start",
];

// Solo users skip the workspace step entirely — their workspace is created
// silently when the first pod lands. ProgressDots uses this so the dots match
// the path the user is actually on.
export function setupStepsForAudience(audience: Audience | null): SetupStep[] {
  if (audience === "personal") {
    return ["boot", "identity", "audience", "connect", "start"];
  }
  return SETUP_STEPS;
}

// The AI connection a user picks during onboarding. "lemma" keeps the built-in
// system profile; "daemon" saves a discovered local harness; "provider" stores
// a pasted API key against an OpenAI- or Anthropic-compatible route.
export type ConnectChoice =
  | { kind: "lemma" }
  | {
      kind: "daemon";
      daemonId: string;
      harnessKind: HarnessKind;
      displayName: string;
      modelName?: string | null;
    }
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
  "meal-log-bot",
  "expense-logger-bot",
  "ask-my-data-bot",
  "personal-crm",
  "reading-digest",
  "habit-tracker",
];

const TEAM_START_RECIPE_IDS = [
  "support-triage",
  "approvals-queue",
  "slack-standup-bot",
  "email-intake-bot",
  "lightweight-crm",
  "renewal-review",
];

export function startRecipesForAudience(audience: Audience): Recipe[] {
  const ids =
    audience === "personal" ? PERSONAL_START_RECIPE_IDS : TEAM_START_RECIPE_IDS;
  return ids
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

export function splitName(value: string) {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  return {
    firstName: parts[0] || "",
    lastName: parts.slice(1).join(" "),
  };
}

export function defaultWorkspaceName(name: string) {
  const firstName = splitName(name).firstName || "My";
  return `${firstName}'s Workspace`;
}

// Solo users never name a workspace — we create one quietly so their pod has a
// home. Keep it personal-sounding, not org-sounding.
export function personalWorkspaceName(name: string) {
  const firstName = splitName(name).firstName || "My";
  return `${firstName}'s Space`;
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
