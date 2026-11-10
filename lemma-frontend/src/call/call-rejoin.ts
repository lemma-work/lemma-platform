/** When a call that closed is joined again, and when it is over.
 *
 *  Pure, so every client that holds a call rejoins it the same way: the web's
 *  `useCall`, and the phone app's call, which runs this module where it is. */

/** Times a dropped call is joined again before it is given up as ended. */
export const MAX_REJOINS = 2;

/** The wait before the first rejoin, doubled for the next: a server
 *  restarting or a network coming back is not there the instant it went. */
export const REJOIN_DELAY_MS = 1_000;

/** After the socket closed: how long to wait before joining the same call
 *  again, or null when the call is over. Only a drop nobody chose is joined
 *  again (`closeOutcome`'s `dropped`), and only `MAX_REJOINS` times a call:
 *  the server refusing or ending the call is its answer, not a hiccup.
 *  `rejoins` is how many times this call has been joined again so far. */
export function rejoinAfter(dropped: boolean, rejoins: number): number | null {
    if (!dropped || rejoins >= MAX_REJOINS) return null;
    return REJOIN_DELAY_MS * 2 ** rejoins;
}
