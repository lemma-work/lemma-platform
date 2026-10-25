import type { SettingsSection } from "@/settings/settings-modal";

/** The parts of a teammate's About page, each its own page under it:
 *  `/t/{pod}/about/{section}`. Kept apart from the page so the address
 *  grammar can read them without importing a component. */
export type AboutSection = "people" | "channels" | "works-with" | "skills" | "memory" | "schedules" | "agents" | "model" | "ai-tools" | "connections";

export const ABOUT_SECTIONS: readonly AboutSection[] = [
    "people", "channels", "works-with", "skills", "memory", "schedules", "agents", "model", "ai-tools", "connections",
];

/** Whether a word from an address is one of About's sections. */
export function isAboutSection(value: string | null | undefined): value is AboutSection {
    return typeof value === "string" && (ABOUT_SECTIONS as readonly string[]).includes(value);
}

/** Sections that were renamed, and what they are called now. A link to the
 *  old name opens the new part, and the shell writes the new name back.
 *  `connectors` was the organization's connector list shown here; the part
 *  is the pod's own connections now. */
const RENAMED: Record<string, AboutSection> = { connectors: "connections" };

/** A section word from an address, with renamed ones read as their new name. */
export function aboutSectionOf(value: string | null | undefined): AboutSection | null {
    if (isAboutSection(value)) return value;
    return typeof value === "string" && Object.hasOwn(RENAMED, value) ? RENAMED[value] : null;
}

/** Sections About carried for a while that belong to the organization, and
 *  where Settings keeps them. A link that still names one opens Settings
 *  there rather than landing on nothing. Connectors stayed: what a teammate
 *  can reach is the first thing people look for on its page. */
const MOVED_TO_SETTINGS: Record<string, SettingsSection> = {
    models: "models",
    usage: "usage",
    "org-usage": "org-usage",
};

export function settingsSectionOf(value: string | null | undefined): SettingsSection | null {
    return typeof value === "string" && Object.hasOwn(MOVED_TO_SETTINGS, value) ? MOVED_TO_SETTINGS[value] : null;
}
