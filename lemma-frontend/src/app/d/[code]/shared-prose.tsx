"use client";

import { Prose } from "@/thread/markdown";
import { imageSource } from "./embedded";

/** Markdown has to cross into the client.
 *
 *  The page itself is a server component — it has to be, because the bytes
 *  come from the API's origin and the reader has no session to fetch them
 *  with. But react-markdown reaches for `createContext`, which does not exist
 *  in a server render, so the rendering half is marked as client and the page
 *  hands it a finished string. Nothing about that string is secret: it is the
 *  document the link was minted for.
 *
 *  The code comes across rather than a resolver, because a function cannot
 *  cross from a server component to a client one; the pictures are pointed
 *  through the link here, on the client side of that line. */
export function SharedProse({ text, code }: { text: string; code: string }) {
    return <Prose text={text} imageSource={(src) => imageSource(code, src)} />;
}
