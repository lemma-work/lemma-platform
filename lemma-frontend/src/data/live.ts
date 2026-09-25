import { askApi, lemma } from "@/session/client";
import { RESOURCE_KEY } from "@/thread/resource-conversation";
import { NEW_CONVERSATION } from "./types";
import { displayAgentName, initialsOf, isPodDefaultAgent } from "./agent-names";
import {
    agentChanges,
    agentRows,
    readAgentDetail,
    type AgentDetail,
    type AgentDraft,
    type AgentRow,
} from "./agents";
import {
    createRequest,
    readRun,
    readRuns,
    readSchedule,
    readSchedules,
    type ScheduleDraft,
    type ScheduleRun,
    type StandingJob,
    type TargetChoice,
} from "@/schedule/schedules";
import { capabilityFor, grantedToolsets } from "@/stage/colleagues";
import { byEffort, readConnectable, type Connectable } from "./connectable";
import { joinWire, orgJoinWire, readJoin, readJoinRequest, readOrgJoin, type JoinPolicy, type JoinRequest, type OrgJoin } from "./joining";

/** The join policy as the SDK types it: a generated enum it does not
 *  re-export, so it is read off the method that wants it. The three values
 *  themselves are spelled once, in `joining.ts`. */
type JoinWire = NonNullable<
    NonNullable<Parameters<ReturnType<typeof lemma>["pods"]["update"]>[1]["config"]>["join_policy"]
>;
import { bestAccount, readAccount, readConnector, type Connector, type ConnectorAccount } from "./accounts";
import {
    readChoice,
    readComputer,
    readLocalAgent,
    readRuntime,
    type Choice,
    type Computer,
    type LocalAgent,
    type Runtime,
} from "./runtimes";
import type {
    AccountConnect,
    Conversation,
    ConversationRef,
    Commitment,
    Profile,
    Project,
    Skill,
    Invitation,
    Member,
    Message,
    NewOrg,
    Org,
    Persona,
    Pod,
    PodDetail,
    PodSource,
    FileContent,
    GuidedSetup,
    SharedLink,
    Surface,
    Tab,
} from "./types";

/** The real source. An org is an organization, a teammate is a pod, the tabs are
 *  that pod's apps, and the profile is assembled from namespaces that already
 *  exist. Nothing here invents backend state — where the plan records a gap,
 *  this source shows less rather than making something up. */

type Listish = { items?: unknown[] } | unknown[];

function itemsOf(value: Listish): unknown[] {
    return Array.isArray(value) ? value : (value.items ?? []);
}

function clock(iso?: string | null): string {
    if (!iso) return "";
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function dayOf(iso?: string | null): string {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    if (sameDay) return "Today";
    return date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

async function membersOf(podId: string): Promise<Member[]> {
    try {
        const listed = (await lemma(podId).podMembers.list(podId)) as Listish;
        return itemsOf(listed).map((raw) => {
            const m = raw as { id?: string; user_id?: string; name?: string; email?: string; role?: string };
            const name = m.name ?? m.email ?? "Member";
            const role = m.role ?? "MEMBER";
            return {
                id: m.id ?? m.user_id ?? name,
                name,
                initials: initialsOf(name),
                kind: "person" as const,
                role: role.charAt(0) + role.slice(1).toLowerCase(),
                can: role === "OWNER" ? "everything" : "—",
            };
        });
    } catch {
        return [];
    }
}

function subtitleFor(members: Member[], email: string | null): string {
    const others = members.filter((m) => m.name !== email).map((m) => m.name.replace(/@.*$/, ""));
    if (others.length === 0) return "just you";
    if (others.length === 1) return "with " + others[0] + " and you";
    if (others.length === 2) return "with " + others[0] + ", " + others[1] + " and you";
    return "with " + others.slice(0, 2).join(", ") + " and " + (others.length - 2) + " others";
}

function shortOrgName(name: string): string {
    const personal = name.match(/^(.+?)'s Personal$/);
    if (!personal) return name;
    return personal[1].replace(/@.*$/, "") + " · personal";
}

/** A pod's agents, fetched once for everyone who wants them.
 *
 *  Five places asked for this list and each picked its own limit — 20, 25,
 *  100 — so three of them fired on a single screen and not one could reuse
 *  another's answer: a different limit is a different URL, which is below the
 *  level any cache above it can see. One limit and one in-flight promise per
 *  pod collapses them into a single request.
 *
 *  Held briefly rather than indefinitely. Agents change when somebody edits
 *  one, which is rare and always somewhere else, so half a minute is long
 *  enough to cover a screen assembling itself and short enough that nobody
 *  meets a stale roster.
 */
const AGENTS_HELD_MS = 30_000;
const agentPages = new Map<string, { at: number; page: Promise<Listish> }>();

/** One pod's own row, held just long enough for a page to assemble.
 *
 *  Two settings on the profile are stored in the same `config` blob — what it
 *  runs on and who may join it — and each was reading the pod to find its own
 *  field, so opening a teammate spent two identical GETs. Five seconds is
 *  shorter than the agents hold on purpose: this one backs controls somebody
 *  is changing, and every write drops it rather than waiting the window out.
 */
const POD_HELD_MS = 5_000;
const podRows = new Map<string, { at: number; row: Promise<{ config?: unknown; name?: string }> }>();

function podRow(podId: string): Promise<{ config?: unknown; name?: string }> {
    const held = podRows.get(podId);
    if (held && Date.now() - held.at < POD_HELD_MS) return held.row;
    const row = lemma(podId).pods.get(podId).catch((problem) => {
        podRows.delete(podId);
        throw problem;
    }) as Promise<{ config?: unknown; name?: string }>;
    podRows.set(podId, { at: Date.now(), row });
    return row;
}

/** Anything that writes to a pod invalidates what was read of it. */
function forgetPod(podId: string): void {
    podRows.delete(podId);
}

export function podAgents(podId: string): Promise<Listish> {
    const held = agentPages.get(podId);
    if (held && Date.now() - held.at < AGENTS_HELD_MS) return held.page;
    /* A rejection is dropped rather than held: a cached failure would make one
       bad moment last for the whole window, and every caller here already has
       its own answer for not being able to read the list. */
    const page = (lemma(podId).agents.list({ limit: 100 }) as Promise<Listish>).catch((problem) => {
        agentPages.delete(podId);
        throw problem;
    });
    agentPages.set(podId, { at: Date.now(), page });
    return page;
}

/** The agent that answers in a pod. A pod usually has one front agent; when
 *  it has none we still need something to attribute a reply to, and the pod's
 *  own name is a better guess than the product's. */
async function teammateOf(podId: string, podName: string, podIcon: string | null): Promise<Persona> {
    /* The pod is the employee. It answers under its own name and wears its own
       icon — "Lem" was the platform's name for a pod's default responder, and
       in a roster of teammates that made every one of them the same person. */
    const persona: Persona = { name: podName, initials: initialsOf(podName), iconUrl: podIcon };
    try {
        const listed = await podAgents(podId);
        const agents = itemsOf(listed).map(
            (raw) => raw as { name?: string; kind?: string; icon_url?: string | null },
        );
        const front = agents.find((agent) => isPodDefaultAgent(agent.name, agent.kind));
        /* Only the picture is taken from the agent, and only when the pod has
           none of its own. */
        if (front?.icon_url && !podIcon) return { ...persona, iconUrl: front.icon_url };
    } catch {
        /* a pod whose agents you may not list still answers */
    }
    return persona;
}

/* Every conversation listed here belongs to the pod's default agent.
   `list()` unfiltered returns the whole pod — sub-agent runs, another
   agent's tasks, work nobody had a conversation about — and putting that in
   a history panel makes it look like you said things you never said. */
/** Big enough that downloading it into the page is the wrong move. Images and
 *  PDFs are streamed by the browser from an object URL, so only text has to
 *  fit in memory to be shown. */
/** A toolset is a bundle of tools the agent may reach for. `WORKSPACE_CLI` is
 *  not a skill anybody would claim; "works a computer" is the same fact in a
 *  form a person recognises.
 *
 *  The naming itself lives in `stage/colleagues.ts`, with the one the agent
 *  rows already use. There were two tables — a `SKILLS` record here and
 *  `TOOLSET_WORDS` there — describing the same twelve codes in two different
 *  vocabularies, so this pod's own toolsets and its sub-agents' were spelled
 *  differently on one page. They are one table now, read twice. */
function toSkill(value: unknown): Skill | null {
    const id = String(value ?? "");
    if (!id) return null;
    const capability = capabilityFor(id);
    return { id: capability.code, label: capability.word, blurb: capability.says };
}

/** `daily-competitor-refresh` is a filename; "Daily competitor refresh" is a
 *  title. Agents name things the first way. */
function humanizeName(raw: string): string {
    const words = raw.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
    return words ? words[0].toUpperCase() + words.slice(1) : raw;
}


function podSummary(pod: { id: string; name: string; organization_id: string; icon_url?: string | null }): Pod {
    const name = humanizeName(pod.name);
    return { id: pod.id, orgId: pod.organization_id, name, iconUrl: pod.icon_url ?? null,
        teammate: { name, initials: initialsOf(name), iconUrl: pod.icon_url ?? null },
        subtitle: "", members: [], waiting: "" };
}


const INLINE_TEXT_LIMIT = 512 * 1024;
/** Html gets its own, and a much larger one. The other two are read as prose
 *  and as a `<pre>`, where half a megabyte is already past the point anybody
 *  is reading; an html file is a page, and a generated one carries its whole
 *  stylesheet inline. Holding it to the prose ceiling is what sent a real
 *  preview down the fallback path in the first place. */
const INLINE_HTML_LIMIT = 4 * 1024 * 1024;

function readableSize(bytes: number): string {
    if (!bytes) return "unknown size";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function fileKind(mime: string, path: string): FileContent["kind"] {
    const ext = (path.split(".").pop() ?? "").toLowerCase();
    if (mime.startsWith("image/")) return "image";
    /* Read the extension as well as the mime: a file uploaded without one
       arrives as application/octet-stream, and an mp4 that says "binary" was
       being drawn as a row about its own size instead of played. */
    if (mime.startsWith("video/") || ["mp4", "webm", "mov", "m4v", "ogv"].includes(ext)) return "video";
    if (mime.startsWith("audio/") || ["mp3", "wav", "m4a", "aac", "ogg", "oga", "flac", "opus"].includes(ext)) return "audio";
    if (mime === "application/pdf" || ext === "pdf") return "pdf";
    if (mime.includes("html") || ext === "html" || ext === "htm") return "html";
    if (mime.includes("markdown") || ext === "md" || ext === "markdown") return "markdown";
    if (mime.startsWith("text/") || ["txt", "csv", "json", "yaml", "yml", "log"].includes(ext)) return "text";
    return "binary";
}


/** A freshly created surface, in the shape the strip renders. */
function asSurface(raw: unknown, platform: string): Surface {
    const made = (raw ?? {}) as {
        id?: string;
        name?: string;
        platform?: string;
        reach?: { handle?: string | null; email?: string | null } | null;
        surface_identity_username?: string | null;
        surface_identity_email?: string | null;
    };
    return {
        id: String(made.id ?? ""),
        platform: String(made.platform ?? platform),
        name: String(made.name ?? platform.toLowerCase()),
        mine: true,
        agentName: "",
        handle: made.reach?.handle ?? made.surface_identity_username ?? made.surface_identity_email ?? "",
        email: made.reach?.email ?? made.surface_identity_email ?? undefined,
        active: true,
    };
}

/** The managed-bot setup, in the shape the sheet renders. */
function asGuided(raw: unknown): GuidedSetup {
    const answer = (raw ?? {}) as {
        setup_id?: string;
        launch_url?: string;
        manager_bot_username?: string;
        status?: string;
        bot_username?: string | null;
        bot_launch_url?: string | null;
        expires_at?: string;
        error?: string | null;
    };
    return {
        setupId: String(answer.setup_id ?? ""),
        launchUrl: String(answer.launch_url ?? ""),
        managerBot: String(answer.manager_bot_username ?? ""),
        status: String(answer.status ?? ""),
        botUsername: answer.bot_username ?? undefined,
        botLaunchUrl: answer.bot_launch_url ?? undefined,
        expiresAt: String(answer.expires_at ?? ""),
        error: answer.error ?? undefined,
    };
}

export const liveSource: PodSource = {
    label: "live",

    async listOrgs(): Promise<Org[]> {
        const listed = (await lemma().organizations.list()) as Listish;
        return itemsOf(listed)
            .map((raw) => raw as { id?: string; name?: string })
            .filter((o): o is { id: string; name: string } => Boolean(o.id && o.name))
            .map((o) => ({ id: o.id, name: shortOrgName(o.name) }));
    },

    /** Invitations addressed to whoever is signed in.
     *
     *  `PENDING` explicitly. The endpoint defaults to it, but an arrival screen
     *  that quietly started offering expired and revoked invitations because a
     *  default moved is a bad way to find out the default moved. */
    async myInvitations(): Promise<Invitation[]> {
        const listed = (await lemma().organizations.invitations.listMine({ status: "PENDING" as never, limit: 50 })) as Listish;
        return itemsOf(listed)
            .map((raw) => raw as {
                id?: string; organization_id?: string; organization_name?: string | null;
                pod_id?: string | null; pod_name?: string | null; pod_description?: string | null;
                role?: string;
            })
            .filter((invite): invite is { id: string; organization_id: string } & typeof invite =>
                Boolean(invite.id && invite.organization_id))
            .map((invite) => ({
                id: invite.id,
                orgId: invite.organization_id as string,
                /* An organization that did not send its name is still an
                   organization somebody can accept, and "an organization" is a
                   truer label for it than a blank. */
                orgName: shortOrgName(invite.organization_name ?? "") || "an organization",
                podId: invite.pod_id ?? null,
                podName: invite.pod_name ?? null,
                podAbout: invite.pod_description ?? null,
                role: invite.role ?? "ORG_MEMBER",
            }));
    },

    async acceptInvitation(invitationId: string): Promise<void> {
        await lemma().organizations.invitations.accept(invitationId);
    },

    /** Organizations this person's email domain may walk into.
     *
     *  Not on `OrganizationsNamespace` — see `askApi`. Empty is the ordinary
     *  answer, and so is a failure: somebody's first screen should not become
     *  an error page because an optional suggestion could not be fetched, so
     *  this swallows the problem and offers nothing. The two rungs either side
     *  of it — an invitation, and making one — both still work. */
    async suggestedOrgs(): Promise<Org[]> {
        try {
            const listed = await askApi<Listish>("/organizations/suggested?limit=20");
            return itemsOf(listed)
                .map((raw) => raw as { id?: string; name?: string })
                .filter((o): o is { id: string; name: string } => Boolean(o.id && o.name))
                .map((o) => ({ id: o.id, name: shortOrgName(o.name) }));
        } catch {
            return [];
        }
    },

    async joinOrg(orgId: string): Promise<void> {
        await askApi<unknown>("/organizations/" + encodeURIComponent(orgId) + "/join", { method: "POST" });
    },

    /** Make one.
     *
     *  `email_domain` and the open policy are sent together or not at all —
     *  a domain with no policy admits nobody and a policy with no domain has
     *  nothing to match, and either half alone is a setting that looks set and
     *  does nothing. `org/arrival.ts` decides whether the pair is safe. */
    async createOrg(wanted: NewOrg): Promise<Org> {
        const made = (await lemma().organizations.create({
            name: wanted.name,
            resolve_name_conflicts: wanted.derived === true,
            ...(wanted.emailDomain
                ? { email_domain: wanted.emailDomain, join_policy: "EMAIL_DOMAIN" as never }
                : {}),
        })) as { id?: string; name?: string };
        if (!made?.id) throw new Error("The organization was made but came back without an id.");
        return { id: made.id, name: shortOrgName(made.name ?? wanted.name) };
    },

    /** One request, and deliberately not 2N. Fetching each pod's roster and
     *  responder here costs 23 requests and three seconds on an eleven-pod
     *  org before a single teammate appears; both are per-teammate reads,
     *  made when you open one. */
    async listPods(orgId: string): Promise<Pod[]> {
        const listed = (await lemma().pods.listByOrganization(orgId)) as Listish;
        return itemsOf(listed)
            .map((raw) => raw as { id?: string; name?: string; organization_id?: string; icon_url?: string | null })
            .filter((pod): pod is { id: string; name: string; organization_id?: string; icon_url?: string | null } =>
                Boolean(pod.id && pod.name),
            )
            .map((pod) => podSummary({ ...pod, organization_id: pod.organization_id ?? orgId }));
    },

    async getPod(podId: string): Promise<Pod> {
        return podSummary(await lemma().pods.get(podId));
    },

    async getPodDetail(podId: string, podName: string, podIcon?: string | null): Promise<PodDetail> {
        const [members, teammate] = await Promise.all([
            membersOf(podId),
            teammateOf(podId, podName, podIcon ?? null),
        ]);
        return { members, teammate, subtitle: subtitleFor(members, null) };
    },

    async listLibrary(podId, kind, directory, page) {
        const client = lemma(podId);
        if (kind === "tables") {
            const result = await client.tables.list({ limit: 50, pageToken: page });
            return { next: result.next_page_token, items: result.items.map(t => ({ id: t.id, name: t.name, kind: "table" as const, path: t.name, updated: t.updated_at, detail: `${t.column_count ?? "—"} columns` })) };
        }
        const result = await client.files.list({ directoryPath: directory, limit: 50, pageToken: page });
        return { next: result.next_page_token, items: result.items.map(f => ({ id: f.id, name: f.name, kind: /folder|directory/i.test(f.kind) ? "folder" as const : "file" as const, path: f.path, updated: f.updated_at, detail: f.description || f.mime_type || f.kind })) };
    },
    async tableColumns(podId, name) { return (await lemma(podId).tables.get(name)).columns; },
    /* Nothing binds parameters here — `datastore.query` takes SQL text and
       nothing else — so a table name goes into the string or not at all. It
       only goes in if it is an ordinary identifier; anything else returns
       `null`, which the caller already handles as "the count is unknown". */
    async tableRecord(podId, name, id) {
        return await lemma(podId).records.get(name, id) as Record<string, unknown>;
    },
    async tableShape(podId, name) { return await lemma(podId).tables.get(name); },
    async tableShapes(podId) {
        const listed = await lemma(podId).tables.list({ limit: 100 });
        const names = ((listed as { items?: { name: string }[] }).items ?? []).map(t => t.name);
        return Promise.all(names.map(async (name) => {
            try { return await lemma(podId).tables.get(name); }
            catch { return { name }; }
        }));
    },
    async referencing(podId, table, column, id, limit) {
        const page = await lemma(podId).records.list(table, {
            filters: [{ field: column, op: "eq", value: id }],
            limit,
        });
        return ((page as { items?: Record<string, unknown>[] }).items ?? []);
    },
    async tableCount(podId, name) {
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return null;
        try {
            const answer = await lemma(podId).datastore.query('SELECT count(*) AS n FROM "' + name + '"');
            const first = (answer.items ?? [])[0] as { n?: unknown } | undefined;
            const count = Number(first?.n);
            return Number.isFinite(count) ? count : null;
        } catch {
            /* A pod that will not run the query is not a broken table. The view
               loses the shape it could have chosen and keeps the grid. */
            return null;
        }
    },
    async tableRows(podId, name, page) {
        const result = await lemma(podId).records.list(name, { limit: 50, pageToken: page });
        return { items: result.items, next: result.next_page_token };
    },
    async runQuery(podId: string, sql: string) {
        const answer = await lemma(podId).datastore.query(sql);
        return { items: (answer.items ?? []) as Record<string, unknown>[], truncated: answer.truncated === true };
    },

    async listTabs(podId: string): Promise<Tab[]> {
        const tabs: Tab[] = [{ id: "conversation", kind: "conversation", label: "Conversation" }, { id: "apps", kind: "apps", label: "Apps" }];
        try {
            const listed = (await lemma(podId).apps.list({ limit: 12 })) as Listish;
            for (const raw of itemsOf(listed)) {
                const app = raw as { name?: string; url?: string; status?: string; description?: string };
                if (!app.name || !app.url) continue;
                tabs.push({
                    id: "app:" + app.name,
                    kind: "app",
                    label: app.name.replace(/[-_]/g, " "),
                    url: app.url,
                    status: app.status ?? "",
                });
            }
        } catch {
            /* a pod whose apps you may not list still has a conversation */
        }
        tabs.push({ id: "library", kind: "library", label: "Library" });
        /* Unconditional, unlike the apps above: a pod always has at least the
           agent answering you, and a tab that appeared only for pods with
           subagents would make "does this one delegate?" a question about
           whether the strip had loaded. The view says so itself when the list
           cannot be read. */
        tabs.push({ id: "profile", kind: "profile", label: "Profile" });
        return tabs;
    },

    async listSurfaces(podId: string): Promise<Surface[]> {
        const listed = (await lemma(podId).podSurfaces.list(podId, { limit: 30 })) as Listish;
        return itemsOf(listed)
            .map(
                (raw) =>
                    raw as {
                        id?: string;
                        platform?: string;
                        name?: string;
                        status?: string;
                        agent_name?: string | null;
                        uses_default_agent?: boolean | null;
                        surface_identity_username?: string | null;
                        surface_identity_email?: string | null;
                        reach?: { handle?: string | null; email?: string | null } | null;
                    },
            )
            .filter((surface) => Boolean(surface.id && surface.platform))
            .map((surface) => ({
                id: String(surface.id),
                platform: String(surface.platform),
                name: String(surface.name ?? surface.platform),
                mine: Boolean(surface.uses_default_agent) || isPodDefaultAgent(surface.agent_name),
                agentName: displayAgentName(String(surface.agent_name ?? "")),
                agentKey: surface.agent_name ?? undefined,
                handle:
                    surface.reach?.handle ??
                    surface.surface_identity_username ??
                    surface.surface_identity_email ??
                    "",
                email: surface.reach?.email ?? surface.surface_identity_email ?? undefined,
                active: (surface.status ?? "ACTIVE") === "ACTIVE",
                status: surface.status ?? "ACTIVE",
            }));
    },

    async getSurface(podId, name) { return lemma(podId).podSurfaces.get(podId, name); },
    async surfaceSetup(podId, name) { return lemma(podId).podSurfaces.setup(podId, name); },
    async surfaceGuide(podId, platform) { return lemma(podId).podSurfaces.setupGuide(podId, platform); },
    async surfaceChannels(podId, name) { return lemma(podId).podSurfaces.channels(podId, name); },
    async updateSurface(podId, name, patch) { await lemma(podId).podSurfaces.update(podId, name, patch); },
    async createSurfaceAccount(orgId, entry, credentials) {
        const client = lemma();
        const install = await client.connectors.enableApp(orgId, entry.connectorId, { kind: entry.kind });
        const account = await client.connectors.accounts.create(orgId, { auth_config_id: install.id, credentials });
        return account.id;
    },

    async listConnectable(podId: string): Promise<Connectable[]> {
        const answer = (await lemma(podId).podSurfaces.available(podId)) as { surfaces?: unknown[] };
        return (answer.surfaces ?? [])
            .map(readConnectable)
            .filter((entry): entry is Connectable => entry !== null)
            .sort(byEffort);
    },

    async connectSystem(podId: string, platform: string): Promise<Surface> {
        /* No account, no OAuth, no name: the surface name defaults to the
           lowercased platform, which is what the single-surface-per-platform
           case wants. The response already carries `reach`, so the address is
           on screen without a second read. */
        /* `platform` and `credential_mode` are generated enums the SDK does
           not re-export, so the payload is asserted once here rather than
           every caller carrying a cast. The values are checked against the
           catalog before this runs — a platform that did not come back from
           `available()` is never offered. */
        const payload = { platform, credential_mode: "SYSTEM" } as unknown as Parameters<
            ReturnType<typeof lemma>["podSurfaces"]["create"]
        >[1];
        return asSurface(await lemma(podId).podSurfaces.create(podId, payload), platform);
    },

    /* ── what a teammate runs on ──────────────────────────────────────
       Provider keys belong to the organization — one key is bought,
       billed and rotated once — while a coding agent belongs to the
       computer it runs on, which belongs to a person. Both come back
       from the same listing because both are the same object to
       whoever is picking one. */

    async listRuntimes(orgId: string): Promise<Runtime[]> {
        /* Disabled ones included: the ledger draws them behind a toggle,
           and without them a row that was archived would offer "Add"
           again and collide with the name already taken. */
        const listed = await lemma().agentRuntime.listProfiles(orgId, { includeDisabled: true });
        return itemsOf(listed as Listish)
            .map(readRuntime)
            .filter((entry): entry is Runtime => entry !== null);
    },

    async defaultRuntime(orgId: string): Promise<Choice | null> {
        const listed = await lemma().agentRuntime.listRuntimes(orgId);
        return readChoice(listed.default_runtime);
    },

    async listComputers(): Promise<Computer[]> {
        const listed = await lemma().agentHost.list();
        /* A revoked computer stays readable for audit and can never take
           work again, so it has no place in a "what can I use" list. */
        const computers = itemsOf(listed as Listish)
            .map(readComputer)
            .filter((entry): entry is Computer => entry !== null && entry.status !== "Removed");
        return Promise.all(
            computers.map(async (computer) => {
                try {
                    const harnesses = await lemma().agentHost.listHarnesses(computer.id);
                    return {
                        ...computer,
                        agents: itemsOf(harnesses as Listish)
                            .map(readLocalAgent)
                            .filter((agent): agent is LocalAgent => agent !== null),
                    };
                } catch {
                    /* A machine whose agents cannot be read is still a
                       machine, and saying so beats dropping it. */
                    return computer;
                }
            }),
        );
    },

    async addProviderKey(orgId, key): Promise<void> {
        const shared = {
            name: key.name,
            api_key: key.apiKey,
            default_model_name: key.models[0] ?? null,
            model_names: key.models,
        };
        await lemma().agentRuntime.createProfile(
            orgId,
            key.protocol === "anthropic"
                ? { ...shared, base_url: key.baseUrl || null }
                : { ...shared, base_url: key.baseUrl },
        );
    },

    async addLocalAgent(orgId, harnessId, agent): Promise<void> {
        /* `scope` is one of the SDK's unexported enums, so the literal has
           to be named as the parameter it is going into. */
        const payload = {
            source: "AGENT_HOST",
            harness_id: harnessId,
            name: agent.name,
            /* Personal unless you say otherwise. This points at a coding
               agent on one person's own machine, holding their
               credentials and seeing their files — sharing it hands that
               machine to the organization, and it should not be what you
               get by not reading the dialog. */
            scope: agent.shared ? "ORGANIZATION" : "PERSONAL",
            default_model_name: agent.model || null,
            /* Everything else stays as that computer has it. Lemma owns
               approvals and session handling, so the only setting worth
               asking about here is which model. */
            config_selections: {},
        } as unknown as Parameters<ReturnType<typeof lemma>["agentRuntime"]["createProfile"]>[1];
        await lemma().agentRuntime.createProfile(orgId, payload);
    },

    async archiveRuntime(orgId: string, runtimeId: string): Promise<void> {
        await lemma().agentRuntime.archiveProfile(orgId, runtimeId);
    },

    async restoreRuntime(orgId: string, runtimeId: string): Promise<void> {
        await lemma().agentRuntime.restoreProfile(orgId, runtimeId);
    },

    async getPodRuntime(podId: string): Promise<Choice | null> {
        const pod = await podRow(podId);
        const config = (pod.config ?? {}) as { default_runtime?: unknown; default_profile_id?: string | null };
        /* The stored runtime carries the model; `default_profile_id` is a
           legacy mirror written from it that never does. Reading the
           mirror first would name the runtime's *own* default model
           whenever somebody picked anything else. */
        return readChoice(config.default_runtime) ?? readChoice({ profile_id: config.default_profile_id });
    },

    async setPodRuntime(podId: string, choice: Choice | null): Promise<void> {
        /* Both keys, and the second one is not redundant. The config is
           merged field-wise, and the backend re-syncs the legacy
           `default_profile_id` from `default_runtime` only when there is a
           runtime to read a profile out of — so clearing just the runtime
           leaves the mirror pointing at the old profile, and the pod keeps
           resolving through it (`resolved_default_runtime` falls back to the
           mirror). "Inherit again" would have saved cleanly and changed
           nothing. */
        await lemma(podId).pods.update(podId, {
            config: choice
                ? { default_runtime: { profile_id: choice.runtimeId, model_name: choice.model || null } }
                : { default_runtime: null, default_profile_id: null },
        });
        forgetPod(podId);
    },

    async getPodJoin(podId: string): Promise<JoinPolicy> {
        const pod = await podRow(podId);
        return readJoin((pod.config as { join_policy?: unknown } | undefined)?.join_policy);
    },

    async getOrgJoin(orgId: string): Promise<OrgJoin> {
        const org = (await lemma().organizations.get(orgId)) as { join_policy?: unknown; email_domain?: unknown };
        return readOrgJoin(org.join_policy, org.email_domain);
    },

    async setOrgJoin(orgId: string, join: OrgJoin): Promise<void> {
        /* Through the raw request rather than a namespace method: the SDK
           generates `orgUpdate` but does not surface it on `organizations`,
           and this is the escape hatch it documents for exactly that. The
           errors come back through the same reader, so a refusal here says
           what it says everywhere else.

           The domain is sent only with the rule that uses it. Nulling it
           alongside the other two would throw away something somebody typed
           the moment they closed the door for a week. */
        await lemma().request("PATCH", `/organizations/${encodeURIComponent(orgId)}`, {
            body: join.policy === "domain"
                ? { join_policy: orgJoinWire(join.policy), email_domain: join.domain }
                : { join_policy: orgJoinWire(join.policy) },
        });
    },

    async setPodJoin(podId: string, policy: JoinPolicy): Promise<void> {
        /* Only this field goes up. The config is merged field-wise, so the
           runtime stored beside it survives — see `setPodRuntime`, which
           relies on the same merge. */
        await lemma(podId).pods.update(podId, { config: { join_policy: joinWire(policy) as JoinWire } });
        forgetPod(podId);
    },

    async myJoinRequest(podId: string): Promise<JoinRequest | null> {
        /* Asked without a pod scope. Every other call here is scoped to the
           pod it is about, which is right when you are inside one — but this
           is the call made by somebody who is not, and a scoped client is a
           client asking the pod for permission to ask about the pod. */
        const said = await lemma().podJoinRequests.me(podId);
        return readJoinRequest(said);
    },

    async askToJoin(podId: string): Promise<JoinRequest> {
        const said = await lemma().podJoinRequests.create(podId);
        const request = readJoinRequest(said);
        /* The answer decides whether the person is now in, so a response this
           app cannot read has to be an error rather than a shrug. Reading it
           as "pending" would leave somebody the door just opened for staring
           at a screen that says they are waiting. */
        if (!request) throw new Error("The ask went through, but the answer could not be read. Reload to see where you stand.");
        return request;
    },

    async listJoinRequests(podId: string): Promise<JoinRequest[]> {
        const listed = (await lemma(podId).podJoinRequests.list(podId, { status: "PENDING" as never, limit: 50 })) as Listish;
        return itemsOf(listed)
            .map(readJoinRequest)
            .filter((entry): entry is JoinRequest => entry !== null);
    },

    async admitToPod(podId: string, requestId: string): Promise<JoinRequest> {
        /* No roles named. The platform picks its own defaults for somebody
           admitted this way, and choosing here would mean this screen deciding
           what a new member may do — which is the roster's question, asked
           where the roster is, after they are in. */
        const said = await lemma(podId).podJoinRequests.approve(podId, requestId);
        const request = readJoinRequest(said);
        if (!request) throw new Error("They may be in; the answer could not be read. Reload the roster to check.");
        return request;
    },

    async listConnectors(): Promise<Connector[]> {
        const listed = (await lemma().connectors.list({ limit: 200 })) as Listish;
        return itemsOf(listed)
            .map(readConnector)
            .filter((entry): entry is Connector => entry !== null)
            .sort((a, b) => a.title.localeCompare(b.title));
    },

    async listAccounts(orgId: string): Promise<ConnectorAccount[]> {
        const listed = (await lemma().connectors.accounts.list(orgId, { limit: 200 })) as Listish;
        return itemsOf(listed)
            .map(readAccount)
            .filter((entry): entry is ConnectorAccount => entry !== null);
    },

    async disconnectAccount(orgId: string, accountId: string): Promise<void> {
        await lemma().connectors.accounts.delete(orgId, accountId);
    },

    async listMySurfaces(): Promise<{ platform: string; podId: string; name: string }[]> {
        const answer = (await lemma().userSurfaces.list()) as {
            groups?: { platform?: string; surfaces?: { pod_id?: string; name?: string }[] }[];
        };
        return (answer.groups ?? []).flatMap((group) =>
            (group.surfaces ?? []).map((surface) => ({
                platform: String(group.platform ?? ""),
                podId: String(surface.pod_id ?? ""),
                name: String(surface.name ?? ""),
            })),
        );
    },

    async slackManifest(agentName: string): Promise<Record<string, unknown>> {
        return (await lemma().podSurfaces.slackManifest(agentName)) as Record<string, unknown>;
    },

    async addCustomApp(
        orgId: string,
        connectorId: string,
        name: string,
        config: Record<string, unknown>,
        kind?: string,
    ): Promise<string> {
        /* `config_source` is what tells the backend these are the org's own
           credentials rather than Lemma's. Without it the SDK's own comment
           warns the OAuth runs against Lemma's app instead of theirs, which
           is exactly the bot-identity mix-up this flow exists to avoid. */
        const made = (await lemma().connectors.enableApp(orgId, connectorId, {
            name,
            config,
            config_source: "ORG_CUSTOM",
            kind,
        })) as { id?: string };
        return String(made.id ?? "");
    },

    async startAccount(
        orgId: string, connectorId: string, authConfigId?: string,
        connectionFields?: Record<string, unknown>,
    ): Promise<AccountConnect> {
        const client = lemma();
        /* Read what is already there first. The callback lands on the
           provider's side, not ours, so the only way to recognise the account
           this authorisation made is that it was not here a moment ago. */
        const existing = (await client.connectors.accounts.list(orgId, { connectorId, limit: 100 })) as {
            items?: { id?: string }[];
        };
        const before = (existing.items ?? []).map((account) => String(account.id ?? "")).filter(Boolean);
        const request = (await client.connectors.createConnectRequest(
            orgId,
            /* Naming the auth config when there is one: a freshly registered
               app is not the org's default, and connecting by connector id
               alone would authorise the wrong one. */
            authConfigId || connectionFields
                ? {
                    connector_id: connectorId,
                    ...(authConfigId ? { auth_config_id: authConfigId } : {}),
                    ...(connectionFields ? { connection_fields: connectionFields } : {}),
                }
                : connectorId,
        )) as { authorization_url?: string | null };
        return { authorizeUrl: request.authorization_url ?? "", before, authConfigId };
    },

    async findAccount(orgId: string, connectorId: string, before: string[], authConfigId?: string): Promise<string> {
        const listed = (await lemma().connectors.accounts.list(orgId, { connectorId, limit: 100 })) as Listish;
        const accounts = itemsOf(listed)
            .map(readAccount)
            .filter((entry): entry is ConnectorAccount => entry !== null);

        /* Prefer the account this authorisation just made — but do not require
           one. Re-authorising a connector the org already has can refresh the
           existing row rather than insert a new one, and the first version of
           this waited forever for an id that was never going to appear. */
        const seen = new Set(before);
        const eligible = accounts.filter(account => !authConfigId || account.authConfigId === authConfigId);
        const fresh = eligible.find((account) => !seen.has(account.id) && account.usable);
        if (fresh) return fresh.id;
        return bestAccount(eligible, connectorId)?.id ?? "";
    },

    async connectAccount(podId: string, platform: string, accountId: string): Promise<Surface> {
        const payload = { platform, credential_mode: "CUSTOM", account_id: accountId } as unknown as Parameters<
            ReturnType<typeof lemma>["podSurfaces"]["create"]
        >[1];
        return asSurface(await lemma(podId).podSurfaces.create(podId, payload), platform);
    },

    async startGuided(podId: string, platform: string): Promise<GuidedSetup> {
        if (platform.toUpperCase() !== "TELEGRAM") {
            throw new Error("Only Telegram has a guided setup today.");
        }
        return asGuided(await lemma(podId).podSurfaces.startTelegramBotSetup(podId, {}));
    },

    async checkGuided(podId: string, setupId: string): Promise<GuidedSetup> {
        return asGuided(await lemma(podId).podSurfaces.getTelegramBotSetup(podId, setupId));
    },

    async disconnect(podId: string, surfaceName: string): Promise<void> {
        await lemma(podId).podSurfaces.delete(podId, surfaceName);
    },

    async downloadFile(podId, path) { return lemma(podId).files.download(path); },

    async readFile(podId: string, path: string): Promise<FileContent> {
        const client = lemma(podId);

        /* The deep link is fetched alongside the metadata rather than only on
           the fallback path: every file gets an "open" affordance, drawn
           inline or not. */
        const [detail, urls] = await Promise.all([
            client.files.get(path) as Promise<{
                name?: string;
                path?: string;
                mime_type?: string | null;
                size_bytes?: number;
            }>,
(client.files.getUrl(path) as Promise<{ app_url?: string; url?: string }>).catch(
                () => ({}) as { app_url?: string; url?: string },
            ),
        ]);

        const mime = detail.mime_type ?? "";
        const size = detail.size_bytes ?? 0;
        const kind = fileKind(mime, path);
        const base: FileContent = {
            name: detail.name ?? path.split("/").pop() ?? path,
            path: detail.path ?? path,
            mime,
            size,
            kind,
            appUrl: urls.app_url,
            rawUrl: urls.url,
        };

        /* Images and PDFs are never pulled into the page. The signed URL
           serves the bytes with no Authorization header — and CORS does not
           apply to what `img` and `iframe` display — so the browser fetches
           them the way it fetches any other image. Downloading a blob would
           also mean owning its lifetime, which is the bug this replaced: an
           object URL revoked by a component while the react-query result
           holding it stayed cached, so the second render drew a dead URL. */
        if (kind === "image" || kind === "video" || kind === "audio" || kind === "pdf") return base;

        if (kind === "markdown" || kind === "text" || kind === "html") {
            /* The bytes or nothing.
             *
             *  There is no framing the signed URL as a second chance: it serves
             *  with attachment headers, so a frame pointed at it downloads
             *  instead of rendering and never fires a load event — which is
             *  exactly why `PdfPreview` fetches and makes its own object URL
             *  rather than using `rawUrl`. Offering it here bought a page stuck
             *  on "Loading…" forever in place of a card that at least said
             *  what had happened. */
            if (size > (kind === "html" ? INLINE_HTML_LIMIT : INLINE_TEXT_LIMIT)) {
                return { ...base, kind: "binary", note: "Too big to show here · " + readableSize(size) };
            }
            try {
                const blob = await client.files.download(path);
                return { ...base, text: await blob.text() };
            } catch {
                /* Unreadable from here: the card and its link still stand, and
                   now they say which of the two things went wrong. */
                return { ...base, kind: "binary", note: "This file could not be read" };
            }
        }

        return { ...base, kind: "binary" };
    },

    async writeFile(podId: string, path: string, text: string): Promise<void> {
        const name = path.split("/").filter(Boolean).pop() ?? path;
        /* The mime is derived from the name rather than carried over from the
           read. A file the pod stored as `application/octet-stream` — which is
           what an upload with no extension gets — would otherwise be written
           back as one, and the thing that reads it next would stop seeing
           markdown. */
        await lemma(podId).files.update(path, {
            file: new Blob([text], { type: /\.(md|markdown)$/i.test(name) ? "text/markdown" : "text/plain" }),
            name,
        });
    },

    async shareFile(podId: string, path: string, options?: { expiresSeconds?: number; maxHits?: number }): Promise<SharedLink> {
        const minted = (await lemma(podId).files.createSignedUrl(path, options)) as {
            signed_url?: string;
            expires_at?: string;
            max_hits?: number;
        };
        const rawUrl = minted.signed_url ?? "";
        /* The code is the last segment of `{api}/s/{code}`. Taken from the URL
           the server returned rather than minted here, so a change of shape on
           that side cannot leave this one confidently wrong. */
        const code = rawUrl.split("/").filter(Boolean).pop() ?? "";
        return {
            rawUrl,
            readUrl: typeof window === "undefined" ? "/d/" + code : window.location.origin + "/d/" + code,
            code,
            expiresAt: minted.expires_at ?? "",
            maxHits: minted.max_hits ?? 0,
        };
    },

    async widgetEmbedUrl(podId: string, conversationId: string, toolCallId: string): Promise<string> {
        const minted = await lemma(podId).widgets.embedUrl({
            conversation_id: conversationId,
            tool_call_id: toolCallId,
        });
        return minted.url;
    },

    async listConversations(podId: string): Promise<ConversationRef[]> {
        const listed = await lemma(podId).conversations.listDefault({ pod_id: podId, limit: 25 });
        return (listed.items ?? []).map((c) => {
            const row = c as { id: string; title?: string | null; type?: string; updated_at?: string; metadata?: Record<string, unknown> | null };
            const bound = row.metadata?.[RESOURCE_KEY];
            return {
                id: row.id,
                title: (row.title ?? "").trim() || "Untitled",
                at: dayOf(row.updated_at) === "Today" ? clock(row.updated_at) : dayOf(row.updated_at),
                kind: row.type ?? "CHAT",
                boundTo: typeof bound === "string" ? bound : null,
            };
        });
    },

    async listCallThreads(podId: string, parentId: string): Promise<ConversationRef[]> {
        /* `list` rather than `listDefault`: the latter is `list` with
           `agent_name: POD_DEFAULT` bolted on and no way to pass a parent. */
        const listed = await lemma(podId).conversations.list({ pod_id: podId, parent_id: parentId, limit: 10 });
        return (listed.items ?? []).map((c) => {
            const row = c as { id: string; title?: string | null; type?: string; updated_at?: string };
            return {
                id: row.id,
                title: (row.title ?? "").trim() || "Call",
                at: dayOf(row.updated_at) === "Today" ? clock(row.updated_at) : dayOf(row.updated_at),
                kind: row.type ?? "CHAT",
            };
        });
    },

    async createPod(orgId: string, name: string, description?: string): Promise<Pod> {
        const pod = (await lemma().pods.create({
            name,
            organization_id: orgId,
            ...(description?.trim() ? { description: description.trim() } : {}),
        })) as {
            id: string;
            name: string;
            icon_url?: string | null;
        };
        return {
            id: pod.id,
            orgId,
            name: humanizeName(pod.name),
            iconUrl: pod.icon_url ?? null,
            teammate: await teammateOf(pod.id, humanizeName(pod.name), pod.icon_url ?? null),
            subtitle: "just you",
            members: [],
            waiting: "",
        };
    },

    async setPodIcon(podId: string, iconUrl: string | null): Promise<void> {
        await lemma(podId).pods.update(podId, { icon_url: iconUrl });
        forgetPod(podId);
    },

    async renamePod(podId: string, name: string): Promise<void> {
        await lemma(podId).pods.update(podId, { name });
        forgetPod(podId);
    },

    async uploadIcon(file: File): Promise<string> {
        const uploaded = (await lemma().icons.upload(file)) as { url?: string; icon_url?: string; path?: string };
        const url = uploaded.url ?? uploaded.icon_url ?? uploaded.path;
        if (!url) throw new Error("The upload came back without a URL.");
        return url;
    },

    /** Raw messages, in order. Turning them into turns is the transcript's
     *  job, and the live pane gets them through `useAssistantSession`; this
     *  stays for the places that want a plain read. */
    async getConversation(podId: string, _teammate?: Persona, conversationId?: string | null): Promise<Conversation> {
        const client = lemma(podId);
        if (conversationId === NEW_CONVERSATION) {
            return { id: null, title: "", status: null, messages: [] };
        }
        const listed = await client.conversations.listDefault({ pod_id: podId, limit: 25 });
        const head = conversationId
            ? (listed.items ?? []).find((c) => c.id === conversationId)
            : ((listed.items ?? []).find((c) => c.type === "CHAT") ?? listed.items?.[0]);
        if (!head) return { id: null, title: "", status: null, messages: [] };

        const page = await client.conversations.messages.list(head.id, { pod_id: podId, limit: 100 });
        return {
            id: head.id,
            title: head.title ?? "",
            status: (head as { status?: string | null }).status ?? null,
            messages: (page.items ?? []) as unknown as Message[],
        };
    },

    async getProfile(podId: string): Promise<Profile> {
        const client = lemma(podId);

        /* Six reads, all independent, so they go together. The profile is a
           tab you open deliberately — it may cost more than the sidebar, but
           it may not cost six round trips in a row. */
        const [pod, agents, schedules, apps, tables, functions, workflows] = await Promise.all([
            client.pods.get(podId),
            podAgents(podId).catch(() => null),
            (client.schedules.list({ limit: 12 }) as Promise<Listish>).catch(() => null),
            (client.apps.list({ limit: 12 }) as Promise<Listish>).catch(() => null),
            (client.tables.list({ limit: 50 }) as Promise<Listish>).catch(() => null),
            (client.functions.list({ limit: 50 }) as Promise<Listish>).catch(() => null),
            (client.workflows.list({ limit: 50 }) as Promise<Listish>).catch(() => null),
        ]);

        const unavailable: NonNullable<Profile["unavailable"]> = [];
        if (agents === null) unavailable.push("tools");
        if (schedules === null) unavailable.push("schedules");
        if (apps === null) unavailable.push("apps");
        if (tables === null) unavailable.push("tables");
        if (functions === null) unavailable.push("functions");
        if (workflows === null) unavailable.push("workflows");
        const podName = humanizeName(pod?.name ?? podId);

        /* The default agent is the one that answers here, and it is the one
           whose instruction is this teammate's own account of itself. Picking
           `agents[0]` names a subagent instead — that mistake is what put
           "success" and "deepak" in the header once. */
        const defaultAgent = itemsOf(agents ?? [])
            .map((raw) => raw as { name?: string; kind?: string })
            .find((agent) => agent.name && isPodDefaultAgent(agent.name, agent.kind));

        let about = pod?.description ?? "";
        let skills: Skill[] = [];
        let permits: string[] = [];
        if (defaultAgent?.name) {
            try {
                const detail = await client.agents.get(defaultAgent.name);
                about = detail.instruction?.trim() || about;
                /* Not `detail.toolsets` on its own. For `pod_default` that
                   array comes back empty on the wire, because the default
                   agent's toolset is assigned at run time rather than stored
                   on its row — so reading it literally had this page telling
                   people the teammate they are talking to can "talk, and that
                   is all", about an agent with a shell, a browser, the pod's
                   files and tables, web search, voice, sub-agents and memory.
                   `grantedToolsets` knows which empty means which. */
                skills = grantedToolsets(
                    detail.toolsets,
                    isPodDefaultAgent(defaultAgent.name, defaultAgent.kind),
                ).map(toSkill).filter(Boolean) as Skill[];
                permits = (detail.allowed_actions ?? []).slice(0, 24);
            } catch {
                unavailable.push("tools");
            }
        }

        if (!defaultAgent && !unavailable.includes("tools")) unavailable.push("tools");

        /* Through the same reader the section itself uses, rather than a
           second hand-rolled pass over the same payload. Two passes disagree,
           and the ways they disagree are not obvious: one counts `is_internal`
           rows out while drawing a schedule wired to a deleted agent as though
           it were fine, and reads the cadence off `interval_seconds` and
           `run_at`, fields no `TimeScheduleConfig` carries. The header's count
           of standing jobs and the rows under it come off one read. */
        const commitments: Commitment[] = readSchedules(schedules ?? []).map((job) => ({
            id: job.id,
            title: job.title,
            detail: job.instruction || job.filter,
            cadence: job.trigger,
            since: job.since,
            active: job.active,
            last: dayOf(job.lastFiredAt),
        }));

        const projects: Project[] = itemsOf(apps ?? [])
            .map((raw) => raw as { id?: string; name?: string; description?: string | null; status?: string })
            .filter((app) => Boolean(app.name))
            .map((app) => ({
                id: app.id ?? (app.name as string),
                name: humanizeName(app.name as string),
                description: app.description ?? "",
                status: (app.status ?? "").toLowerCase(),
                tabId: "app:" + app.name,
            }));

        return {
            podId: podId,
            name: podName,
            iconUrl: pod?.icon_url ?? null,
            headline: (pod?.description ?? "").trim(),
            joined: pod?.created_at ?? "",
            about: about.trim(),
            skills,
            permits,
            commitments,
            projects,
            unavailable,
            counts: {
                tables: itemsOf(tables ?? []).length,
                functions: itemsOf(functions ?? []).length,
                workflows: itemsOf(workflows ?? []).length,
            },
        };
    },

    /* `include=permissions` is deliberately not asked for. It is one extra
       query for the whole page rather than one per row, but the list draws no
       grants — they are a detail, and the detail fetch brings them anyway. */
    async listAgents(podId: string): Promise<AgentRow[]> {
        return agentRows(await podAgents(podId));
    },

    async getAgent(podId: string, name: string): Promise<AgentDetail> {
        return readAgentDetail(await lemma(podId).agents.get(name));
    },

    async updateAgent(podId: string, name: string, before: AgentDraft, after: AgentDraft): Promise<AgentDetail> {
        const patch = agentChanges(before, after);
        /* The PATCH answers `AgentActionResponse`, which has no `permissions`
           block — only `agent.get` carries one. So the saved agent is re-read
           rather than taken from the reply, which would blank the grants the
           detail is already showing. */
        await lemma(podId).agents.update(name, patch);
        return liveSource.getAgent(podId, name);
    },

    async deleteAgent(podId: string, name: string): Promise<void> {
        await lemma(podId).agents.delete(name);
    },

    /* ── standing work ──────────────────────────────────────────────
       The list, the create and the update are in the SDK's `schedules`
       namespace. The two run routes are not — the namespace stops at
       list/create/get/update/delete — so those are written out here against
       `request`, the same arrangement the usage and computer calls are in. */

    async listSchedules(podId: string): Promise<StandingJob[]> {
        /* A hundred, where the profile's own summary asks for twelve. This is
           the list itself rather than a count beside a name, and a pod that
           keeps thirty schedules would otherwise silently show eighteen. */
        return readSchedules(await lemma(podId).schedules.list({ limit: 100 }));
    },

    async listScheduleRuns(podId: string, scheduleId: string): Promise<ScheduleRun[]> {
        /* Newest first is the server's order (`list_for_schedule` orders by
           `created_at DESC`), so nothing here sorts. Twenty is a screenful of
           history; the route's own ceiling is a thousand. */
        return readRuns(await lemma(podId).request(
            "GET",
            `/pods/${podId}/schedules/${scheduleId}/runs`,
            { params: { limit: 20 } },
        ));
    },

    async setScheduleActive(podId: string, scheduleId: string, active: boolean): Promise<StandingJob> {
        /* The PATCH answers the saved schedule, and it is worth reading rather
           than assuming: resuming one the breaker stopped clears the failure
           count server-side (`is_explicit_reactivation`), so the row that comes
           back says "0 failures" where the row that went in said five. */
        return readSchedule(await lemma(podId).schedules.update(scheduleId, { is_active: active }));
    },

    async retryScheduleRun(podId: string, scheduleId: string, runId: string): Promise<ScheduleRun> {
        /* 202, and the body is the *new* run — a redrive carrying the original
           event, not the old row flipped back to RECEIVED. Asking twice is
           safe: `create_redrive` hands back the redrive that already exists
           rather than making a second one. */
        return readRun(await lemma(podId).request(
            "POST",
            `/pods/${podId}/schedules/${scheduleId}/runs/${runId}/retry`,
        ));
    },

    async createSchedule(podId: string, draft: ScheduleDraft): Promise<StandingJob> {
        const made = await lemma(podId).request(
            "POST",
            `/pods/${podId}/schedules`,
            { body: createRequest(draft) },
        );
        return readSchedule(made);
    },

    async scheduleTargets(podId: string): Promise<TargetChoice[]> {
        /* The agents come from the same cached page the rest of this file
           uses, so opening the form on a profile that has already drawn its
           agents costs one request rather than two. */
        const [agents, workflows] = await Promise.all([
            podAgents(podId).catch(() => [] as unknown[]),
            (lemma(podId).workflows.list({ limit: 100 }) as Promise<Listish>).catch(() => [] as unknown[]),
        ]);
        const fromAgents: TargetChoice[] = itemsOf(agents)
            .map((raw) => raw as { name?: string; kind?: string })
            .filter((agent) => Boolean(agent.name))
            .map((agent) => ({
                kind: "agent" as const,
                name: agent.name as string,
                label: displayAgentName(agent.name as string, agent.kind),
            }));
        const fromWorkflows: TargetChoice[] = itemsOf(workflows)
            .map((raw) => raw as { name?: string; is_active?: boolean })
            /* A retired workflow is still listed. Pointing a new schedule at
               one would make a job that fires and does nothing. */
            .filter((flow) => Boolean(flow.name) && flow.is_active !== false)
            .map((flow) => ({
                kind: "workflow" as const,
                name: flow.name as string,
                label: humanizeName(flow.name as string),
            }));
        return [...fromAgents, ...fromWorkflows];
    },

    async send(podId: string, text: string, teammate?: Persona, into?: string | null): Promise<Conversation> {
        const client = lemma(podId);
        let target = into === NEW_CONVERSATION ? null : (into ?? null);
        if (into === NEW_CONVERSATION) {
            target = (await client.conversations.create({ pod_id: podId })).id;
        }
        if (!target) {
            const listed = await client.conversations.listDefault({ pod_id: podId, limit: 25 });
            const head = (listed.items ?? []).find((c) => c.type === "CHAT") ?? listed.items?.[0];
            target = head ? head.id : (await client.conversations.create({ pod_id: podId })).id;
        }
        const conversationId = target;

        const stream = await client.conversations.sendMessageStream(
            conversationId,
            { content: text },
            { pod_id: podId },
        );
        // Drain the stream so the turn actually runs, then re-read the
        // conversation. Rendering tokens as they arrive is the next step;
        // this already sends for real.
        const reader = (stream as ReadableStream<Uint8Array>).getReader();
        try {
            for (;;) {
                const { done } = await reader.read();
                if (done) break;
            }
        } finally {
            reader.releaseLock();
        }
        return liveSource.getConversation(podId, teammate, conversationId);
    },
};
