import type { Automation, Channel, ConversationRef, DayCount, Lemma, Me, NeedCard, NeedItem, Org, Owns, Runs, Starter, AppItem } from "./types";

/** Everything the screens read, behind one interface, with two sources:
 *  `live.ts` (main's API) and `sample.ts` (fixtures shaped like the design
 *  canvas). Screens never call the SDK for a list; they call this.
 *
 *  Live, what waits on you and what moved lately are one read each for the
 *  whole organization (`needsYou`, `recents`); only a Lemma's chats with
 *  people and its tables are still read per Lemma. */

/** One Lemma's reads, from which the model derives its rows. */
export interface LemmaPulse {
    lemma: Lemma;
    /** The reader's recent conversations with it, newest first. */
    conversations: ConversationRef[];
    /** Its workflows and schedules that ran lately, one each; null where
     *  they are read off `conversations` instead (the sample). */
    automations: Automation[] | null;
    /** Its chats with people: a person's own chat with it, groups. */
    channels: Channel[];
    tables: { name: string; updated: string | null }[];
    /** The reader's conversations per day, by who started them; null where
     *  they are counted from `conversations` instead (the sample). */
    activity: DayCount[] | null;
}

export interface ChatLine {
    id: string;
    who: string;
    whoKind: "MEMBER" | "CONTACT" | "AGENT";
    mine: boolean;
    text: string;
    at: number | null;
}

export interface LemmaSource {
    readonly label: "live" | "sample";
    me(): Promise<Me>;
    orgs(): Promise<Org[]>;
    lemmas(orgId: string): Promise<Lemma[]>;
    pulse(orgId: string, lemmas: readonly Lemma[]): Promise<LemmaPulse[]>;
    /** Everything waiting on the reader, every Lemma, oldest first. */
    needs(orgId: string, lemmas: readonly Lemma[]): Promise<NeedItem[]>;
    owns(lemma: Lemma): Promise<Owns>;
    apps(orgId: string, lemmas: readonly Lemma[]): Promise<AppItem[]>;
    /** What a Home card offers, read off the paused call. */
    card(item: NeedItem): Promise<NeedCard>;
    /** Answer an approval or a question in place: `decision` is the
     *  approval's, `answers` a question's (a MESSAGE's one choice). */
    answer(item: NeedItem, decision: "APPROVE_ONCE" | "DENY", answers?: Record<string, unknown>): Promise<void>;
    runs(lemma: Lemma): Promise<Runs>;
    starters(lemma: Lemma): Promise<Starter[]>;
    /** A shared chat or a contact's: its name and lines, oldest first. */
    lines(podId: string, channelId: string): Promise<{ title: string; kind: Channel["kind"]; lines: ChatLine[] }>;
    say(podId: string, channelId: string, text: string): Promise<void>;
    homeSeen(orgId: string): Promise<number | null>;
    markHomeSeen(orgId: string, at: number): Promise<void>;
}
