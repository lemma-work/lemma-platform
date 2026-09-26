/** How long a desktop sign-in request has left, said as a clock.
 *
 *  The request expires on the backend (`expires_in_seconds`), and when it does
 *  the wait simply fails. Showing the time left turns "why did that stop
 *  working?" into something the reader could see coming. Whole seconds,
 *  rounded up, so it never reads 0:00 while there is still time. */
export function timeLeft(expiresAt: number, now: number): string {
    const seconds = Math.max(0, Math.ceil((expiresAt - now) / 1000));
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}
