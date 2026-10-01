import { conversationWidget } from "./conversation-widgets";
import { sampleListing } from "@/thread/memory-notes";
import { teammates, teammateFor } from "./teammates";
import { fixtureSource } from "@/data/fixtures";
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
        { id: "you", name: "You", initials: "YO", kind: "person", role: "Owner", can: "everything" },
        { id: "priya", name: "Priya", initials: "PR", kind: "person", role: "Member", can: "reviews external commitments" },
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
    async deletePod(id) { const at = teammates.findIndex(item => item.id === id); if (at >= 0) teammates.splice(at, 1); },
    async listSurfaces(id) { const person = teammateFor(id); return [{ id: id + "-email", platform: "RESEND", name: "email", mine: true, agentName: person.name, handle: person.id + "@acme.example.invalid", email: person.id + "@acme.example.invalid", active: true }]; },
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
        if (kind === "tables") return { items: [] };
        const notes = sampleListing(directory, notesOf(id).map(note => ({ path: note.path, updated: new Date(Date.now() - note.daysAgo * DAY).toISOString(), description: note.gloss })));
        if (directory === "/me" || directory === "/memory" || directory.startsWith("/memory/") || directory.startsWith("/me/agents")) return { items: notes };
        if (directory === "/skills") return { items: [{ id: "brand-voice", name: "brand-voice", kind: "folder", path: "/skills/brand-voice", updated: "2026-09-23T09:00:00Z", detail: `What the team taught ${teammateFor(id).name}` }] };
        return { items: [...(directory === "/" ? notes : []), { id: "guidance", name: "SKILL.md", kind: "file", path: voicePath, updated: "2026-09-23T09:00:00Z", detail: teammateFor(id).learned }] };
    },
    async readFile(id, path) {
        const note = notesOf(id).find(one => one.path === path);
        if (note) { const text = edits.get(id + path) ?? note.text; return { name: path.split("/").pop() ?? path, path, mime: "text/markdown", size: text.length, kind: "markdown", text }; }
        const text = edits.get(id + path) ?? guidance(id);
        return { name: "SKILL.md", path, mime: "text/markdown", size: text.length, kind: "markdown", text };
    },
    async writeFile(id, path, text) { edits.set(id + path, text); },
    async getProfile(id) {
        const person = teammateFor(id);
        const profile = await fixtureSource.getProfile(id);
        return { ...profile, podId: id, name: person.name, iconUrl: person.icon, headline: person.promise, about: person.job + "\n\n" + person.learned, commitments: [], counts: { tables: 0, functions: 0, workflows: 0 }, projects: [{ id: person.id, name: person.app, description: person.role, status: "running", tabId: "app:launch" }] };
    },
    async listAgents() { return []; },
    async listSchedules() { return []; },
};
