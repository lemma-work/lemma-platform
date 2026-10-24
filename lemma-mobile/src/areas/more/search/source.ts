import type { ResourceKind } from "@web/thread/resource-conversation";
import type { RecordSource } from "@web/search/sources";

/** Search (PARITY.md, G): the reads one box makes across every Lemma that
 *  no other area already makes.
 *
 *  The rest of what search finds is read through the area that owns it,
 *  under that area's own key, so a list on screen elsewhere and the list
 *  search matches against are one request and one cache entry, as on the
 *  web (`search/use-search.ts`): agents, workflows and schedules from work,
 *  tables and their columns from tables, chats from chats, people from
 *  settings. What is left is here.
 *
 *  The lists come back as the server sent them (`unknown`), and the web's
 *  own readers (`web/sources.ts`) make sense of them, so a shape that moves
 *  is read the same way on both. */
export interface SearchSource {
    /** A Lemma's functions (`functions.list`). */
    functions(podId: string): Promise<unknown>;
    /** A Lemma's apps (`apps.list`). Not the organization's app list on Home,
     *  which reads every Lemma's and passes over one that fails: here a
     *  Lemma whose apps could not be read has to say so. */
    apps(podId: string): Promise<unknown>;
    /** Files whose name or contents match, by the server's own search, the
     *  closest few first (`files.search`, hybrid). */
    files(podId: string, query: string, limit: number): Promise<unknown>;
    /** Rows of one table holding the words in any of its text columns: one
     *  read-only SELECT per table (`datastore.query`), at most `table.limit`. */
    records(podId: string, table: RecordSource, query: string): Promise<Record<string, unknown>[]>;
    /** The chat that belongs to a function (or anything else a chat can be
     *  bound to), made the first time somebody asks: its id. Null where this
     *  source cannot bind one, and a new chat is opened instead. */
    discuss(podId: string, kind: ResourceKind, name: string): Promise<string | null>;
}
