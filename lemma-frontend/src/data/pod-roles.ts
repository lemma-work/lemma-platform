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

/** The action the server checks before a pod is deleted. A pod admin holds
 *  it, and so does the organization's owner without being a member at all —
 *  which is why this is asked of the server rather than read off a role. */
export const DELETE_POD = "pod.delete";

/** The actions that let somebody see a pod's contacts and their chats:
 *  reading them, or writing to them, which implies it. Editors and admins
 *  hold both; a member who only uses the pod holds neither. */
export const SEE_CONTACTS = ["contact.read", "contact.message"] as const;

/** The actions over a pod's channels, weakest first; each implies the ones
 *  before it. Viewers read, users write and keep the groups they make,
 *  editors and admins manage any group. Who holds which is the server's
 *  (`podPermissions.me`); a channel's own `me.can_manage` already answers
 *  for that channel. */
export const CHANNEL_ACTIONS = { read: "channel.read", write: "channel.write", manage: "channel.manage" } as const;

/** A member's roles, said as the roster says them. A custom role this build
 *  does not know is still a role, so it is shown tidied rather than dropped. */
/** The one role a membership is held at, as the wire spells it, for a
 *  picker that shows and sets a single role: the widest built-in role held,
 *  or a lone custom one.
 *
 *  Null when one role cannot stand for the membership — none held, or a
 *  custom role alongside others. Setting a single role there would quietly
 *  drop the ones the picker never showed, so no picker is offered. */
export function heldPodRole(roles: readonly string[] | null | undefined): string | null {
    const held = roles ?? [];
    const builtIn = new Set<string>(POD_ROLES.map((entry) => entry.value));
    if (held.length > 1 && held.some((role) => !builtIn.has(role))) return null;
    return POD_ROLES.find((entry) => held.includes(entry.value))?.value ?? held[0] ?? null;
}

export function readPodRoles(roles: readonly string[] | null | undefined): { role: string; can: string } {
    const held = roles ?? [];
    const known = POD_ROLES.find((entry) => held.includes(entry.value));
    if (known) return { role: known.role, can: known.can };
    const custom = held[0]?.replace(/^POD_/, "").replaceAll("_", " ").toLowerCase();
    return custom ? { role: custom.charAt(0).toUpperCase() + custom.slice(1), can: "—" } : { role: "Member", can: "—" };
}
