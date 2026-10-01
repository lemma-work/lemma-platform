import { conversationWidget } from "./conversation-widgets";
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
    { id: "dev", label: "dev@acme.test", orgRole: "admin" },
];

export function addPreviewMember(podId: string, id: string, role: string): void {
    const members = added.get(podId) ?? [];
    const candidate = previewCandidates.find(person => person.id === id);
    if (!candidate || members.some(person => person.id === id)) return;
    added.set(podId, [...members, { id, name: candidate.label, initials: id.slice(0, 2).toUpperCase(), kind: "person", role, can: role }]);
}

const voicePath = "/skills/brand-voice/SKILL.md";
const edits = new Map<string, string>();
function members(id: string): Member[] {
    const person = teammateFor(id);
    return [
        { id: "you", name: "You", initials: "YO", kind: "person", role: "Owner", can: "everything", userId: "sample-user" },
        { id: "priya", name: "Priya", initials: "PR", kind: "person", role: "Member", can: "reviews external commitments", userId: "priya-user" },
        /* June's imports wait on Dev; nobody else's work does. */
        ...(id === "june" ? [{ id: "dev", name: "Dev", initials: "DE", kind: "person" as const, role: "Member", can: "approves customer data fixes", userId: "dev-user" }] : []),
        { id: person.id, name: person.name, initials: person.name.slice(0, 2).toUpperCase(), kind: "teammate", role: person.role, can: "prepares work · asks before sending" },
        ...(added.get(id) ?? []),
    ];
}
function persona(id: string) {
    const person = teammateFor(id);
    return { name: person.name, initials: person.name.slice(0, 2).toUpperCase(), iconUrl: person.icon };
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
        { path: "/memory/launch-checks.md", gloss: "Readiness runs Thursdays at 9", daysAgo: 2, text: "# Launch checks\n\n- Readiness check runs Thursdays at 09:00 (moved from Fridays after the September launch).\n- A launch is not ready until the demo matches the current onboarding.\n" },
        { path: "/memory/publishing.md", gloss: "Anything public goes to Priya first", daysAgo: 4, text: "# Publishing\n\n- Anything public is prepared and brought to Priya. Kit never publishes on its own.\n- Customer names need written permission first.\n" },
        { path: "/memory/brand-voice.md", gloss: "Lead with the customer’s problem", daysAgo: 20, text: "# Brand voice\n\n" + teammateFor(id).learned + "\n" },
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
 *  write under the reply is what draws "Kit noted this". Kit's is specific
 *  because the landing shows it; the others reuse their first note. */
function notedIn(id: string) {
    const note = notesOf(id)[0];
    const told = id === "kit"
        ? { ask: "From now on the readiness check runs Thursdays at 9, not Fridays.", reply: "Got it. Readiness checks run Thursdays at 09:00 from now on." }
        : id === "remy"
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

/** Isolated fictional work; production and general QA fixtures stay separate. */
export const previewSource: PodSource = {
    ...fixtureSource,
    async listOrgs() { return [{ id: "acme", name: "Acme" }]; },
    async getPod(podId) { return (await previewSource.listPods("acme")).find(pod => pod.id === podId) ?? null; },
    async listPods(orgId) {
        return orgId === "acme" ? teammates.map(person => ({ id: person.id, orgId, name: person.name, iconUrl: person.icon, description: person.role, teammate: persona(person.id), subtitle: person.role, members: members(person.id), waiting: person.waiting })) : [];
    },
    async createPod(orgId, name, description) {
        const id = "sample-" + Date.now();
        const person = { ...teammates[0], id, name, role: description?.trim() || "New teammate", job: description ?? "Define my first responsibility with me.", promise: description ?? "Ready for my first responsibility.", waiting: "Ready to get started", ask: "What should we work on first?", reply: "This is a sample teammate. Give me a first responsibility to explore the setup.", learned: "No team guidance yet.", items: [], app: "Workspace" };
        teammates.push(person);
        return { id, orgId, name, iconUrl: person.icon, description: person.role, teammate: persona(id), subtitle: person.role, members: members(id), waiting: person.waiting };
    },
    async renamePod(id, name) { const person = teammates.find(item => item.id === id); if (person) person.name = name; },
    async describePod(id, description) { const person = teammates.find(item => item.id === id); if (person) person.role = description.trim() || person.role; },
    async listSurfaces(id) { const person = teammateFor(id); return [{ id: id + "-email", platform: "RESEND", name: "email", mine: true, agentName: person.name, handle: person.id + "@acme.example.invalid", email: person.id + "@acme.example.invalid", active: true }]; },
    /* The general sample has spent the org's shared WhatsApp number, to
       exercise that sentence. A visitor to the landing has not. */
    async listConnectable(id) {
        const all = await fixtureSource.listConnectable(id);
        return all.map(entry => entry.platform === "WHATSAPP" ? { ...entry, systemFree: true, claimedBy: undefined, effort: "instant" as const } : entry).sort(byEffort);
    },
    async listMySurfaces() { return teammates.map(person => ({ platform: "RESEND", podId: person.id, name: "email" })); },
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
        return { ...profile, podId: id, name: person.name, iconUrl: person.icon, headline: person.promise, about: person.job + "\n\n" + person.learned, commitments: [], counts: { tables: spaceOf(id)?.tables.length ?? 0, functions: 0, workflows: SAMPLE_WORKFLOWS.filter(flow => flow.pod_id === id).length }, projects: [{ id: person.id, name: person.app, description: person.role, status: "running", tabId: "app:launch" }] };
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
