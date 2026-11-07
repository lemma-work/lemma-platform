import type { PickedFile } from "../../../data/io";

/** When a Lemma was started, and by whom: the web's "Hired 12 October by
 *  Sam", said in the phone's words. */
export interface LemmaOrigin {
    createdAt: string | null;
    /** The user id of whoever started it. */
    createdBy: string | null;
}

/** A Lemma's name, what it is for, its face, and deleting it (the web's
 *  About hero, `space/about-page.tsx`, and `space/delete-teammate.tsx`). */
export interface AboutSource {
    origin(podId: string): Promise<LemmaOrigin>;
    rename(podId: string, name: string): Promise<void>;
    /** Cleared rather than stored blank when the words are empty. */
    describe(podId: string, description: string): Promise<void>;
    /** A face: a picture's address, a chosen character (`lemma-identity:N`),
     *  or null for the one its id draws. */
    setIcon(podId: string, icon: string | null): Promise<void>;
    /** Upload a picture from the phone as an icon; answers its address. */
    uploadIcon(file: PickedFile): Promise<string>;
    remove(podId: string): Promise<void>;
}
