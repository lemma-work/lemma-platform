/** The Friday review: a schedule on the teammate, and what it is told.
 *
 *  It is standing work like any other — a TIME schedule targeting the
 *  teammate, owned by whoever turned it on — so it shows in Standing work,
 *  keeps a run history and can be paused there. What makes it a review is its
 *  instruction, which this file writes: count with the tool, write the page,
 *  suggest, tell the reviewer. The counting is code; the teammate is told in
 *  so many words never to compute or change a number.
 *
 *  Whoever turns it on is the reviewer. The run acts as them, so the message
 *  at the end reaches them, and the schedule is theirs to pause. */

export const REVIEW_SCHEDULE = "weekly_review";

/** Fridays at four, in the reviewer's own time zone. */
export const REVIEW_CRON = "0 16 * * 5";

/** The platform's cap on a schedule instruction (`schedule_schemas.py`). */
export const INSTRUCTION_LIMIT = 8000;

/** Where a week's page goes. One level under /pages, because the Pages view
 *  lists only that level; dated with the year so next October's review does
 *  not land on this one. */
export function reviewPagePath(weekEnd: string): string {
    return "/pages/Week to " + sayDate(weekEnd) + ".md";
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "10 October 2026" from "2026-10-10". Cut from the string, not parsed into a
 *  Date: a date has no time zone, and a local-time read prints the day before
 *  west of Greenwich. */
export function sayDate(isoDate: string): string {
    const [year, month, day] = isoDate.slice(0, 10).split("-").map(Number);
    return day + " " + (MONTHS.at(month - 1) ?? "") + " " + year;
}

/** The next time the review fires after `now`, as a date to say. */
export function nextReview(now: Date): Date {
    const next = new Date(now);
    next.setHours(16, 0, 0, 0);
    const ahead = (5 - next.getDay() + 7) % 7;
    next.setDate(next.getDate() + ahead);
    if (next.getTime() <= now.getTime()) next.setDate(next.getDate() + 7);
    return next;
}

/** "Friday 10 October" — the day the next review comes. */
export function sayReviewDay(when: Date): string {
    return "Friday " + when.getDate() + " " + MONTHS.at(when.getMonth());
}

export function reviewInstruction(name: string): string {
    return [
        "This is " + name + "’s weekly review. It runs unattended: nobody is watching, so never ask anyone a question and never ask for approval. Change nothing except what steps 2 and 3 say.",
        "",
        "1. Call the score_week tool. It counts the past week from the platform and from this space’s tables, and records the results in scorecard_weeks. Never compute, estimate or change a number yourself; quote what the tool returns. If it says there is no scorecard, stop.",
        "",
        "2. Write a page at /pages/Week to <the week’s end date, like 10 October 2026>.md. Start with one line per measure: the measure, what the tool shows, the target, and whether it was met. Then a short section, What went wrong: at most three sentences on the misses, naming only what you can point to — conversations from the week, approvals you were refused, corrections people made. If nothing went wrong, say so in one line.",
        "",
        "3. Suggest at most five changes that would fix what went wrong, one row each in the review_suggestions table, with status open and week set to the week’s end date. Each row has a kind, a one-line title and a one-line why that names the evidence:",
        "- remember: a fact to keep. path = /memory/<short-lowercase-topic>.md, content = the whole note, a few lines.",
        "- skill: a fix to how you do something. path = /skills/<name>/SKILL.md of a skill that exists, content = the whole new file, frontmatter included, changed only where the fix is.",
        "- standing_work: something to do on a schedule. cron = a five-field cron, content = the instruction it would carry.",
        "Never write the memory, the skill or the schedule yourself. A person decides, from About.",
        "",
        "4. Send the person this review is for one short message with message_user: how many measures were met, the one miss that matters most, and that the review and its suggestions are in " + name + "’s About, under Judged on.",
    ].join("\n");
}
