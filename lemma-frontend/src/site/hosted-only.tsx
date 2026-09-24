"use client";

import { useSyncExternalStore, type ReactNode } from "react";
import { isLocalDeployment } from "./config";

const never = () => () => {};

/** Children only where this is hosted Lemma, not a local installation.
 *
 *  For the site pages, which are prerendered once, at build time, by a build
 *  that is shared with every desktop app -- so the HTML always says "hosted"
 *  and only the browser, reading `/site-config.js`, knows otherwise. Hydrated
 *  as the server rendered it and corrected straight after, which
 *  `useSyncExternalStore` does without a mismatch. */
export function HostedOnly({ children }: { children: ReactNode }) {
    const local = useSyncExternalStore(never, isLocalDeployment, () => false);
    return local ? null : children;
}
