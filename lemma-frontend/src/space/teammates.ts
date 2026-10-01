/** What each teammate is waiting on you for, and the order the Teammates page
 *  shows them in.
 *
 *  Pure, and in one place, so the rail's badge, the line on a card and the
 *  "Needs you" group cannot disagree about the same teammate. */

export interface Owed {
    count: number;
    /** The one it has been waiting on longest, by name. */
    first: string;
}

/** The waiting queue, per teammate. The queue arrives stuck-longest first
 *  (`byStuckLongest`), so the first row seen for a pod is the one to name. */
export function owedByPod(rows: readonly { podId: string; workflowName: string }[]): Map<string, Owed> {
    const owed = new Map<string, Owed>();
    for (const row of rows) {
        const was = owed.get(row.podId);
        owed.set(row.podId, was ? { ...was, count: was.count + 1 } : { count: 1, first: row.workflowName });
    }
    return owed;
}

/** Both queues a teammate can be waiting on you in: workflow forms, and
 *  conversations paused on a question or an approval. One map, so a
 *  scheduled run stopped on `ask_user` badges the rail the same way a form
 *  does, instead of reading as a teammate with nothing to say. */
export function owedFrom(
    waits: readonly { podId: string; workflowName: string }[],
    asked: readonly { podId: string; title: string }[],
): Map<string, Owed> {
    return owedByPod([...waits, ...asked.map((row) => ({ podId: row.podId, workflowName: row.title }))]);
}

/** The line under a teammate's name while something is waiting on you. */
export function sayOwed(owed: Owed): string {
    return owed.count === 1 ? owed.first + " is waiting on you" : owed.count + " things are waiting on you";
}

/** Whether a teammate needs you: an entry in the real queue, or — only where
 *  it is written by hand, in the sample and the landing tour — its own
 *  `waiting` line. The live source always leaves that line empty. */
export function needsYou(pod: { id: string; waiting: string }, owed: ReadonlyMap<string, Owed>): boolean {
    return (owed.get(pod.id)?.count ?? 0) > 0 || pod.waiting.trim() !== "";
}

/** What the rail says under a teammate's name: what it needs from you, or,
 *  when nothing, what it is for. */
export function railLine(pod: { id: string; waiting: string; description?: string }, owed: ReadonlyMap<string, Owed>): string {
    const mine = owed.get(pod.id);
    if (mine) return sayOwed(mine);
    return pod.waiting.trim() || pod.description?.trim() || "";
}

/** Two groups and never more: the ones that need you, then everyone else,
 *  each kept in the order the list arrived in. */
export function byNeed<T extends { id: string; waiting: string }>(
    pods: readonly T[],
    owed: ReadonlyMap<string, Owed>,
): { needs: T[]; rest: T[] } {
    const needs: T[] = [];
    const rest: T[] = [];
    for (const pod of pods) (needsYou(pod, owed) ? needs : rest).push(pod);
    return { needs, rest };
}

/** "Hired 12 March by Priya". Tenure, said once, from the pod's own record:
 *  when it was made and by whom. No count of days — a number that grows by
 *  itself is not news. */
export function sayHired(pod: { hiredAt?: string; hiredBy?: string; members: readonly { userId?: string; name: string }[] }, now = new Date()): string {
    if (!pod.hiredAt) return "";
    const at = new Date(pod.hiredAt);
    if (Number.isNaN(at.getTime())) return "";
    const day = at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
        ? { day: "numeric", month: "long" }
        : { day: "numeric", month: "long", year: "numeric" });
    const by = pod.hiredBy ? pod.members.find((member) => member.userId === pod.hiredBy) : undefined;
    if (!by) return "Hired " + day;
    return "Hired " + day + " by " + (by.name === "You" ? "you" : by.name);
}
