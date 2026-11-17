/** "What do you do?" — asked once, kept on the person.
 *
 *  The answer ranks what is suggested next (plugins, Lemmas to join), so it
 *  belongs to the person rather than the organization: two people in one
 *  company do different jobs, and one person in two organizations does the
 *  same one. The ids are the server's (`Job` on `PUT /users/me/work`). */

export type JobId =
    | "SALES"
    | "MARKETING"
    | "PRODUCT"
    | "FOUNDER"
    | "OPERATIONS"
    | "HR"
    | "PROJECT_MANAGEMENT"
    | "ENGINEERING"
    | "SUPPORT";

export const JOBS: readonly { id: JobId; label: string }[] = [
    { id: "SALES", label: "Sales" },
    { id: "MARKETING", label: "Marketing" },
    { id: "PRODUCT", label: "Product" },
    { id: "FOUNDER", label: "Founder" },
    { id: "OPERATIONS", label: "Operations" },
    { id: "HR", label: "HR" },
    { id: "PROJECT_MANAGEMENT", label: "Project management" },
    { id: "ENGINEERING", label: "Engineering" },
    { id: "SUPPORT", label: "Customer support" },
];

/** The longest "something else" the server keeps. */
export const OTHER_LIMIT = 120;

export interface WorkAnswer {
    jobs: JobId[];
    other: string | null;
}

/** What the person said, as the server takes it: the picked jobs in the
 *  order they appear on screen, and their own words only when they wrote
 *  some. Nothing picked and nothing written is still an answer -- they
 *  skipped -- and saving it is what keeps them from being asked again. */
export function workAnswer(picked: ReadonlySet<JobId>, other: string): WorkAnswer {
    const words = other.trim().slice(0, OTHER_LIMIT);
    return {
        jobs: JOBS.filter((job) => picked.has(job.id)).map((job) => job.id),
        other: words || null,
    };
}

/** How long after sign-up somebody who has not answered is still asked.
 *  Long enough to catch whoever signed up in a chat and opens the web days
 *  later; short enough that people who were here before the question existed
 *  are not stopped by it. */
const ASK_FOR_DAYS = 14;

/** Whether this person still has the question ahead of them. Read off the
 *  current user: an answer of any kind (skipping included) ends it, and so
 *  does an account older than the window. A user the server did not describe
 *  is never stopped. */
export function stillAsksWork(
    user: { work?: unknown; created_at?: string | null } | null | undefined,
    now: number = Date.now(),
): boolean {
    if (!user || !("work" in user)) return false;
    if (user.work) return false;
    const made = user.created_at ? Date.parse(user.created_at) : NaN;
    if (!Number.isFinite(made)) return false;
    return now - made < ASK_FOR_DAYS * 24 * 60 * 60 * 1000;
}
