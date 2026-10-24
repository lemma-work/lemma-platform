import type { Channel, ConversationRef, Lemma, Me, NeedCard, NeedItem, Org, Owns, Runs, Starter, AppItem } from "./types";
import type { PendingAsk, WaitRow } from "./model";

/** Everything the screens read, behind one interface, with two sources:
 *  `live.ts` (main's API, per Lemma) and `sample.ts` (fixtures shaped like
 *  the design canvas). Screens never call the SDK for a list; they call this.
 *
 *  `pulse` is the part the open backend PRs replace: today it is six reads
 *  per Lemma; with #1045 it is `needs-you` + `recents` for the whole org, and
 *  with #1046 `owns` reads `config.outcome` and `/outcome/standing`. */

/** One Lemma's raw reads, from which the model derives its queue and rows. */
export interface LemmaPulse {
    lemma: Lemma;
    conversations: ConversationRef[];
    asks: PendingAsk[];
    waits: WaitRow[];
    /** Its channels: a person's line, groups. */
    channels: Channel[];
    /** Contacts waiting on a person, where the reader may look after them. */
    contacts: Channel[];
    tables: { name: string; updated: string | null }[];
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
    owns(lemma: Lemma): Promise<Owns>;
    apps(orgId: string, lemmas: readonly Lemma[]): Promise<AppItem[]>;
    /** What a Home card offers, read off the paused call. */
    card(item: NeedItem): Promise<NeedCard>;
    /** Answer an approval or a question in place: `decision` is the
     *  approval's, `answers` a question's. */
    answer(item: NeedItem, decision: "APPROVE_ONCE" | "DENY", answers?: Record<string, unknown>): Promise<void>;
    runs(lemma: Lemma): Promise<Runs>;
    starters(lemma: Lemma): Promise<Starter[]>;
    /** A shared chat or a contact's: its name and lines, oldest first. */
    lines(podId: string, channelId: string): Promise<{ title: string; kind: Channel["kind"]; lines: ChatLine[] }>;
    say(podId: string, channelId: string, text: string): Promise<void>;
    homeSeen(orgId: string): Promise<number | null>;
    markHomeSeen(orgId: string, at: number): Promise<void>;
}
