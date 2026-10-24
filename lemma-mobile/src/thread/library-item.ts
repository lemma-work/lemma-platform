/** The one shape `memory-notes.ts` takes from the web's data layer
 *  (`lemma-frontend/src/data/types.ts`), kept here so the copied thread
 *  files need nothing else from it. */
export interface LibraryItem {
    id: string;
    name: string;
    kind: "file" | "folder" | "table";
    path: string;
    updated: string;
    detail: string;
    status?: string;
    description?: string;
    visibility?: string;
    rls?: boolean;
    owner?: string | null;
}
