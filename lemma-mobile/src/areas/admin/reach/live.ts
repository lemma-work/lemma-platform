import type { SurfaceCreateRequest } from "lemma-sdk";
import { PodAccountVisibility } from "lemma-sdk";
import { readPodAccounts, shareAsBody } from "@web/data/pod-accounts";
import { freshSlug, readPodConnectors } from "@web/data/pod-connectors";
import type { LiveContext } from "../../context";
import { findAccount, listAccounts, startAccount } from "../connect/accounts";
import { authorize } from "../connect/round-trip";
import type { Answerer, BotGroup, GuidedSetup, MySurface, Surface } from "./types";
import type { ReachSource } from "./source";
import { isPodDefaultAgent, displayAgentName } from "@web/data/agent-names";
import { byEffort, readConnectable, type Connectable } from "@web/data/connectable";
import { readSourceSettings, type SourceSettings } from "@web/channels/sources";
import { PHONE_RETURN } from "../connect/outcome";


type Raw = Record<string, unknown>;

function rows(value: unknown): Raw[] {
    const list = Array.isArray(value) ? value : (value as { items?: unknown[] } | null)?.items ?? [];
    return list.filter((one): one is Raw => Boolean(one) && typeof one === "object");
}

function words(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** "customer-support" → "Customer support", for a Lemma's own name. */
function readable(name: string): string {
    const spaced = name.replace(/[-_]+/g, " ").trim();
    return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : name;
}

/** A place, the way the web reads one (`listSurfaces`, `asSurface`). */
function readSurface(raw: unknown, podName: string | null, fallbackPlatform = ""): Surface | null {
    const row = (raw ?? {}) as Raw;
    const id = words(row.id);
    const platform = words(row.platform) || fallbackPlatform;
    if (!id || !platform) return null;
    const agent = typeof row.agent_name === "string" ? row.agent_name : null;
    const mine = row.uses_default_agent === true || isPodDefaultAgent(agent);
    const reach = (row.reach ?? null) as { handle?: string | null; email?: string | null } | null;
    const status = words(row.status) || "ACTIVE";
    return {
        id,
        platform,
        name: words(row.name) || platform.toLowerCase(),
        mine,
        agentName: mine ? podName ?? "" : displayAgentName(agent ?? ""),
        agentKey: agent ?? undefined,
        handle: reach?.handle ?? (row.surface_identity_username as string | null | undefined) ?? (row.surface_identity_email as string | null | undefined) ?? "",
        email: reach?.email ?? (row.surface_identity_email as string | null | undefined) ?? undefined,
        active: status === "ACTIVE",
        status,
    };
}

function readGuided(raw: unknown): GuidedSetup {
    const answer = (raw ?? {}) as Raw;
    return {
        setupId: words(answer.setup_id),
        launchUrl: words(answer.launch_url),
        managerBot: words(answer.manager_bot_username),
        status: words(answer.status),
        botUsername: words(answer.bot_username) || undefined,
        botLaunchUrl: words(answer.bot_launch_url) || undefined,
        expiresAt: words(answer.expires_at),
        error: words(answer.error) || undefined,
    };
}

function settings(raw: unknown): SourceSettings {
    const read = readSourceSettings(raw);
    if (!read) throw new Error("That group came back unreadable.");
    return read;
}

/** The same calls the web makes (`data/live.ts`: `listSurfaces`,
 *  `listConnectable`, `connectSystem`, `connectAccount`, `startGuided`, …;
 *  `channels/api.ts` for a place's groups). */
export function reachLive(context: LiveContext): ReachSource {
    const pod = (podId: string) => context.client(podId);
    const org = () => context.client();
    const podName = async (podId: string): Promise<string | null> => {
        try {
            const row = (await pod(podId).pods.get(podId)) as { name?: string | null };
            return row.name ? readable(row.name) : null;
        } catch {
            return null;
        }
    };
    const create = async (podId: string, payload: Raw, platform: string): Promise<Surface> => {
        const made = await pod(podId).podSurfaces.create(podId, payload as unknown as SurfaceCreateRequest);
        const read = readSurface({ ...(made as unknown as Raw), uses_default_agent: true }, null, platform);
        if (!read) throw new Error("It connected, but came back unreadable. Look again.");
        return read;
    };
    return {
        async places(podId) {
            const [listed, name] = await Promise.all([pod(podId).podSurfaces.list(podId, { limit: 30 }), podName(podId)]);
            return rows(listed).map((raw) => readSurface(raw, name)).filter((one): one is Surface => one !== null);
        },
        async catalog(podId) {
            const answer = (await pod(podId).podSurfaces.available(podId)) as { surfaces?: unknown[] };
            return (answer.surfaces ?? []).map(readConnectable).filter((entry): entry is Connectable => entry !== null).sort(byEffort);
        },
        async accounts(orgId) {
            return listAccounts(org(), orgId);
        },
        async mine() {
            const answer = (await org().userSurfaces.list()) as { groups?: { platform?: string; surfaces?: { pod_id?: string; name?: string }[] }[] };
            return (answer.groups ?? []).flatMap((group) => (group.surfaces ?? []).map((surface): MySurface => ({
                platform: String(group.platform ?? ""),
                podId: String(surface.pod_id ?? ""),
                name: String(surface.name ?? ""),
            })));
        },
        async connectSystem(podId, platform) {
            return create(podId, { platform, credential_mode: "SYSTEM" }, platform);
        },
        async podConnectors(podId) {
            return readPodConnectors(await pod(podId).connectors.pod.list());
        },
        async connectPodConnector(podId, platform, podConnector) {
            return create(podId, { platform, credential_mode: "CUSTOM", pod_connector: podConnector }, platform);
        },
        async connectOwnAccount(podId, platform, accountId) {
            /* The web's `shareOwnAccount`: the account into the Lemma, then one
               of its SHARED connectors running as it, reused when one does. */
            const connectors = pod(podId).connectors.pod;
            const held = readPodAccounts(await connectors.accounts.list()).find((one) => one.accountId === accountId);
            const account = held ?? readPodAccounts({ items: [await connectors.accounts.add({ account_id: accountId, visibility: PodAccountVisibility.POD })] })[0];
            if (!account) throw new Error("The account could not be shared with this Lemma.");
            const existing = readPodConnectors(await connectors.list());
            const running = existing.find((one) => one.mode === "SHARED" && one.sharedPodAccountId === account.id);
            const shareAs = async (slug: string, purpose: string) => {
                const path = "/" + ["pods", podId, "connector-accounts", account.id, "share-as"].map(encodeURIComponent).join("/");
                const made = await pod(podId).request("POST", path, { body: shareAsBody(slug, purpose) });
                return readPodConnectors({ items: [made] })[0]?.slug ?? slug;
            };
            const named = account.connectorId || "account";
            const slug = running && account.visibility !== "PERSONAL" ? running.slug
                : running ? await shareAs(running.slug, running.purpose)
                : await shareAs(freshSlug(named, existing.map((one) => one.slug)), "Where this Lemma answers, as " + (account.label || named));
            return create(podId, { platform, credential_mode: "CUSTOM", pod_connector: slug }, platform);
        },
        async disconnect(podId, name) {
            await pod(podId).podSurfaces.delete(podId, name);
        },

        async detail(podId, name) {
            return pod(podId).podSurfaces.get(podId, name);
        },
        async setup(podId, name) {
            return pod(podId).podSurfaces.setup(podId, name);
        },
        async options(podId, name) {
            const answer = (await pod(podId).podSurfaces.channels(podId, name)) as { channels?: { id?: string; name?: string | null; is_member?: boolean | null }[] };
            return (answer.channels ?? []).filter((one) => one.id).map((one) => ({ id: String(one.id), name: one.name ?? null, isMember: one.is_member ?? null }));
        },
        async answerers(podId) {
            const [listed, name] = await Promise.all([pod(podId).agents.list({ limit: 100 }), podName(podId)]);
            return rows(listed).map((raw): Answerer | null => {
                const agent = words(raw.name);
                if (!agent) return null;
                const front = isPodDefaultAgent(agent, words(raw.kind) || null);
                return { name: agent, label: front && name ? name : displayAgentName(agent, words(raw.kind) || null), front };
            }).filter((one): one is Answerer => one !== null)
                .sort((a, b) => Number(b.front) - Number(a.front) || a.label.localeCompare(b.label));
        },
        async update(podId, name, patch) {
            await pod(podId).podSurfaces.update(podId, name, patch);
        },

        async groups(podId, name) {
            const listed = await pod(podId).channels.sources.list(podId, name, { limit: 100 });
            return rows(listed).flatMap((row): BotGroup[] => {
                const read = readSourceSettings(row.source_settings);
                return read ? [{ ...read, joined: row.joined !== false }] : [];
            });
        },
        async joinGroup(podId, channelId) {
            await pod(podId).channels.join(podId, channelId);
        },
        async group(podId, channelId, sourceId) {
            return settings(await pod(podId).channels.sources.get(podId, channelId, sourceId));
        },
        async startWhatsAppGroup(podId, body) {
            return settings(await pod(podId).channels.sources.startWhatsAppGroup(podId, {
                surface_name: body.surfaceName,
                title: body.title,
                answers_outsiders: body.answersOutsiders,
            }));
        },
        async telegramLink(podId, name) {
            const link = await pod(podId).channels.sources.telegramLink(podId, name);
            return { url: link.url, expiresAt: link.expires_at };
        },

        async startAccount(orgId, connectorId, options = {}) {
            return startAccount(org(), orgId, connectorId, { ...options, returnTo: PHONE_RETURN });
        },
        async signIn(url) {
            return authorize(url);
        },
        async findAccount(orgId, connectorId, before, authConfigId) {
            return findAccount(org(), orgId, connectorId, before, authConfigId);
        },
        async createAccount(orgId, entry, credentials) {
            const install = await org().connectors.enableApp(orgId, entry.connectorId, { kind: entry.kind });
            const account = await org().connectors.accounts.create(orgId, { auth_config_id: install.id, credentials } as never);
            return String((account as { id?: unknown }).id ?? "");
        },
        async removeAccount(orgId, accountId) {
            await org().connectors.accounts.delete(orgId, accountId);
        },

        async startGuided(podId, platform) {
            if (platform.toUpperCase() !== "TELEGRAM") throw new Error("Only Telegram has a guided setup today.");
            return readGuided(await pod(podId).podSurfaces.startTelegramBotSetup(podId, {}));
        },
        async checkGuided(podId, setupId) {
            return readGuided(await pod(podId).podSurfaces.getTelegramBotSetup(podId, setupId));
        },

        async slackManaged(podId) {
            const listed = await org().podSurfaces.slackManagedWorkspaces(podId);
            return (listed.items ?? []).map((item) => ({ teamId: item.team_id, teamName: item.team_name ?? null }));
        },
        async bindSlackManager(podId, accountId) {
            const bound = await org().podSurfaces.bindSlackManager(podId, { account_id: accountId });
            return { teamId: bound.team_id, teamName: bound.team_name ?? null };
        },
        async slackAppWorkspaces(podId) {
            const listed = await org().podSurfaces.slackAppWorkspaces(podId);
            return {
                canManage: listed.can_manage,
                workspaces: (listed.items ?? []).map((item) => ({ teamId: item.team_id, teamName: item.team_name ?? null, needsNewToken: Boolean(item.needs_new_token) })),
            };
        },
        async saveSlackToken(podId, refreshToken) {
            const saved = await org().podSurfaces.saveSlackConfigurationToken(podId, { refresh_token: refreshToken.trim() });
            return { teamId: saved.team_id, teamName: saved.team_name ?? null, needsNewToken: Boolean(saved.needs_new_token) };
        },
        async createSlackApp(podId, teamId, key) {
            const made = await org().podSurfaces.createSlackApp(podId, { team_id: teamId }, key);
            return { appId: made.app_id, authConfigId: String(made.auth_config_id) };
        },
        async slackManifest(name) {
            return (await org().podSurfaces.slackManifest(name)) as Record<string, unknown>;
        },
        async addOwnApp(orgId, connectorId, name, config, kind) {
            /* `ORG_CUSTOM` is what makes the sign-in run against this app
               rather than Lemma's own. */
            const made = (await org().connectors.enableApp(orgId, connectorId, { name, config, config_source: "ORG_CUSTOM", kind } as never)) as { id?: string };
            return String(made.id ?? "");
        },
    };
}
