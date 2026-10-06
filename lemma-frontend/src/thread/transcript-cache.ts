/** The last few conversations a person had open, kept so going back to one is
 *  instant.
 *
 *  Opening a conversation remounts its pane, and the pane's session starts
 *  empty: every switch was a skeleton and two round trips, including a switch
 *  back to the conversation left a second ago. The pane now draws what it last
 *  held from here at once and re-reads the server behind it — the cached copy
 *  is what is shown while the truth is fetched, never instead of fetching it.
 *
 *  Also where hovering a conversation in the history parks its first page, so
 *  the click that usually follows lands on something already loaded.
 *
 *  Module state rather than React Query: the session owns the messages once
 *  the pane is up, and this only has to carry them across the remount. Kept to
 *  a handful so a long day of browsing cannot accumulate transcripts. */

export interface CachedTranscript<M, C> {
    conversation: C | null;
    /** The session's status, as it was. Shown while the server is re-read: a
     *  conversation left mid-run is most likely still running. */
    status?: string;
    messages: M[];
    /** The cursor onto older messages, so "Earlier" still works from the copy. */
    olderToken: string | null;
    savedAt: number;
}

export const RETAINED = 8;
/** How long a copy fetched by hovering is good enough to skip fetching again
 *  on the next hover. The pane re-reads on open whatever its age. */
export const PREFETCH_FRESH_MS = 15_000;

export class TranscriptCache<M, C> {
    private entries = new Map<string, CachedTranscript<M, C>>();
    private inFlight = new Map<string, Promise<void>>();
    private readonly retained: number;
    private readonly now: () => number;

    constructor(retained = RETAINED, now: () => number = Date.now) {
        this.retained = retained;
        this.now = now;
    }

    private key(podId: string, conversationId: string): string {
        return podId + ":" + conversationId;
    }

    get(podId: string, conversationId: string | null | undefined): CachedTranscript<M, C> | null {
        if (!conversationId) return null;
        return this.entries.get(this.key(podId, conversationId)) ?? null;
    }

    /** Most recently saved last; the oldest goes once there are too many. */
    save(podId: string, conversationId: string, entry: Omit<CachedTranscript<M, C>, "savedAt">): void {
        const key = this.key(podId, conversationId);
        this.entries.delete(key);
        this.entries.set(key, { ...entry, savedAt: this.now() });
        while (this.entries.size > this.retained) {
            const oldest = this.entries.keys().next().value;
            if (oldest === undefined) break;
            this.entries.delete(oldest);
        }
    }

    forget(podId: string, conversationId: string): void {
        this.entries.delete(this.key(podId, conversationId));
    }

    clear(): void {
        this.entries.clear();
        this.inFlight.clear();
    }

    /** Fetch a conversation into the cache unless a fresh copy is already
     *  here or on its way. Failures are dropped: a prefetch is a guess, and the
     *  pane fetches for itself when it opens. */
    prefetch(
        podId: string,
        conversationId: string,
        fetch: () => Promise<Omit<CachedTranscript<M, C>, "savedAt">>,
    ): Promise<void> {
        const key = this.key(podId, conversationId);
        const held = this.entries.get(key);
        if (held && this.now() - held.savedAt < PREFETCH_FRESH_MS) return Promise.resolve();
        const going = this.inFlight.get(key);
        if (going) return going;
        const started = fetch()
            .then((entry) => {
                /* The pane may have opened and saved something newer while this
                   was in the air; that copy wins. */
                const now = this.entries.get(key);
                if (!now || now.savedAt <= (held?.savedAt ?? -1)) this.save(podId, conversationId, entry);
            })
            .catch(() => undefined)
            .finally(() => this.inFlight.delete(key));
        this.inFlight.set(key, started);
        return started;
    }
}
