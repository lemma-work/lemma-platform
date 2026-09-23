"use client";

import { SessionGate } from "@/session/session";
import { AppShell } from "./shell";

/** The gate and the thing it guards, behind one browser-only boundary.
 *
 *  They have to be imported together. Everything the gate decides from —
 *  whether this is sample mode, and what the SDK's auth manager knows — lives
 *  in browser storage, so a server rendering it is a server guessing: it reads
 *  no `localStorage`, gets `live` and `loading`, and paints the waiting screen.
 *  A browser in sample mode paints the shell. React then hydrates one onto the
 *  other and says so.
 *
 *  Keeping the gate outside this boundary and "fixing" the mismatch with a
 *  mounted flag would work and would be worse: it would mean the door and the
 *  waiting screen are things the server has an opinion about, which they are
 *  not.
 */
export function App() {
    return (
        <SessionGate>
            <AppShell />
        </SessionGate>
    );
}
