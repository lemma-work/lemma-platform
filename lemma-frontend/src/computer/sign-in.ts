/** Reading a sign-in: which site you are actually on, and what came of it.
 *
 *  Split from the pane for the reason everything else in here is: these are
 *  rules, the pane is a canvas, and only one of those can be tested. This one
 *  is worth testing more than most — it decides the host shown above a
 *  password field, which is the single label on the screen a person is being
 *  asked to trust.
 */

/* The card in the transcript and the pane it opens have to name the same site.
   If they could disagree, a card saying one host over a browser showing
   another would be the exact confusion this display exists to prevent — so
   there is one reading of a URL in this app, and this is not a second one. */
import { hostOf } from "@/thread/tool-cards";

/** Where the browser is, as a person would check it.
 *
 *  Read off the page the browser is *on*, not the site it was sent to. A
 *  sign-in is a chain of redirects by design — to an identity provider, to a
 *  second factor, back — and a header pinned to the requested origin kept
 *  naming the first site, with its padlock, above a page served by another
 *  one. On the one screen in this product whose whole job is "type your
 *  password here", that is a claim it cannot make and must not appear to.
 */
export interface Whereabouts {
    /** The host to show. */
    host: string;
    /** Whether the page showing it came over TLS. */
    secure: boolean;
    /** Whether this is somewhere other than the site the agent asked for. Not
     *  a fault, and said plainly rather than hidden: an identity-provider hop
     *  is what a real sign-in looks like. */
    elsewhere: boolean;
    /** Whether the browser has got where it was sent. Answering late is the
     *  ordinary case for a question that pauses a run, so "still on its way"
     *  has to read as progress rather than as an empty browser. */
    arrived: boolean;
}

export function whereabouts(origin: string, page: string | null): Whereabouts {
    const showing = page || origin;
    const host = hostOf(showing);
    const wanted = hostOf(origin);
    return {
        host,
        secure: showing.startsWith("https://"),
        elsewhere: host !== wanted,
        /* No page yet is not "arrived somewhere else" — it is a browser that
           has not answered. Reported as not arrived, which is what it is. */
        arrived: Boolean(page) && host === wanted,
    };
}

/** What to say once somebody has answered.
 *
 *  Three outcomes and not two. "I signed in" and "the site stopped asking"
 *  are different claims, and the second is a guess the server makes by
 *  looking — so a person who really did sign in is told the run is carrying
 *  on either way, and warned only that they may be asked again. Reporting
 *  the guess as the answer would call somebody a liar about their own
 *  password.
 */
export function outcomeSay(signedIn: boolean, working: boolean): { headline: string; note: string } {
    if (!signedIn) {
        return {
            headline: "Told the teammate",
            note: "It knows you could not sign in, and will not wait. You can close this.",
        };
    }
    if (working) {
        return {
            headline: "Signed in",
            note: "The teammate is carrying on. The browser keeps the session, so you will not be asked again.",
        };
    }
    return {
        headline: "Signed in",
        note: "The teammate is carrying on, but the site was still showing a login form just now — so you may be asked again.",
    };
}
