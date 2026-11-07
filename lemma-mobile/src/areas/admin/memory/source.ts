import type { LibraryItem } from "../../../thread/library-item";

/** What a Lemma remembers: the notes it wrote as it worked (PARITY.md, F).
 *
 *  Read from the files themselves, in the folders the server keeps them in
 *  (`thread/memory-notes.ts`), so it is the notes that exist and nothing
 *  else. One read: a folder's listing; `model.ts` walks the folders. The
 *  live side is `live.ts`, the sample side `sample.ts`; screens reach it
 *  through `hooks.ts`. */
export interface MemorySource {
    /** One page of a folder's listing. Throws for a folder that is not there. */
    list(podId: string, directory: string, page?: string): Promise<{ items: LibraryItem[]; next: string | null }>;
}
