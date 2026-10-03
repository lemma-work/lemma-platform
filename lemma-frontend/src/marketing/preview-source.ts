import { conversationWidget, kitWidget } from "./conversation-widgets";
import { sampleListing } from "@/thread/memory-notes";
import { teammates, teammateFor } from "./teammates";
import { fixtureSource } from "@/data/fixtures";
import { sampleShape } from "@/data/sample-tables";
import { samplePageIn, sampleTableIn, spaceOf } from "./sample-spaces";
import { SAMPLE_WORKFLOWS } from "./preview-fixtures";
import { readSchedules } from "@/schedule/schedules";
import { byEffort } from "@/data/connectable";
import type { Conversation, Member, PodSource, Tab } from "@/data/types";

const added = new Map<string, Member[]>();
export const previewCandidates = [
    { id: "aditi", label: "aditi@acme.test", orgRole: "member" },
    { id: "rohan", label: "rohan@acme.test", orgRole: "member" },
    { id: "maya", label: "maya@acme.test", orgRole: "admin" },
];

export function addPreviewMember(podId: string, id: string, role: string): void {
    const members = added.get(podId) ?? [];
    const candidate = previewCandidates.find(person => person.id === id);
    if (!candidate || members.some(person => person.id === id)) return;
    added.set(podId, [...members, { id, name: candidate.label, initials: id.slice(0, 2).toUpperCase(), kind: "person", role, can: role }]);
}

/* The faces of teammates hired during the tour. A hire is a copy of Kit
   with a new name, so it used to keep Kit's picture too — every new hire
   wore Kit in the rail while the reveal, which draws from the pod's id, drew
   the face that was dealt. A hire starts with no picture, which is what the
   live source hands back, and keeps whatever the hiring flow then saves. */
const faces = new Map<string, string | null>();
function faceOf(person: { id: string; icon: string }): string | null {
    return faces.has(person.id) ? faces.get(person.id) ?? null : person.icon;
}

const voicePath = "/skills/brand-voice/SKILL.md";
const edits = new Map<string, string>();
function members(id: string): Member[] {
    const person = teammateFor(id);
    const teammate: Member = { id: person.id, name: person.name, initials: person.name.slice(0, 2).toUpperCase(), kind: "teammate", role: person.role, can: "prepares work · asks before sending" };
    /* Kit's loop needs a whole product team: the engineer who confirms a fix
       is live, and support, who answers the big customers. */
    if (id === "kit") return [
        { id: "you", name: "You", initials: "YO", kind: "person", role: "Owner", can: "everything · assigns owners", userId: "sample-user" },
        { id: "dev", name: "Dev", initials: "DE", kind: "person", role: "Member", can: "confirms a fix is live before anyone hears back", userId: "dev-user" },
        { id: "sam", name: "Sam", initials: "SA", kind: "person", role: "Member", can: "answers Enterprise accounts", userId: "sam-user" },
        { id: "alex", name: "Alex", initials: "AL", kind: "person", role: "Member", can: "fixes what is assigned", userId: "alex-user" },
        { ...teammate, can: "drafts replies · nothing goes out until the fix is live" },
        ...(added.get(id) ?? []),
    ];
    return [
        { id: "you", name: "You", initials: "YO", kind: "person", role: "Owner", can: "everything", userId: "sample-user" },
        { id: "priya", name: "Priya", initials: "PR", kind: "person", role: "Member", can: "reviews external commitments", userId: "priya-user" },
        /* June's imports wait on Dev; nobody else's work does. */
        ...(id === "june" ? [{ id: "dev", name: "Dev", initials: "DE", kind: "person" as const, role: "Member", can: "approves customer data fixes", userId: "dev-user" }] : []),
        teammate,
        ...(added.get(id) ?? []),
    ];
}
function persona(id: string) {
    const person = teammateFor(id);
    return { name: person.name, initials: person.name.slice(0, 2).toUpperCase(), iconUrl: faceOf(person) };
}
function guidance(id: string) {
    const person = teammateFor(id);
    return `---\nname: brand-voice\ndescription: How ${person.name} works with the team.\n---\n\n# ${person.name} · Working guidance\n\n${person.job}\n\n## What the team taught me\n\n${person.learned}\n`;
}

/** What each sample teammate has written down, for About's "remembers"
 *  section in the tour. Kit's are specific because the tour opens on Kit;
 *  the others keep the one line they were written with. Dated relative to
 *  now, so the week's dot means what it says. */
const DAY = 86_400_000;
function notesOf(id: string): { path: string; gloss: string; daysAgo: number; text: string }[] {
    if (id === "kit") return [
        { path: "/memory/feedback-rules.md", gloss: "On mobile, “stuck” means Notifications", daysAgo: 0, text: "# Feedback rules\n\n- On mobile, “app feels stuck” is the missing push notification, not a timeout. File it under Notifications on mobile. (Dev)\n- Timeouts go to Dev, top priority. (You)\n- Priority is reports × paying accounts. (You)\n" },
        { path: "/memory/closing-the-loop.md", gloss: "Replies wait until the fix is live", daysAgo: 1, text: "# Closing the loop\n\n- Draft a retry reply for everyone who reported a problem when its fix merges. Nothing goes out until the PR’s author confirms it’s live. (You)\n- Enterprise accounts hear from their CSM. Kit drafts, Sam sends. (Sam)\n- If someone says it still breaks, reopen the theme and tell its owner.\n" },
        { path: "/memory/capture.md", gloss: "Every #feedback message, as it lands", daysAgo: 1, text: "# Capture\n\n- Every message in #feedback, every email to feedback@ and every in-app report is filed as it lands. Nothing waits for a weekly sweep. (You)\n- A report that fits no theme starts a new one; the nightly run suggests merges.\n" },
        { path: "/me/agents/pod-default/your-preferences.md", gloss: "Summaries as short bullets", daysAgo: 9, text: "# Your preferences\n\n- Summaries as short bullets, decisions first.\n" },
    ];
    if (id === "remy") return [
        { path: "/memory/northstar.md", gloss: "Anita signs; Raj evaluates", daysAgo: 1, text: "# Northstar\n\n- Anita Rao is the buyer and signs. Raj is the technical evaluator: send him the detail, send her the decision.\n- Procurement deadline is Friday.\n" },
        { path: "/memory/security-documents.md", gloss: "Only the approved overview goes out", daysAgo: 5, text: "# Security documents\n\n- Send only the approved security overview. Anything else goes to Priya first.\n" },
        { path: "/memory/follow-ups.md", gloss: "Read colleagues’ threads before chasing", daysAgo: 12, text: "# Follow-ups\n\n- Check what colleagues said to a buyer this week before proposing another follow-up.\n" },
        { path: "/me/agents/pod-default/your-preferences.md", gloss: "Short drafts; you send them", daysAgo: 8, text: "# Your preferences\n\n- Keep drafts under five lines. You send them yourself.\n- Tell me on Slack, not email.\n" },
    ];
    return [{ path: "/memory/working-notes.md", gloss: teammateFor(id).learned, daysAgo: 6, text: "# Working notes\n\n" + teammateFor(id).learned + "\n" }];
}

/** A correction and the note it becomes, in a teammate's sample
 *  conversation: somebody tells it how things work now, it says so, and the
 *  write under the reply is what draws "Remy noted this". Remy's is
 *  specific; the others reuse their first note. Kit has a conversation of
 *  its own, below. */
function notedIn(id: string) {
    const note = notesOf(id)[0];
    const told = id === "remy"
        ? { ask: "Raj isn’t the buyer at Northstar. Anita signs; Raj only evaluates.", reply: "Thanks. I’ll send Raj the technical detail and bring decisions to Anita." }
        : { ask: "Keep this in mind for next time: " + note.gloss.charAt(0).toLowerCase() + note.gloss.slice(1) + ".", reply: "Noted. I’ll work that way from now on." };
    return [
        { id: id + "-told", role: "user", kind: "TEXT", sequence: 4, text: told.ask },
        { id: id + "-ack", role: "assistant", kind: "TEXT", sequence: 5, text: told.reply },
        { id: id + "-note", role: "assistant", kind: "TOOL_CALL", sequence: 6, tool_name: "pod_write_file", tool_call_id: id + "-note-call",
            tool_args: { path: note.path, description: note.gloss, content: note.text, overwrite: true } },
        { id: id + "-note-back", role: "assistant", kind: "TOOL_RETURN", sequence: 7, tool_call_id: id + "-note-call",
            tool_result: { success: true, path: note.path, created: false } },
    ];
}

/** Kit's conversation, the way the loop got built: one ask that leaves a
 *  space behind it (tables, two workflows, a nightly run, a page, the app),
 *  what happened overnight while nobody was here, and a correction that
 *  becomes a rule. Every part is a real message the thread draws: the work
 *  folds into steps, widgets and resource cards are `display_resource`, and
 *  "Kit noted this" is drawn from the note it writes. */
function kitConversation() {
    let sequence = 0;
    /* When each message happened: the ask yesterday afternoon took seven
       minutes of work; this morning's two came minutes apart. */
    let clock = Date.now() - 18 * 3_600_000;
    const tick = (minutes: number) => new Date(clock += minutes * 60_000).toISOString();
    const said = (role: "user" | "assistant", text: string, minutes = 0.2) => ({ id: "kit-" + ++sequence, role, kind: "TEXT", sequence, text, created_at: tick(minutes) });
    const thought = (text: string) => ({ id: "kit-" + ++sequence, role: "assistant", kind: "THINKING", sequence, text, created_at: tick(0.1) });
    const did = (tool_name: string, tool_args: Record<string, unknown>, tool_result: unknown = { success: true }, minutes = 0.6) => {
        const call = "kit-call-" + ++sequence;
        return [
            { id: call, role: "assistant", kind: "TOOL_CALL", sequence, tool_name, tool_call_id: call, tool_args, created_at: tick(0.05) },
            { id: call + "-back", role: "assistant", kind: "TOOL_RETURN", sequence: ++sequence, tool_call_id: call, tool_result, created_at: tick(minutes) },
        ];
    };
    const show = (tool_args: Record<string, unknown>) => ({ id: "kit-" + ++sequence, role: "assistant", kind: "TOOL_CALL", sequence, tool_name: "display_resource", tool_args, created_at: tick(0.05) });
    const rules = notesOf("kit")[0];
    const later = (hours: number) => { clock = Date.now() - hours * 3_600_000; return []; };
    return [
        said("user", teammateFor("kit").ask, 0),
        ...did("run_connector_operation", { comment: "Reading the last 30 days of #feedback", connector: "slack", operation: "conversations.history", channel: "#feedback" }, { success: true, messages: 214 }, 1.4),
        ...did("exec_command", { comment: "A table for every report, and one for the themes they fall into", command: "lemma tables create feedback_reports feedback_themes" }),
        ...did("pod_write_record", { comment: "Filed 214 reports under 9 themes", table: "feedback_reports", records: 214 }, { success: true }, 1.6),
        ...did("pod_query", { comment: "Linked 6 themes to their tickets, 4 tickets to merged PRs", query: "SELECT theme, ticket, pr FROM feedback_themes" }),
        ...did("exec_command", { comment: "Capture: runs on every new #feedback message", command: "lemma workflows create capture-feedback --on slack.message_posted" }, { success: true }, 0.4),
        ...did("exec_command", { comment: "Close the loop: runs when a PR merges", command: "lemma workflows create close-the-loop --on github.pull_request_merged" }, { success: true }, 0.4),
        ...did("exec_command", { comment: "Nightly reconcile, every day at 02:00", command: "lemma schedules create nightly-reconcile --cron '0 2 * * *'" }, { success: true }, 0.2),
        ...did("pod_write_file", { comment: "This week’s report", path: "/pages/Feedback report, week 40.md" }),
        ...did("exec_command", { comment: "Built Feedback loop and shared it with Dev, Sam and Alex", command: "lemma apps deploy feedback-loop" }, { success: true }, 1.2),
        said("assistant", teammateFor("kit").reply),
        show({ type: "WIDGET", content: kitWidget("themes") }),
        show({ type: "APP", name: "Feedback loop" }),

        ...later(1.6),
        said("user", "What happened overnight?", 0),
        ...did("pod_query", { comment: "Read last night’s runs", query: "SELECT * FROM workflow_runs WHERE started_at > now() - interval '12 hours'" }, { success: true }, 0.2),
        said("assistant", "The nightly reconcile ran at 02:00 with nobody here. It filed 8 new reports and started one new theme, Upload spinner never ends (5). It looks like the import timeouts, so I suggested a merge in Feedback loop.\n\nDev merged PR #482 at 23:10. It fixes Dates import as text, so I drafted 22 retry replies, one in each thread, with the PR link. Sam’s rule: Enterprise accounts hear from their CSM, so 3 of the 22 went to Sam. The other 19 wait for Dev to confirm the fix is live."),
        show({ type: "WIDGET", content: kitWidget("replies") }),
        show({ type: "WORKFLOW", name: "close-the-loop" }),

        /* Eight messages, the sample pane's first page, so the correction
           opens on the ask that made it. */
        ...later(1.4),
        said("user", "Dev says “app feels stuck” on mobile is the missing push notification, not a timeout. Sort them that way. And timeouts go to Dev, top priority.", 0),
        thought("11 of the 14 “app feels stuck” reports came from the mobile app. The other 3 are uploads over 10k rows."),
        ...did("pod_write_record", { comment: "Moved 11 reports to Notifications on mobile, 3 to Large imports time out, now Dev’s at P0", table: "feedback_reports", records: 14 }, { success: true }, 0.3),
        said("assistant", "Done. 11 of the 14 were on mobile, so they’re under Notifications on mobile now. The other 3 are real timeouts and joined Large imports time out, which Dev owns as P0 with the 3 sample files people sent. I’ll sort “stuck” that way from now on."),
        show({ type: "WIDGET", content: kitWidget("resorted") }),
        ...did("pod_write_file", { path: rules.path, description: rules.gloss, content: rules.text, overwrite: true }, { success: true, path: rules.path, created: false }, 0.1),
    ];
}

/** Isolated fictional work; production and general QA fixtures stay separate. */
export const previewSource: PodSource = {
    ...fixtureSource,
    async listOrgs() { return [{ id: "acme", name: "Acme" }]; },
    async getPod(podId) { return (await previewSource.listPods("acme")).find(pod => pod.id === podId) ?? null; },
    async listPods(orgId) {
        return orgId === "acme" ? teammates.map(person => ({ id: person.id, orgId, name: person.name, iconUrl: faceOf(person), description: person.role, teammate: persona(person.id), subtitle: person.role, members: members(person.id), waiting: person.waiting })) : [];
    },
    async createPod(orgId, name, description) {
        const id = "sample-" + Date.now();
        const person = { ...teammates[0], id, name, role: description?.trim() || "New teammate", job: description ?? "Define my first responsibility with me.", promise: description ?? "Ready for my first responsibility.", waiting: "Ready to get started", ask: "What should we work on first?", reply: "This is a sample teammate. Give me a first responsibility to explore the setup.", learned: "No team guidance yet.", items: [], app: "Workspace" };
        teammates.push(person);
        faces.set(id, null);
        return { id, orgId, name, iconUrl: faceOf(person), description: person.role, teammate: persona(id), subtitle: person.role, members: members(id), waiting: person.waiting };
    },
    async setPodIcon(id, iconUrl) { faces.set(id, iconUrl); },
    async renamePod(id, name) { const person = teammates.find(item => item.id === id); if (person) person.name = name; },
    async describePod(id, description) { const person = teammates.find(item => item.id === id); if (person) person.role = description.trim() || person.role; },
    async deletePod(id) { const at = teammates.findIndex(item => item.id === id); if (at >= 0) teammates.splice(at, 1); },
    async listSurfaces(id) { const person = teammateFor(id); return [{ id: id + "-email", platform: "RESEND", name: "email", mine: true, agentName: person.name, handle: person.id + "@acme.example.invalid", email: person.id + "@acme.example.invalid", active: true }]; },
    /* The general sample has spent the org's shared WhatsApp number, to
       exercise that sentence. A visitor to the landing has not. */
    async listConnectable(id) {
        const all = await fixtureSource.listConnectable(id);
        return all.map(entry => entry.platform === "WHATSAPP" ? { ...entry, systemFree: true, claimedBy: undefined, effort: "instant" as const } : entry).sort(byEffort);
    },
    async listMySurfaces() { return teammates.map(person => ({ platform: "RESEND", podId: person.id, name: "email" })); },
    async chatPodChoice() { return null; },
    async setChatPod() { /* the landing preview has no chat number to point */ },
    async getPodDetail(id) { return { members: members(id), teammate: persona(id), subtitle: teammateFor(id).role }; },
    async listTabs(id) {
        const person = teammateFor(id);
        return [
            { id: "conversation", kind: "conversation", label: "Conversation" },
            { id: "app:launch", kind: "app", label: person.app, url: `/demo/launch?teammate=${person.id}`, status: "sample" },
            { id: "library", kind: "library", label: "Library" },
            { id: "profile", kind: "profile", label: "Profile" },
        ] satisfies Tab[];
    },
    async listConversations(id) { return [{ id: id + "-today", title: teammateFor(id).ask, at: "Today", kind: "CHAT" }]; },
    async listConversationsPage(id, _cursor, search) {
        const items = await previewSource.listConversations(id);
        const needle = search?.toLowerCase();
        return { items: needle ? items.filter((item) => item.title.toLowerCase().includes(needle)) : items, next: null };
    },
    async getConversation(id) {
        const person = teammateFor(id);
        if (id === "kit") return { id: id + "-today", title: person.ask, status: "COMPLETED", messages: kitConversation() } satisfies Conversation;
        return { id: id + "-today", title: person.ask, status: "COMPLETED", messages: [
            { id: id + "-1", role: "user", kind: "TEXT", sequence: 1, text: person.ask },
            { id: id + "-2", role: "assistant", kind: "TEXT", sequence: 2, text: person.reply },
            { id: id + "-widget", role: "assistant", kind: "TOOL_CALL", sequence: 3, tool_name: "display_resource", tool_args: { type: "WIDGET", content: conversationWidget(id) } },
            /* The note it keeps while it works, so the "noted" line under the
               reply is the real one, drawn from this write. */
            ...notedIn(id),
        ] } satisfies Conversation;
    },
    async listLibrary(id, kind, directory) {
        /* Each teammate keeps the tables and pages of its own job. */
        const space = spaceOf(id);
        if (kind === "tables") return { items: (space?.tables ?? []).map(table => ({ id: table.name, name: table.name, kind: "table" as const, path: table.name, updated: new Date(Date.now() - DAY).toISOString(), detail: table.detail, rls: space?.ownRows?.includes(table.name) })) };
        if (directory === "/pages") return { items: (space?.pages ?? []).map(page => ({ id: page.path, name: page.path.split("/").pop() ?? page.path, kind: "file" as const, path: page.path, updated: new Date(Date.now() - page.daysAgo * DAY).toISOString(), detail: page.detail })) };
        const notes = sampleListing(directory, notesOf(id).map(note => ({ path: note.path, updated: new Date(Date.now() - note.daysAgo * DAY).toISOString(), description: note.gloss })));
        if (directory === "/me" || directory === "/memory" || directory.startsWith("/memory/") || directory.startsWith("/me/agents")) return { items: notes };
        if (directory === "/skills") return { items: [{ id: "brand-voice", name: "brand-voice", kind: "folder", path: "/skills/brand-voice", updated: "2026-09-23T09:00:00Z", detail: `What the team taught ${teammateFor(id).name}` }] };
        /* The root holds folders, as a real space's does; the guidance
           lives in its skill's folder, not at the top as if it were a page. */
        if (directory === "/") return { items: [...notes, ...["pages", "skills"].map(name => ({ id: name, name, kind: "folder" as const, path: "/" + name, updated: "2026-09-23T09:00:00Z", detail: "" }))] };
        return { items: [{ id: "guidance", name: "SKILL.md", kind: "file", path: voicePath, updated: "2026-09-23T09:00:00Z", detail: teammateFor(id).learned }] };
    },
    async readFile(id, path) {
        const page = samplePageIn(id, path);
        if (page) { const text = edits.get(id + path) ?? page.text; return { name: path.split("/").pop() ?? path, path, mime: "text/markdown", size: text.length, kind: "markdown", text }; }
        const note = notesOf(id).find(one => one.path === path);
        if (note) { const text = edits.get(id + path) ?? note.text; return { name: path.split("/").pop() ?? path, path, mime: "text/markdown", size: text.length, kind: "markdown", text }; }
        const text = edits.get(id + path) ?? guidance(id);
        return { name: "SKILL.md", path, mime: "text/markdown", size: text.length, kind: "markdown", text };
    },
    async writeFile(id, path, text) { edits.set(id + path, text); },
    async getProfile(id) {
        const person = teammateFor(id);
        const profile = await fixtureSource.getProfile(id);
        return { ...profile, podId: id, name: person.name, iconUrl: faceOf(person), headline: person.promise, about: person.job + "\n\n" + person.learned, commitments: [], counts: { tables: spaceOf(id)?.tables.length ?? 0, functions: 0, workflows: SAMPLE_WORKFLOWS.filter(flow => flow.pod_id === id).length }, projects: [{ id: person.id, name: person.app, description: person.role, status: "running", tabId: "app:launch" }] };
    },
    async tableColumns(id, name) { return sampleTableIn(id, name)?.columns ?? []; },
    async tableRows(id, name) { return { items: sampleTableIn(id, name)?.rows() ?? [], next: null }; },
    async tableCount(id, name) { return sampleTableIn(id, name)?.rows().length ?? null; },
    async tableRecord(id, name, recordId) { return sampleTableIn(id, name)?.rows().find(row => String(row.id) === recordId) ?? null; },
    async tableShape(id, name) { const table = sampleTableIn(id, name); return table ? sampleShape(table) : null; },
    async tableShapes(id) { return (spaceOf(id)?.tables ?? []).map(sampleShape); },
    async referencing(id, table, column, recordId, limit) { return (sampleTableIn(id, table)?.rows() ?? []).filter(row => String(row[column]) === recordId).slice(0, limit); },
    async runQuery(id) { return { items: spaceOf(id)?.query() ?? [], truncated: false }; },
    async listAgents() { return []; },
    async listSchedules(id) { return readSchedules(spaceOf(id)?.schedules ?? []); },
};
