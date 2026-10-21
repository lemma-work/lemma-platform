import { CHANNEL_ACTIONS, SEE_CONTACTS } from "@/data/pod-roles";
import type { ChannelKind } from "./model";

/** What the reader may do in a pod's channels, from the actions the server
 *  says they hold (`podPermissions.me`), and so what a channel offers them.
 *
 *  The server decides every one of these again on each request; this only
 *  stops the app offering what it would refuse. `channel.manage` implies
 *  `channel.write`, which implies `channel.read`; `contact.message` implies
 *  `contact.read`. A group's own settings follow the channel's
 *  `me.can_manage`, which already answers for that channel (its owner, or a
 *  holder of `channel.manage`). */

export interface Abilities {
    /** False while the server has not said: nothing is withheld on a guess. */
    known: boolean;
    read: boolean;
    write: boolean;
    manage: boolean;
    contactRead: boolean;
    contactMessage: boolean;
}

export const EVERYTHING: Abilities = { known: true, read: true, write: true, manage: true, contactRead: true, contactMessage: true };
const UNKNOWN: Abilities = { ...EVERYTHING, known: false };

export function readAbilities(actions: readonly string[] | null | undefined): Abilities {
    if (!actions) return UNKNOWN;
    const held = new Set(actions);
    const manage = held.has(CHANNEL_ACTIONS.manage);
    const write = manage || held.has(CHANNEL_ACTIONS.write);
    const contactMessage = held.has(SEE_CONTACTS[1]);
    return {
        known: true,
        read: write || held.has(CHANNEL_ACTIONS.read),
        write,
        manage,
        contactRead: contactMessage || held.has(SEE_CONTACTS[0]),
        contactMessage,
    };
}

export interface ChannelAbilities {
    /** May write lines here. */
    write: boolean;
    /** Said in place of the composer when they may not. */
    writeRefused: string | null;
    /** A group's name, topic, visibility, archive, delete, members, roles, agents. */
    manage: boolean;
    /** A contact's channel: assignee, status, the agent switch, hand-back. */
    contactOps: boolean;
}

export function channelAbilities(
    abilities: Abilities,
    channel: { kind: ChannelKind; me: { canManage: boolean } },
    space: string,
): ChannelAbilities {
    const contact = channel.kind === "CONTACT";
    const write = abilities.write && (!contact || abilities.contactMessage);
    return {
        write,
        writeRefused: write ? null
            : contact ? "Only " + space + "’s editors and admins write to contacts. You can read what they said."
            : "You can read this chat. Writing here takes a role in " + space + " that can use it.",
        manage: channel.kind === "GROUP" && channel.me.canManage,
        contactOps: contact && abilities.write && abilities.contactMessage,
    };
}

/** Whether the reader may start a group. */
export function mayStartChannel(abilities: Abilities): boolean {
    return abilities.write;
}
