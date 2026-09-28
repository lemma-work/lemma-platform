/** When a conversation last moved, short enough to sit on the same line as
 *  its title.
 *
 *  One ladder, coarsening with age: the time today, the weekday within the
 *  week, the date after that, the year only once it is not this one. The list
 *  used to mix a bare clock with a full "Sat 26 Sept" — two shapes a row apart,
 *  and the longer one pushed the title onto a second line.
 */
export function listStamp(iso: string | null | undefined, now = new Date()): string {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    if (date.toDateString() === now.toDateString()) {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const daysAgo = Math.ceil((midnight.getTime() - date.getTime()) / 86_400_000);
    if (daysAgo >= 0 && daysAgo < 7) return date.toLocaleDateString([], { weekday: "short" });
    if (date.getFullYear() === now.getFullYear()) return date.toLocaleDateString([], { day: "numeric", month: "short" });
    return date.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}
