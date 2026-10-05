"use client";

import { createContext, useContext } from "react";

/* Whether the pane this sits in is on screen.
 *
 *  A pane you have visited stays mounted behind the one in front, so it keeps
 *  its place and its draft. What it must not keep is its clock: a poll, a
 *  change check or a live picture behind a hidden pane spends requests on a
 *  screen nobody is looking at. Anything that repeats asks this first. Outside
 *  a pane — a sheet, the landing preview — it is always on screen. */
export const PaneVisibleContext = createContext(true);

export function usePaneVisible(): boolean {
    return useContext(PaneVisibleContext);
}
