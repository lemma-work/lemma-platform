"use client";

import { SessionGate } from "@/session/session";
import { OutcomesShell } from "./shell";

/** The gate and the frame it guards, behind one browser-only boundary, for
 *  the same reason `shell/app.tsx` keeps them together. */
export function OutcomesApp() {
    return (
        <SessionGate>
            <OutcomesShell />
        </SessionGate>
    );
}
