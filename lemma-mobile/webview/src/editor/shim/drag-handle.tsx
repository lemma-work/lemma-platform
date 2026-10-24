import type { ReactNode } from "react";

/** The web's block handle follows the pointer: it appears beside the block a
 *  mouse is over. A phone has no pointer to follow, so the handle is not
 *  drawn there, and the code it would carry (with its collaboration support)
 *  stays out of the page. Adding a block is the slash menu's job on a phone. */
export default function DragHandle(_props: { children?: ReactNode; [prop: string]: unknown }) {
    void _props;
    return null;
}
