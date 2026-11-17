import type { Job, TableKind } from "./types";

/** The shared tables. A plugin makes one (its table, and the app that shows
 *  it) or uses one another plugin made. They are matched by what they hold,
 *  not by name, so a Lemma's own "contacts" can still be People. */
export const SHARED_TABLES: Record<TableKind, { name: string; line: string }> =
    {
        person: {
            name: "People",
            line: "Anyone you deal with, inside or outside the company",
        },
        company: {
            name: "Companies",
            line: "The organisations those people are at",
        },
        touch: {
            name: "Conversations",
            line: "One line per email, meeting or message",
        },
        commitment: {
            name: "Follow-ups",
            line: "What someone owes someone, and by when",
        },
        task: { name: "Tasks", line: "A thing to do, with an owner and a date" },
        project: {
            name: "Projects",
            line: "A group of tasks with a goal and a finish date",
        },
        meeting: {
            name: "Meetings",
            line: "A meeting, its notes and what was decided",
        },
        brand: { name: "Brand", line: "Logo, colours, fonts and voice" },
        metric: {
            name: "Weekly numbers",
            line: "A number reported every week, with the rows behind it",
        },
        deal: {
            name: "Deals",
            line: "A possible sale moving through stages",
        },
        campaign: {
            name: "Campaigns",
            line: "A push to an audience across channels",
        },
        content: {
            name: "Content",
            line: "A post, article, email or video, from idea to published",
        },
        mention: {
            name: "Mentions",
            line: "A public post about you or a rival",
        },
        feedback: {
            name: "Feedback",
            line: "Something a person said about the product, with the source",
        },
        feature: {
            name: "Features",
            line: "An idea, request or roadmap item",
        },
        ticket: {
            name: "Tickets",
            line: "A customer question until it is answered",
        },
        repo: { name: "Repos", line: "A code repository and its rules" },
        pr: { name: "Pull requests", line: "A proposed change to the code" },
        issue: { name: "Issues", line: "A bug or piece of engineering work" },
        signal: {
            name: "Signals",
            line: "An error, alert or event from an engineering tool",
        },
        employee: {
            name: "Team",
            line: "Someone who works here: role, manager, location",
        },
        candidate: {
            name: "Candidates",
            line: "A person applying for a role",
        },
        vendor: {
            name: "Vendors",
            line: "A supplier or tool you pay, and its contract",
        },
        request: {
            name: "Requests",
            line: "Something waiting for someone's yes",
        },
    };

/** The jobs a person can say they do, in the order the shelf lists them. */
export const JOBS: Array<{ id: Job; name: string }> = [
    { id: "sales", name: "Sales" },
    { id: "marketing", name: "Marketing" },
    { id: "product", name: "Product" },
    { id: "founder", name: "Founder" },
    { id: "ops", name: "Ops" },
    { id: "hr", name: "HR" },
    { id: "pm", name: "Project management" },
    { id: "dev", name: "Development" },
    { id: "support", name: "Customer support" },
    { id: "everyone", name: "Any job" },
];

export function jobName(job: Job): string {
    return JOBS.find((j) => j.id === job)?.name ?? job;
}
