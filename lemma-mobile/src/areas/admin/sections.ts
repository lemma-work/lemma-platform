/** Where every setting lives, by who owns it: you, the organization, or one
 *  Lemma. The web keeps the first two in its settings window
 *  (`settings/settings-modal.tsx`) and the third on a Lemma's About page
 *  (`space/about-page.tsx`); the phone lists the first two on the account
 *  screen and the third on a Lemma's Settings tab, a screen each. */

export type SettingsSectionId = "profile" | "usage" | "plan" | "people" | "connectors" | "models" | "org-usage" | "team-billing";

export interface SettingsEntry {
    id: SettingsSectionId;
    label: string;
    /** The screen's heading. */
    title: string;
    /** The line under the heading, and under the row on the account screen. */
    hint: string;
    group: "you" | "organization";
    /** About paying, which a local installation has none of: hidden there,
     *  as on the web, since an empty Plan reads as broken billing. */
    billing?: boolean;
}

/** The account's and the organization's settings, in the web's order. */
export const SETTINGS: readonly SettingsEntry[] = [
    { id: "profile", label: "Profile", title: "Profile", hint: "Your name, time zone and mobile number", group: "you" },
    { id: "usage", label: "Usage", title: "Your usage", hint: "What you have spent, and what you may spend", group: "you" },
    { id: "plan", label: "Plan", title: "Your plan", hint: "What you pay for", group: "you", billing: true },
    { id: "people", label: "People", title: "People", hint: "Who is in this organization", group: "organization" },
    { id: "connectors", label: "Connectors", title: "Connectors", hint: "Accounts this organization has connected", group: "organization" },
    { id: "models", label: "Models", title: "Models", hint: "Models, provider keys and coding agents", group: "organization" },
    { id: "org-usage", label: "Usage", title: "Organization usage", hint: "Usage across this organization", group: "organization" },
    { id: "team-billing", label: "Billing", title: "Billing", hint: "What this organization pays for, and its seats", group: "organization", billing: true },
];

export function settingsEntry(id: string | undefined): SettingsEntry | null {
    return SETTINGS.find((one) => one.id === id) ?? null;
}

/** The settings a deployment offers: no billing on a local one. */
export function offeredSettings(deployment: string | null): readonly SettingsEntry[] {
    return deployment === "local" ? SETTINGS.filter((one) => !one.billing) : SETTINGS;
}

export type LemmaPartId = "about" | "model" | "skills" | "memory" | "connectors" | "channels" | "mcp" | "people" | "works-with";

export interface LemmaPart {
    id: LemmaPartId;
    label: string;
    /** The part's heading on its own screen, when the label alone says less. */
    title: (name: string) => string;
    /** The line under the heading. */
    note: (name: string) => string;
    /** What the row says when its live line cannot be read. */
    blurb: string;
}

/** A Lemma's parts, in the order its Settings tab lists them. The web's
 *  About tiles, less the two that are the Work area's (agents and standing
 *  work, on the Runs tab), and with "Teammates" said as "Lemmas". */
export const LEMMA_PARTS: readonly LemmaPart[] = [
    { id: "model", label: "Default model", title: () => "Default model",
        note: (name) => `What ${name} thinks with, unless an agent names its own.`, blurb: "The model it thinks with" },
    { id: "skills", label: "Skills", title: () => "Skills",
        note: (name) => `Instructions ${name} follows when a task matches. People write these.`, blurb: "Instructions people wrote" },
    { id: "memory", label: "Memory", title: () => "Memory",
        note: (name) => `Notes ${name} writes as it works. Open one to read or fix it.`, blurb: "Notes it wrote itself" },
    { id: "connectors", label: "Connectors", title: () => "Connectors",
        note: (name) => `Accounts ${name} can use. They belong to the organization, so every Lemma in it shares them.`, blurb: "Accounts it can use" },
    { id: "channels", label: "Where people reach it", title: (name) => `Where to reach ${name}`,
        note: (name) => `Pick a place and ${name} answers there, under its own name: WhatsApp, Telegram, email, Slack, Teams, Discord.`, blurb: "Only here, in Lemma" },
    { id: "mcp", label: "MCP", title: () => "Connect ChatGPT, Claude and others",
        note: (name) => `Add ${name} to any AI app that speaks MCP, and use it from there.`, blurb: "Use it from other AI apps" },
    { id: "people", label: "People", title: () => "People",
        note: (name) => `Everyone here can open what is in ${name}.`, blurb: "Who can open it" },
    { id: "works-with", label: "Other Lemmas", title: (name) => `Lemmas ${name} works with`,
        note: (name) => `As you: ${name} asks with your access there, and checks with you first. Allowed: that Lemma lets ${name} ask it with nobody present, reading only what it shared.`,
        blurb: "Other Lemmas it can ask" },
];

export function lemmaPart(id: string | undefined): LemmaPart | null {
    return LEMMA_PARTS.find((one) => one.id === id) ?? null;
}
