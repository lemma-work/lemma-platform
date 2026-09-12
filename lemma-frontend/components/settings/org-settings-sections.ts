/**
 * The sections an organization's settings are divided into.
 *
 * One list, so the sidebar and any other consumer cannot disagree about what
 * an organization has. Billing is absent here on purpose: it only exists on a
 * deployment that has the cloud billing module, so the sidebar appends it from
 * the capability probe rather than the list pretending it is always there.
 */
export const ORG_SETTINGS_SECTIONS = [
    { segment: "members", label: "Members", icon: "users" },
    { segment: "usage", label: "Usage", icon: "chart" },
    { segment: "agent-runtimes", label: "Models", icon: "sliders" },
    { segment: "pods", label: "Pods", icon: "boxes" },
] as const;

export type OrgSettingsSection = (typeof ORG_SETTINGS_SECTIONS)[number];

export function orgSettingsHref(organizationId: string, segment: string): string {
    return `/organizations/${organizationId}/settings/${segment}`;
}
