/** What the teammate's browser is holding, said in a line.
 *
 *  These are read from the browser itself every time, not from a table: it
 *  keeps its own profile in the durable home, so what Chrome holds is the only
 *  true answer. Nothing here is a secret — cookie *values* never leave the
 *  sandbox at all, which is structural rather than a promise.
 */

import type { WebLogin } from "./queries";

/** How long this login has, as near as a browser can say.
 *
 *  `expires` is the soonest of the site's cookies to lapse, which is the
 *  closest thing there is to "when will I have to sign in again". Null means
 *  they are all session cookies — which does not mean "expiring now", it means
 *  the browser drops them when it stops, and this browser is not in the habit
 *  of stopping.
 */
export function lastsFor(login: WebLogin, now: Date): string {
    if (!login.expires) return "for as long as the browser runs";
    const when = new Date(login.expires);
    if (Number.isNaN(when.getTime())) return "";
    const days = Math.round((when.getTime() - now.getTime()) / 86_400_000);
    if (days < 0) return "already lapsed";
    if (days === 0) return "until later today";
    if (days === 1) return "until tomorrow";
    if (days < 30) return "for another " + days + " days";
    const months = Math.round(days / 30);
    return "for another " + months + (months === 1 ? " month" : " months");
}

/** The whole secondary line for one site.
 *
 *  The cookie count is a rough sense of scale rather than a health check, and
 *  it is said second because nobody came here to count cookies — they came to
 *  see whether a site is signed in and how long that lasts.
 */
export function loginNote(login: WebLogin, now: Date): string {
    const lasts = lastsFor(login, now);
    const count = login.cookie_count === 1 ? "1 cookie" : login.cookie_count + " cookies";
    return lasts ? "Kept " + lasts + " · " + count : count;
}

/** The two groups this list is really made of.
 *
 *  A browser collects a cookie domain per site *visited*, not per site signed
 *  in to, so a real profile holds the ad networks, the font CDN and the video
 *  somebody watched once alongside the three sites this pane exists to show.
 *  Under one heading reading "sites this browser is signed in to", all of them
 *  were being claimed as logins.
 *
 *  **Only split when the split says something.** The mark begins empty on every
 *  profile that predates it, so a list that hid four real sites behind a
 *  collapsed "other" because nobody had answered a sign-in yet would be worse
 *  than the flat list it replaced. Both groups have to be occupied before the
 *  heading appears, and `split` is what says so.
 */
export function groupSites(items: readonly WebLogin[]): {
    signedIn: WebLogin[];
    other: WebLogin[];
    split: boolean;
} {
    const signedIn = items.filter(saidSoFor);
    const other = items.filter((login) => !saidSoFor(login));
    return { signedIn, other, split: signedIn.length > 0 && other.length > 0 };
}

/** Whether to mark this site as one somebody signed in to on purpose.
 *
 *  Only shown where true. `signed_in` records that somebody answered "yes, I
 *  signed in" to a request for this site — so false means "nobody said so",
 *  not "no session", and a row that read "not signed in" would be asserting
 *  something this data cannot support. Measured, not guessed: a real profile
 *  held two cookies for a site somebody was signed in to and six for one that
 *  had merely had a video played on it.
 */
export function saidSoFor(login: WebLogin): boolean {
    return login.signed_in;
}
