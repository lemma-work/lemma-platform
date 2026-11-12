import type { BeginSource } from "./begin/source";
import type { CallSource } from "./call/source";
import type { ComputerSource } from "./computer/source";
import type { InstallSource } from "./install/source";
import type { SearchSource } from "./search/source";
import type { ShareSource } from "./share/source";
import type { TourSource } from "./tour/source";
import type { WelcomeSource } from "./welcome/source";

/** Search, starting out, sharing, the agent's computer, calls and the tour
 *  (PARITY.md, G).
 *
 *  Everything these screens read or change, part by part, each part in its
 *  own folder with a live side (`live.ts`) and a sample side (`sample.ts`).
 *  Screens reach it only through `useArea("more")` (each part's `hooks.ts`),
 *  never the SDK directly, so sample data and a live account are the same
 *  screens. A part per key, so no two parts' calls can collide. */
export interface MoreSource {
    /** One box for everything in every Lemma. */
    search: SearchSource;
    /** The first morning: invitations, organizations to join, a new one; the
     *  first profile step; an invitation link. */
    welcome: WelcomeSource;
    /** Starting a Lemma, and asking to join one that is not yours. */
    begin: BeginSource;
    /** Templates, installing from GitHub, and remixing an app. */
    install: InstallSource;
    /** Who can open a Lemma, a file, a table or an app, and its links. */
    share: ShareSource;
    /** The computer the Lemmas work on: its state, screen, files and logins;
     *  a site waiting on a person to sign in. */
    computer: ComputerSource;
    /** A voice call with a Lemma. */
    call: CallSource;
    /** The tour, and how each place works. */
    tour: TourSource;
}
