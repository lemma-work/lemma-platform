import type { SourceSettings } from "@web/channels/sources";

/** The shapes where a Lemma is reached is read in. The web's names are kept
 *  where its copied modules (`web/`) read them: a place a Lemma answers is a
 *  "surface" on the wire and in code, and never in what a person reads. */

/** A place a Lemma answers (the web's `Surface`, read off `podSurfaces.list`). */
export interface Surface {
    id: string;
    /** WHATSAPP | TELEGRAM | RESEND | SLACK | TEAMS, or one this build has
     *  never heard of. */
    platform: string;
    /** Unique in a Lemma, and what every write is keyed by. */
    name: string;
    /** It answers as the Lemma itself, rather than as one of its agents. */
    mine: boolean;
    agentName: string;
    agentKey?: string;
    /** Where people write to it: a number, a username, an address. */
    handle: string;
    email?: string;
    active: boolean;
    status?: string;
}

/** What a Lemma has made Public, which people outside it are answered from. */
export interface PublicReach {
    files: { name: string; path: string | null }[];
    tables: string[];
    more: boolean;
}

/** Somebody named on a group: a member, a contact or an agent. */
export interface PersonRef {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string | null;
    name: string | null;
}

/** A group the Lemma is in, with its settings there; `joined` is whether the
 *  reader is in it (an admin is shown private ones they are not in). */
export type BotGroup = SourceSettings & { joined: boolean };

/** Where the reader's other Lemmas already answer, across the organization. */
export interface MySurface {
    platform: string;
    podId: string;
    name: string;
}

/** Lemma's setup bot making a Telegram bot of the Lemma's own: started here,
 *  finished in Telegram, noticed back here. */
export interface GuidedSetup {
    setupId: string;
    /** Opens Telegram on the setup bot. */
    launchUrl: string;
    managerBot: string;
    status: string;
    botUsername?: string;
    botLaunchUrl?: string;
    expiresAt: string;
    error?: string;
}

/** Who can answer a place: the Lemma itself (`front`), or one of its agents. */
export interface Answerer {
    name: string;
    label: string;
    front: boolean;
}

/** A Slack or Teams conversation the Lemma could answer in. */
export interface PlaceOption {
    id: string;
    name: string | null;
    /** False when the Lemma has not been invited there yet. */
    isMember: boolean | null;
}

/** A Slack workspace Lemma may make apps in, from a saved configuration token. */
export interface SlackAppWorkspace {
    teamId: string;
    teamName: string | null;
    /** Slack stopped accepting the saved token. */
    needsNewToken: boolean;
}

export interface SlackAppWorkspaces {
    /** An organization owner or editor: may save a token and make apps. */
    canManage: boolean;
    workspaces: SlackAppWorkspace[];
}

/** A Slack workspace Lemma's own app looks after for the organization. */
export interface SlackWorkspace {
    teamId: string;
    teamName: string | null;
}

/** A link that adds the Lemma's Telegram bot to a group, good for an hour. */
export interface InviteLink {
    url: string;
    expiresAt: string;
}
