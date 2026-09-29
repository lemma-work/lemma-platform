/** A pod's built-in roles, strongest first.
 *
 *  The wire names are the backend's `PodRole`. A member can hold several, and
 *  what they can do is the strongest of them, so the order is load-bearing:
 *  `readPodRoles` takes the first one a member holds.
 *
 *  `label` is the picker's word for granting it, `role` is what the roster
 *  calls somebody who has it, and `can` says what that means. */
export const POD_ROLES = [
    { value: "POD_ADMIN", label: "Admin", role: "Admin", can: "Can edit and add people" },
    { value: "POD_EDITOR", label: "Can edit", role: "Editor", can: "Can edit" },
    { value: "POD_USER", label: "Can use", role: "User", can: "Can use" },
    { value: "POD_VIEWER", label: "Can read", role: "Viewer", can: "Can read" },
] as const;

/** The action the server checks before anybody is added, re-roled or removed.
 *  Asked of `podPermissions.me` rather than inferred from a role: the server
 *  decides who holds it, and this app only stops offering what it will refuse. */
export const MANAGE_MEMBERS = "pod.member.manage";

/** A member's roles, said as the roster says them. A custom role this build
 *  does not know is still a role, so it is shown tidied rather than dropped. */
export function readPodRoles(roles: readonly string[] | null | undefined): { role: string; can: string } {
    const held = roles ?? [];
    const known = POD_ROLES.find((entry) => held.includes(entry.value));
    if (known) return { role: known.role, can: known.can };
    const custom = held[0]?.replace(/^POD_/, "").replaceAll("_", " ").toLowerCase();
    return custom ? { role: custom.charAt(0).toUpperCase() + custom.slice(1), can: "—" } : { role: "Member", can: "—" };
}
