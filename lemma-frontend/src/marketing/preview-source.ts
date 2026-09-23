import { conversationWidget } from "./conversation-widgets";
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
    return `---\nname: working-guidance\ndescription: How ${person.name} works with the team.\n---\n\n# ${person.name} · Working guidance\n\n${person.job}\n\n## What the team taught me\n\n${person.learned}\n`;
}

/** Isolated fictional work; production and general QA fixtures stay separate. */
export const previewSource: PodSource = {
    ...fixtureSource,
    async listOrgs() { return [{ id: "acme", name: "Acme" }]; },
    async listPods(orgId) {
        return orgId === "acme" ? teammates.map(person => ({ id: person.id, orgId, name: person.name, iconUrl: person.icon, teammate: persona(person.id), subtitle: person.role, members: members(person.id), waiting: person.waiting })) : [];
    },
    async createPod(orgId, name, description) {
        const id = "sample-" + Date.now();
        const person = { ...teammates[0], id, name, role: "New teammate", job: description ?? "Define my first responsibility with me.", promise: description ?? "Ready for my first responsibility.", waiting: "Ready to get started", ask: "What should we work on first?", reply: "This is a sample teammate. Give me a first responsibility to explore the setup.", learned: "No team guidance yet.", items: [], app: "Workspace" };
        teammates.push(person);
        return { id, orgId, name, iconUrl: person.icon, teammate: persona(id), subtitle: person.role, members: members(id), waiting: person.waiting };
    },
    async renamePod(id, name) { const person = teammates.find(item => item.id === id); if (person) person.name = name; },
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
    async getConversation(id) {
        const person = teammateFor(id);
        return { id: id + "-today", title: person.ask, status: "COMPLETED", messages: [
            { id: id + "-1", role: "user", kind: "TEXT", sequence: 1, text: person.ask },
            { id: id + "-2", role: "assistant", kind: "TEXT", sequence: 2, text: person.reply },
            { id: id + "-widget", role: "assistant", kind: "TOOL_CALL", sequence: 3, tool_name: "display_resource", tool_args: { type: "WIDGET", content: conversationWidget(id) } },
        ] } satisfies Conversation;
    },
    async listLibrary(id, kind, directory) {
        if (kind === "tables") return { items: [] };
        if (directory === "/skills") return { items: [{ id: "brand-voice", name: "brand-voice", kind: "folder", path: "/skills/brand-voice", updated: "2026-09-23T09:00:00Z", detail: `What the team taught ${teammateFor(id).name}` }] };
        return { items: [{ id: "guidance", name: "SKILL.md", kind: "file", path: voicePath, updated: "2026-09-23T09:00:00Z", detail: teammateFor(id).learned }] };
    },
    async readFile(id, path) { const text = edits.get(id + path) ?? guidance(id); return { name: "SKILL.md", path, mime: "text/markdown", size: text.length, kind: "markdown", text }; },
    async writeFile(id, path, text) { edits.set(id + path, text); },
    async getProfile(id) {
        const person = teammateFor(id);
        const profile = await fixtureSource.getProfile(id);
        return { ...profile, podId: id, name: person.name, iconUrl: person.icon, headline: person.promise, about: person.job + "\n\n" + person.learned, commitments: [], counts: { tables: 0, functions: 0, workflows: 0 }, projects: [{ id: person.id, name: person.app, description: person.role, status: "running", tabId: "app:launch" }] };
    },
    async listAgents() { return []; },
    async listSchedules() { return []; },
};
