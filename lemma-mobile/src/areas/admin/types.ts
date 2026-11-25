/** Shapes the settings parts share. */

/** One live line under a part's row on a Lemma's Settings tab: what is
 *  true there now. `text` is null while it is read, and on a failure, when
 *  the row says what the part is for instead (`LemmaPart.blurb`), so a
 *  refused read never looks like an empty one. `needs` is the violet dot:
 *  somebody asking to join, a model that is not there. */
export interface AboutLine {
    text: string | null;
    needs?: boolean;
}

/** What a screen for one of a Lemma's parts is given. */
export interface PartProps {
    podId: string;
}
